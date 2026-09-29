"""
api/messages.py

Builds the exact message list sent to the chat model from a stored thread.

Why this exists (root cause of the "limit" errors):
    The old code re-sent the *entire* history on every turn and had no notion of
    images at all. Groq's free tiers have small per-minute token budgets and every
    image costs ~2048 tokens, so a couple of turns were enough to trip a 429/413.

Policy implemented here:
    * documents:  only the most recent message that carries documents includes the
      full extracted text (older ones are replaced by a one-line placeholder), so
      follow-up questions about a PDF keep working without re-sending it N times.
    * images:     only the most recent image-bearing message inside the last
      IMAGE_CONTEXT_WINDOW messages is re-sent, at most MAX_IMAGES_PER_REQUEST.
    * budget:     if the text still exceeds MAX_CONTEXT_CHARS, the OLDEST messages
      are dropped first; the latest user message is never dropped.
    * error bubbles and empty messages are never sent.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from api.image_utils import file_to_data_url
from config import IMAGE_CONTEXT_WINDOW, MAX_CONTEXT_CHARS, MAX_IMAGES_PER_REQUEST, SYSTEM_PROMPT

DEFAULT_IMAGE_QUESTION = "Please look at the attached image(s) and describe what you see."
DEFAULT_FILE_QUESTION = "Please read the attached file(s) and summarize the key points."


def _usable(message: Dict) -> bool:
    if message.get("kind") == "error":
        return False
    return bool(message.get("content", "").strip() or message.get("attachments"))


def _split_attachments(message: Dict) -> Tuple[List[Dict], List[Dict]]:
    atts = message.get("attachments") or []
    return (
        [a for a in atts if a.get("kind") == "document"],
        [a for a in atts if a.get("kind") == "image"],
    )


def build_api_messages(thread_messages: List[Dict]) -> Tuple[List[Dict], bool]:
    """Return (api_messages, has_images)."""
    msgs = [m for m in thread_messages if _usable(m)]

    last_doc_idx: Optional[int] = None
    last_img_idx: Optional[int] = None
    window_start = max(0, len(msgs) - IMAGE_CONTEXT_WINDOW)
    for i, m in enumerate(msgs):
        docs, imgs = _split_attachments(m)
        if docs:
            last_doc_idx = i
        if imgs and i >= window_start:
            last_img_idx = i

    built: List[Dict] = []
    sizes: List[int] = []
    has_images = False

    for i, m in enumerate(msgs):
        role = m.get("role", "user")
        text = m.get("content", "").strip()
        docs, imgs = _split_attachments(m)

        parts: List[str] = [text] if text else []
        for d in docs:
            if i == last_doc_idx:
                parts.append(f"[Attached file: {d['name']}]\n```\n{d.get('text', '')}\n```")
            else:
                parts.append(f"[Attached file: {d['name']} — content omitted to save tokens; ask the user to re-attach if needed]")

        image_urls: List[str] = []
        if i == last_img_idx:
            for img in imgs[:MAX_IMAGES_PER_REQUEST]:
                url = file_to_data_url(img.get("image", ""))
                if url:
                    image_urls.append(url)
                else:
                    parts.append(f"[Image {img.get('name', '')} is no longer available on disk]")
        elif imgs:
            parts.append("[Earlier image(s) omitted to save tokens]")

        body = "\n\n".join(parts).strip()
        if role == "user" and not body:
            body = DEFAULT_IMAGE_QUESTION if image_urls else DEFAULT_FILE_QUESTION
        elif role == "user" and not text and image_urls and not docs:
            body = DEFAULT_IMAGE_QUESTION

        if image_urls:
            has_images = True
            content: object = [{"type": "text", "text": body}] + [
                {"type": "image_url", "image_url": {"url": u}} for u in image_urls
            ]
        else:
            content = body

        built.append({"role": role, "content": content})
        sizes.append(len(body))

    # Trim oldest messages until we fit the text budget (keep the newest message).
    while len(built) > 1 and sum(sizes) > MAX_CONTEXT_CHARS:
        built.pop(0)
        sizes.pop(0)
    # Some models require the conversation to start with a user turn.
    while len(built) > 1 and built[0]["role"] != "user":
        built.pop(0)
        sizes.pop(0)

    # Only keep image parts if they survived trimming.
    has_images = any(isinstance(m["content"], list) for m in built)
    return [{"role": "system", "content": SYSTEM_PROMPT}] + built, has_images
