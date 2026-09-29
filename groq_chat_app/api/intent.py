"""
api/intent.py

Decides whether a message is a request to GENERATE an image (-> OpenRouter) or a
normal chat/vision message (-> Groq).

Order of precedence:
    1. "/image <text>"  or "/chat <text>" prefixes always win.
    2. The image-mode toggle in the input bar (handled by the UI, passed as `forced`).
    3. A conservative keyword heuristic (English, Urdu, Spanish, French):
       <creation verb> ... <image noun>, e.g. "draw a logo for my cafe".

The heuristic is deliberately conservative: analysing a picture ("what is in this
image?", "describe this photo") must never trigger generation.
"""

from __future__ import annotations

import re
from typing import Tuple

_VERBS = (
    r"generate|create|make|draw|paint|design|render|produce|illustrate|sketch|"
    r"genera|crea|dibuja|haz|dise[nñ]a|pinta|"
    r"g[eé]n[eè]re|cr[eé]e|dessine|fais|con[cç]ois|"
    r"بنا|بناؤ|بنائیں|بنادو|بنا دو|تخلیق|ڈرا"
)
_NOUNS = (
    r"image|images|picture|pictures|photo|photos|illustration|logo|poster|banner|icon|"
    r"wallpaper|artwork|drawing|painting|infographic|diagram|flyer|thumbnail|avatar|"
    r"sticker|mockup|cover|"
    r"imagen|im[aá]genes|foto|dibujo|p[oó]ster|ilustraci[oó]n|"
    r"affiche|dessin|"
    r"تصویر|تصاویر|فوٹو|لوگو|پوسٹر|ڈیزائن|خاکہ"
)
_GEN = re.compile(rf"\b(?:{_VERBS})\b[^.\n]{{0,60}}?\b(?:{_NOUNS})\b|(?:{_NOUNS})[^.\n]{{0,40}}?(?:{_VERBS})", re.I)
# "draw/sketch/paint me a cat" at the START of a message is an image request even without
# an image noun. Abstract uses ("draw a conclusion") are excluded.
_DRAW_START = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+)?(?:draw|sketch|paint|dibuja|dessine)\s+(?:me\s+)?"
    r"(?:a|an|the|some|un|una|une|le|la)?\s*(?!conclusion|comparison|parallel|line\b|distinction|attention|"
    r"inspiration|analogy|blank|breath)\w+",
    re.I,
)
_NOT_GEN = re.compile(
    r"\b(caption|description|alt text|describe|explain|analy[sz]e|summari[sz]e|ocr|extract|translate|"
    r"what(?:'s| is) in|read)\b",
    re.I,
)


def detect_image_intent(text: str) -> Tuple[bool, str]:
    """Return (wants_image, cleaned_text). `cleaned_text` has any /image or /chat prefix removed."""
    stripped = (text or "").strip()
    lowered = stripped.lower()
    if lowered.startswith("/image"):
        return True, stripped[6:].strip()
    if lowered.startswith("/chat"):
        return False, stripped[5:].strip()
    if not stripped:
        return False, stripped
    if _NOT_GEN.search(stripped) and not re.search(r"\b(generate|create|draw|paint)\b", lowered):
        return False, stripped
    return bool(_GEN.search(stripped) or _DRAW_START.search(stripped)), stripped
