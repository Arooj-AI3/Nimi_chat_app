"""
api/groq_client.py

Groq (OpenAI-compatible) chat + vision, run on a QThread so the UI never blocks.

Fixes vs. the previous version:
    * The worker's `finished(str)` signal SHADOWED QThread.finished. Qt emits the
      built-in no-argument `finished` when a thread ends, so that was a latent
      signature clash. Renamed to `completed`.
    * Errors are classified by exception type / HTTP status (see api/errors.py).
    * A request timeout is set (the default was effectively "wait forever").
    * On 429/413/model-missing the request is retried once on a fallback model
      (text requests only — Groq has a single vision model).
    * Partial text is kept if the stream dies half-way, and `stop()` really stops
      (the HTTP stream is closed instead of just being ignored).
    * <think>…</think> blocks emitted by reasoning models are stripped.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import httpx
from PySide6.QtCore import QThread, Signal

from api.errors import friendly_error, is_model_missing, is_rate_limited

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - surfaced via error signal at runtime
    OpenAI = None

_OPEN, _CLOSE = "<think>", "</think>"


def _partial_suffix_len(buf: str, tag: str) -> int:
    """Length of the longest suffix of `buf` that is a proper prefix of `tag`."""
    for k in range(min(len(tag) - 1, len(buf)), 0, -1):
        if tag.startswith(buf[-k:]):
            return k
    return 0


class ThinkStripper:
    """Streaming filter that removes <think>...</think> even when tags span chunks."""

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False

    def feed(self, text: str) -> str:
        self._buf += text
        out: List[str] = []
        while True:
            if self._inside:
                i = self._buf.find(_CLOSE)
                if i == -1:
                    keep = _partial_suffix_len(self._buf, _CLOSE)
                    self._buf = self._buf[len(self._buf) - keep:] if keep else ""
                    break
                self._buf = self._buf[i + len(_CLOSE):].lstrip("\n")
                self._inside = False
            else:
                i = self._buf.find(_OPEN)
                if i == -1:
                    keep = _partial_suffix_len(self._buf, _OPEN)
                    emit = self._buf[: len(self._buf) - keep]
                    out.append(emit)
                    self._buf = self._buf[len(self._buf) - keep:] if keep else ""
                    break
                out.append(self._buf[:i])
                self._buf = self._buf[i + len(_OPEN):]
                self._inside = True
        return "".join(out)

    def flush(self) -> str:
        rest = "" if self._inside else self._buf
        self._buf = ""
        return rest


def strip_think(text: str) -> str:
    s = ThinkStripper()
    return (s.feed(text) + s.flush()).strip()


class GroqClient:
    """Thin synchronous wrapper around the OpenAI-compatible Groq client."""

    def __init__(self, api_key: str, base_url: str, timeout: float = 90.0) -> None:
        if OpenAI is None:
            raise RuntimeError("The 'openai' package is not installed. Run: pip install openai")
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(timeout, connect=15.0),
            max_retries=2,  # SDK honours Retry-After on 429
        )

    def stream_chat(self, model: str, messages: List[Dict], max_tokens: int, should_stop=lambda: False):
        """Yield visible text deltas from a streaming completion."""
        stream = self._client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            max_completion_tokens=max_tokens,
        )
        stripper = ThinkStripper()
        try:
            for event in stream:
                if should_stop():
                    break
                if not event.choices:
                    continue
                content = getattr(event.choices[0].delta, "content", None)
                if content:
                    visible = stripper.feed(content)
                    if visible:
                        yield visible
            tail = stripper.flush()
            if tail:
                yield tail
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass

    def complete(self, model: str, messages: List[Dict], max_tokens: int) -> str:
        """Non-streaming completion (used by the image-prompt writer)."""
        resp = self._client.chat.completions.create(
            model=model, messages=messages, max_completion_tokens=max_tokens
        )
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        return strip_think(text)


class ChatWorker(QThread):
    """
    Streaming chat completion on a background thread.

    Signals:
        chunk_received(str)   text delta
        completed(str)        full text (also emitted, with partial text, after stop())
        error(str, str)       (friendly message, partial text produced so far)
        notice(str)           non-fatal info, e.g. "switched to fallback model"
    """

    chunk_received = Signal(str)
    completed = Signal(str)
    error = Signal(str, str)
    notice = Signal(str)

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        messages: List[Dict],
        max_tokens: int,
        fallback_model: Optional[str] = None,
        thread_id: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._messages = messages
        self._max_tokens = max_tokens
        self._fallback = fallback_model if fallback_model and fallback_model != model else None
        self.thread_id = thread_id  # which chat this reply belongs to
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:  # noqa: D401 - Qt override
        accumulated = ""
        try:
            client = GroqClient(self._api_key, self._base_url)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(friendly_error(exc, "Groq"), "")
            return

        # Vision requests can't fall back (Groq has a single vision model).
        has_images = any(isinstance(m.get("content"), list) for m in self._messages)
        models = [self._model] + ([self._fallback] if self._fallback and not has_images else [])

        for idx, model in enumerate(models):
            try:
                for delta in client.stream_chat(
                    model, self._messages, self._max_tokens, lambda: self._stop_requested
                ):
                    accumulated += delta
                    self.chunk_received.emit(delta)
                break
            except Exception as exc:  # noqa: BLE001 - surface any API/network error
                can_retry = idx + 1 < len(models) and not accumulated
                if can_retry and (is_rate_limited(exc) or is_model_missing(exc)):
                    self.notice.emit(f"{model} is unavailable right now — retrying with {models[idx + 1]}.")
                    continue
                self.error.emit(friendly_error(exc, "Groq"), accumulated)
                return
        self.completed.emit(accumulated)
