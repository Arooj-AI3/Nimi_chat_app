"""
api/image_utils.py

Image plumbing shared by the UI and the API layer:
    * validate + downscale uploaded images (keeps requests under provider limits)
    * turn a stored image into a base64 data URL
    * save generated images to disk so chat history can reference them by path

Uses QImage only (safe outside the GUI thread; no QPixmap here).
"""

from __future__ import annotations

import base64
import mimetypes
import uuid
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter

from config import IMAGES_DIR, MAX_IMAGE_SIDE_PX

UPLOADS_DIR = IMAGES_DIR / "uploads"
GENERATED_DIR = IMAGES_DIR / "generated"

_EXT_BY_MEDIA_TYPE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def ensure_dirs() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)


def _flatten_on_white(image: QImage) -> QImage:
    """JPEG has no alpha; composite transparent PNG/WebP onto white."""
    if not image.hasAlphaChannel():
        return image.convertToFormat(QImage.Format_RGB32)
    canvas = QImage(image.size(), QImage.Format_RGB32)
    canvas.fill(QColor("white"))
    painter = QPainter(canvas)
    painter.drawImage(0, 0, image)
    painter.end()
    return canvas


def store_upload(path: str) -> str:
    """
    Decode `path`, downscale so the longest side <= MAX_IMAGE_SIDE_PX and save a
    JPEG copy inside the app folder. Returns the stored path.
    Raises ValueError if the file is not a decodable image.
    """
    ensure_dirs()
    image = QImage(path)
    if image.isNull():
        raise ValueError("This file could not be read as an image (corrupt or unsupported format).")
    if max(image.width(), image.height()) > MAX_IMAGE_SIDE_PX:
        image = image.scaled(
            MAX_IMAGE_SIDE_PX, MAX_IMAGE_SIDE_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
    image = _flatten_on_white(image)
    target = UPLOADS_DIR / f"{uuid.uuid4().hex}.jpg"
    if not image.save(str(target), "JPEG", 85):
        raise ValueError("Could not process the image.")
    return str(target)


def save_generated(data: bytes, media_type: str) -> str:
    """Persist raw image bytes from the image API. Raises ValueError if undecodable."""
    ensure_dirs()
    media_type = (media_type or "image/png").lower()
    ext = _EXT_BY_MEDIA_TYPE.get(media_type)
    if ext is None:
        raise ValueError(f"The image API returned an unsupported type ({media_type}).")
    probe = QImage()
    if not probe.loadFromData(data):
        raise ValueError("The image API returned data that is not a valid image.")
    target = GENERATED_DIR / f"{uuid.uuid4().hex}{ext}"
    target.write_bytes(data)
    return str(target)


def file_to_data_url(path: str) -> Optional[str]:
    """Return a base64 data URL for a stored image, or None if it is gone."""
    p = Path(path)
    if not p.exists():
        return None
    mime = mimetypes.guess_type(str(p))[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"
