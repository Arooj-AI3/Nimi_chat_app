import base64
import json

import httpx
import pytest
from PySide6.QtGui import QColor, QImage

from api import openrouter_client
from api.errors import ProviderError, friendly_error
from api.file_handler import attachments_to_records, extract_file
from api.groq_client import ThinkStripper, strip_think
from api.intent import detect_image_intent
from api.messages import build_api_messages


# ---------------------------------------------------------------- intent
@pytest.mark.parametrize(
    "text",
    [
        "generate an image of a red fox",
        "draw a logo for my cafe",
        "Create a poster for the science fair",
        "make a picture of a mountain at sunrise",
        "/image sunset over Multan",
        "ایک تصویر بناؤ پہاڑوں کی",
        "genera una imagen de un gato",
        "draw a green square",
        "Can you sketch me a cat?",
        "dessine un logo pour mon site",
    ],
)
def test_image_intent_positive(text):
    assert detect_image_intent(text)[0] is True


@pytest.mark.parametrize(
    "text",
    [
        "what is in this image?",
        "describe this photo",
        "explain how photosynthesis works",
        "summarize the attached pdf",
        "write an image caption for instagram",
        "/chat draw me a cat",
        "how do I draw a conclusion from this data?",
        "",
    ],
)
def test_image_intent_negative(text):
    assert detect_image_intent(text)[0] is False


def test_image_prefix_is_stripped():
    assert detect_image_intent("/image a red fox") == (True, "a red fox")


# ---------------------------------------------------------------- think stripper
def test_think_stripper_across_chunks():
    s = ThinkStripper()
    out = "".join(s.feed(c) for c in ["Hel", "lo <thi", "nk>secret ", "stuff</th", "ink>\nWorld"]) + s.flush()
    assert out == "Hello World"


def test_strip_think_plain_text_untouched():
    assert strip_think("no tags <here> at all") == "no tags <here> at all"


# ---------------------------------------------------------------- errors
def test_error_uses_status_not_substrings():
    # A message merely containing "401" or "model" must NOT be reported as an auth error.
    exc = ProviderError(400, "Requested 4010 tokens for model x")
    assert "rejected the request (400)" in friendly_error(exc)


def test_rate_limit_scope_detected():
    exc = ProviderError(429, "Rate limit reached on tokens per minute (TPM): Limit 6000")
    assert "per-minute limit" in friendly_error(exc)


def test_missing_model_message():
    assert "not found" in friendly_error(ProviderError(404, "model_not_found"))


# ---------------------------------------------------------------- files
def _png(path, w=40, h=30):
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor("red"))
    assert img.save(str(path))
    return str(path)


def test_image_attachment_is_stored_and_downscaled(tmp_path):
    big = tmp_path / "big.png"
    img = QImage(4000, 2000, QImage.Format_RGB32)
    img.fill(QColor("blue"))
    img.save(str(big))
    a = extract_file(str(big))
    assert a.is_valid and a.is_image
    stored = QImage(a.image_path)
    assert max(stored.width(), stored.height()) <= 1536


def test_corrupt_image_reports_error(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    a = extract_file(str(bad))
    assert not a.is_valid and "image" in a.error.lower()


def test_pdf_text_and_scanned_detection(tmp_path):
    from reportlab.pdfgen import canvas

    good = tmp_path / "spec.pdf"
    c = canvas.Canvas(str(good))
    c.drawString(72, 720, "Poster for a robotics fair, blue and orange, title Robo Expo 2026")
    c.save()
    a = extract_file(str(good))
    assert a.is_valid and "Robo Expo" in a.content

    blank = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(blank))
    c.showPage()
    c.save()
    b = extract_file(str(blank))
    assert not b.is_valid and "scanned" in b.error.lower()


def test_long_document_is_truncated(tmp_path):
    f = tmp_path / "long.txt"
    f.write_text("word " * 50000, encoding="utf-8")
    a = extract_file(str(f))
    assert a.is_valid and a.truncated and len(a.content) < 21000


# ---------------------------------------------------------------- message building
def test_error_messages_never_sent_and_docs_only_on_latest():
    msgs = [
        {"role": "user", "content": "read", "attachments": [{"kind": "document", "name": "a.pdf", "text": "AAA"}]},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "again", "attachments": [{"kind": "document", "name": "b.pdf", "text": "BBB"}]},
        {"role": "assistant", "kind": "error", "content": "⚠ boom"},
        {"role": "user", "content": "and now?"},
    ]
    api, has_images = build_api_messages(msgs)
    blob = json.dumps(api)
    assert "boom" not in blob
    assert "BBB" in blob and "AAA" not in blob  # older doc replaced by a placeholder
    assert not has_images


