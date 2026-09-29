"""Headless end-to-end run of the real widgets with the network layer faked."""
import time

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

import api.pipeline as pipeline
import ui.main_window as mw
from api import groq_client


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _wait(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def _png_bytes():
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    img = QImage(64, 64, QImage.Format_RGB32)
    img.fill(QColor("green"))
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


class FakeClient:
    """Stands in for GroqClient: records requests, streams a canned answer."""

    calls = []
    fail_first_with = None

    def __init__(self, *a, **k):
        pass

    def stream_chat(self, model, messages, max_tokens, should_stop=lambda: False):
        FakeClient.calls.append((model, messages))
        if FakeClient.fail_first_with and model == "openai/gpt-oss-120b":
            raise FakeClient.fail_first_with
        for piece in ["Hello ", "from ", model]:
            yield piece

    def complete(self, model, messages, max_tokens):
        FakeClient.calls.append((model, messages))
        return "A detailed poster of a robotics fair, blue and orange, title \"Robo Expo\"."


@pytest.fixture()
def window(app, monkeypatch, tmp_path):
    import config

    for path in (config.HISTORY_PATH, config.SETTINGS_PATH):  # fresh state for every test
        if path.exists():
            path.unlink()
    FakeClient.calls = []
    FakeClient.fail_first_with = None
    monkeypatch.setattr(groq_client, "GroqClient", FakeClient)
    monkeypatch.setattr(pipeline, "GroqClient", FakeClient)
    w = mw.MainWindow()
    w.settings.update({"api_key": "gsk_test", "openrouter_api_key": "sk-or-test"})
    return w


def _send(w, text, files=(), image_mode=False):
    cw = w.chat_widget
    cw._attach_paths(list(files))
    cw.image_mode_button.setChecked(image_mode)
    cw.message_input.setPlainText(text)
    cw._on_send_clicked()


def test_plain_chat_streams_and_persists(window):
    _send(window, "hi there")
    assert _wait(lambda: window.worker is None and not window.chat_widget._is_generating)
    msgs = window._get_thread(window.active_thread_id)["messages"]
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["content"].startswith("Hello from openai/gpt-oss-120b")
    assert len(window.chat_widget._bubbles) == len(msgs)      # indices stay aligned


def test_image_question_uses_vision_model(window, tmp_path):
    p = tmp_path / "pic.png"
    QImage(50, 50, QImage.Format_RGB32).save(str(p))
    _send(window, "what is in this picture?", [str(p)])
    assert _wait(lambda: window.worker is None and not window.chat_widget._is_generating)
    model, messages = FakeClient.calls[-1]
    assert model == "qwen/qwen3.8-27b"
    assert any(part["type"] == "image_url" for part in messages[-1]["content"])


def test_rate_limit_falls_back_to_second_model(window):
    from api.errors import ProviderError

    FakeClient.fail_first_with = ProviderError(429, "Rate limit reached on tokens per minute")
    _send(window, "hello")
    assert _wait(lambda: window.worker is None and not window.chat_widget._is_generating)
    used = [m for m, _ in FakeClient.calls]
    assert used[:2] == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    assert window._get_thread(window.active_thread_id)["messages"][-1]["role"] == "assistant"


def test_image_generation_from_text(window, monkeypatch):
    monkeypatch.setattr(pipeline, "generate_images", lambda **k: [(_png_bytes(), "image/png")])
    _send(window, "draw a green square")            # auto-detected, no toggle
    assert _wait(lambda: window.worker is None and not window.chat_widget._is_generating)
    last = window._get_thread(window.active_thread_id)["messages"][-1]
    assert last["kind"] == "image" and len(last["images"]) == 1
    assert not FakeClient.calls                      # plain prompt: no prompt-writer round trip


def test_image_generation_from_pdf_requirements(window, monkeypatch, tmp_path):
    from reportlab.pdfgen import canvas

    pdf = tmp_path / "req.pdf"
    c = canvas.Canvas(str(pdf))
    c.drawString(72, 720, "Requirement: poster for Robo Expo 2026, blue and orange, robot arm illustration")
    c.save()

    captured = {}

    def fake_generate(**kw):
        captured.update(kw)
        return [(_png_bytes(), "image/png")]

    monkeypatch.setattr(pipeline, "generate_images", fake_generate)
    _send(window, "make an image from these requirements", [str(pdf)])
    assert _wait(lambda: window.worker is None and not window.chat_widget._is_generating)
    # The prompt writer received the PDF text; the image API received the distilled prompt.
    writer_msgs = FakeClient.calls[-1][1]
    assert "Robo Expo" in writer_msgs[-1]["content"]
    assert "Robo Expo" in captured["prompt"] and captured["model"] == "inclusionai/ming-image-0.1-design"
    assert window._get_thread(window.active_thread_id)["messages"][-1]["kind"] == "image"


def test_image_error_is_shown_and_not_resent_to_model(window, monkeypatch):
    from api.errors import ProviderError

    def boom(**k):
        raise ProviderError(402, "Insufficient credits")

    monkeypatch.setattr(pipeline, "generate_images", boom)
    _send(window, "/image a cat")
    assert _wait(lambda: window.worker is None and not window.chat_widget._is_generating)
    last = window._get_thread(window.active_thread_id)["messages"][-1]
    assert last["kind"] == "error" and "insufficient credits" in last["content"].lower()

    _send(window, "hello again")                      # the error must not poison the next request
    assert _wait(lambda: window.worker is None and not window.chat_widget._is_generating)
    sent = str(FakeClient.calls[-1][1])
    assert "Insufficient" not in sent


def test_missing_openrouter_key_blocks_image_mode(window, monkeypatch):
    window.settings["openrouter_api_key"] = ""
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(window, "_on_settings_requested", lambda: None)
    _send(window, "/image a cat")
    assert window.worker is None
    assert window._get_thread(window.active_thread_id)["messages"] == []
