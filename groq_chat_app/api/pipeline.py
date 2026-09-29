"""
api/pipeline.py

Image-generation pipeline, run on a QThread:

    [optional] 1. "prompt writer": Groq turns the user's request + any attached
                  PDF/text/image into ONE detailed image prompt. This is what makes
                  "generate an image from this PDF's requirements" work — the image
                  model never sees the raw document, only a distilled prompt.
               2. OpenRouter Images API renders the prompt.
               3. Results are validated and saved to disk.

Plain requests ("draw a red fox logo") skip step 1 and go straight to step 2.
"""

from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import QThread, Signal

from api.errors import friendly_error
from api.groq_client import GroqClient, strip_think
from api.image_utils import file_to_data_url, save_generated
from api.openrouter_client import generate_images

_WRITER_SYSTEM = (
    "You are an expert prompt engineer for text-to-image models. Using the user's request "
    "and any attached document or image, write ONE detailed image-generation prompt "
    "(90-180 words): subject, setting, style, composition, lighting, colour palette. "
    "If the user wants specific text to appear in the image (title, labels, slogan), put it "
    "in double quotes exactly as it should be spelled, in its original language. "
    "Write the rest of the prompt in English. Output ONLY the prompt — no preface, no notes."
)
_DOC_CHARS_FOR_WRITER = 12000
_MAX_PROMPT_CHARS = 1800


class ImageWorker(QThread):
    """
    Signals:
        status(str)              progress text for the placeholder bubble
        completed(str, list)     (prompt_used, [saved image paths])
        error(str)               friendly error message
    """

    status = Signal(str)
    completed = Signal(str, list)
    error = Signal(str)

    def __init__(
        self,
        settings: Dict,
        user_text: str,
        doc_text: str,
        image_paths: List[str],
        thread_id: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._s = settings
        self._user_text = user_text.strip()
        self._doc_text = doc_text.strip()
        self._image_paths = image_paths
        self.thread_id = thread_id
        self._cancelled = False

    def stop(self) -> None:
        """Cancel: the HTTP call can't be aborted mid-flight, but its result is discarded."""
        self._cancelled = True

    # ------------------------------------------------------------------ #
    def _write_prompt(self) -> str:
        """Ask Groq for a rich image prompt. Falls back to the raw text on failure."""
        needs_writer = bool(self._doc_text or self._image_paths)
        if not needs_writer:
            return self._user_text

        self.status.emit("writing_prompt")
        request = self._user_text or "Create an image that captures the essence of the attached material."
        text = f"User request:\n{request}"
        if self._doc_text:
            text += f"\n\nAttached document:\n{self._doc_text[:_DOC_CHARS_FOR_WRITER]}"

        urls = [u for u in (file_to_data_url(p) for p in self._image_paths[:3]) if u]
        if urls:
            content: object = [{"type": "text", "text": text}] + [
                {"type": "image_url", "image_url": {"url": u}} for u in urls
            ]
            model = self._s.get("vision_model")
        else:
            content = text
            model = self._s.get("model")

        try:
            client = GroqClient(self._s.get("api_key", "").strip(), self._s.get("base_url"))
            written = client.complete(
                model,
                [{"role": "system", "content": _WRITER_SYSTEM}, {"role": "user", "content": content}],
                max_tokens=1500,  # reasoning models spend part of this on thinking
            )
            written = strip_think(written).strip().strip('"')
            if written:
                return written[:_MAX_PROMPT_CHARS]
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            self.status.emit(f"prompt_writer_failed:{friendly_error(exc, 'Groq')}")
        return (self._user_text or self._doc_text[:1200])[:_MAX_PROMPT_CHARS]

    def run(self) -> None:  # noqa: D401 - Qt override
        try:
            prompt = self._write_prompt()
            if not prompt.strip():
                self.error.emit("There is nothing to generate an image from. Describe the image you want.")
                return

            self.status.emit("generating_image")
            model = self._s.get("image_model")
            reference = None
            edit_model = (self._s.get("image_edit_model") or "").strip()
            if edit_model and self._image_paths:
                model = edit_model
                reference = file_to_data_url(self._image_paths[0])

            raw = generate_images(
                api_key=self._s.get("openrouter_api_key", "").strip(),
                base_url=self._s.get("openrouter_base_url"),
                model=model,
                prompt=prompt,
                reference_data_url=reference,
            )
            paths = [save_generated(data, media_type) for data, media_type in raw]
        except ValueError as exc:  # invalid image bytes
            self.error.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.error.emit(friendly_error(exc, "OpenRouter"))
            return

        if not self._cancelled:
            self.completed.emit(prompt, paths)