def test_images_sent_as_parts_and_history_budget(tmp_path, monkeypatch):
    p = _png(tmp_path / "x.png")
    msgs = [{"role": "user", "content": "what is this?", "attachments": [{"kind": "image", "name": "x.png", "image": p}]}]
    api, has_images = build_api_messages(msgs)
    assert has_images
    parts = api[-1]["content"]
    assert parts[0]["type"] == "text" and parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")

    monkeypatch.setattr("api.messages.MAX_CONTEXT_CHARS", 1000)
    long = [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 400} for i in range(10)]
    long.append({"role": "user", "content": "latest"})
    api, _ = build_api_messages(long)
    assert api[-1]["content"] == "latest"          # newest never dropped
    assert len(api) < 12                            # oldest were trimmed


# ---------------------------------------------------------------- OpenRouter client
_REAL_CLIENT = httpx.Client


def _client_factory(handler):
    real = _REAL_CLIENT  # captured once: monkeypatching must not chain factories

    def factory(*a, **k):
        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)

    return factory


def test_openrouter_success_and_request_shape(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"IMG").decode(), "media_type": "image/png"}]})

    monkeypatch.setattr(openrouter_client.httpx, "Client", _client_factory(handler))
    out = openrouter_client.generate_images("k", "https://openrouter.ai/api/v1", "inclusionai/ming-image-0.1-design", "a fox")
    assert out == [(b"IMG", "image/png")]
    assert seen["url"] == "https://openrouter.ai/api/v1/images"
    assert seen["body"] == {"model": "inclusionai/ming-image-0.1-design", "prompt": "a fox", "n": 1}
    assert seen["auth"] == "Bearer k"


def test_openrouter_reference_image_and_errors(monkeypatch):
    def ok(request):
        body = json.loads(request.content)
        assert body["input_references"][0]["image_url"]["url"].startswith("data:")
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"A").decode(), "media_type": "image/webp"}]})

    monkeypatch.setattr(openrouter_client.httpx, "Client", _client_factory(ok))
    assert openrouter_client.generate_images("k", "https://x/v1", "m", "p", reference_data_url="data:image/jpeg;base64,AA")

    monkeypatch.setattr(
        openrouter_client.httpx, "Client",
        _client_factory(lambda r: httpx.Response(402, json={"error": {"code": 402, "message": "Insufficient credits"}})),
    )
    with pytest.raises(ProviderError) as ei:
        openrouter_client.generate_images("k", "https://x/v1", "m", "p")
    assert "insufficient credits" in friendly_error(ei.value, "OpenRouter").lower()


def test_openrouter_retries_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"retry-after": "0"})
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"Z").decode(), "media_type": "image/png"}]})

    monkeypatch.setattr(openrouter_client.httpx, "Client", _client_factory(handler))
    monkeypatch.setattr(openrouter_client.time, "sleep", lambda s: None)
    assert openrouter_client.generate_images("k", "https://x/v1", "m", "p")[0][0] == b"Z"
    assert calls["n"] == 3


# ---------------------------------------------------------------- settings migration / secrets
def test_deprecated_models_migrated_and_env_keys_not_persisted(monkeypatch):
    import config

    config.SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    config.SETTINGS_PATH.write_text(
        json.dumps({"model": "llama-3.3-70b-versatile", "vision_model": "meta-llama/llama-4-scout-17b-16e-instruct",
                    "image_model": "inclusionai/ming-image-0.1-design-layer2"}),
        encoding="utf-8",
    )
    s = config.load_settings()
    assert s["model"] == "openai/gpt-oss-120b"
    assert s["vision_model"] == "qwen/qwen3.8-27b"
    assert s["image_model"] == "inclusionai/ming-image-0.1-design"

    monkeypatch.setitem(config._SECRET_ENV_DEFAULTS, "api_key", "gsk_from_env")
    s["api_key"] = "gsk_from_env"
    s["openrouter_api_key"] = "sk-or-typed-in-dialog"
    config.save_settings(s)
    saved = json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
    assert "api_key" not in saved                        # mirrors .env -> not copied to disk
    assert saved["openrouter_api_key"] == "sk-or-typed-in-dialog"
