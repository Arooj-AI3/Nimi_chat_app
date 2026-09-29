"""
api/errors.py

Turns provider exceptions (Groq via the OpenAI SDK, OpenRouter via httpx) into
short, accurate, actionable messages.

The old implementation matched substrings like "401" or "model" anywhere in the
error text, so an unrelated message could be shown as "Authentication failed".
This version dispatches on the real exception type / HTTP status instead, and
always appends the provider's own message so the user sees the true cause
(e.g. "tokens per minute", "too many images", "model decommissioned").
"""

from __future__ import annotations

from typing import Optional


class ProviderError(Exception):
    """Raised by our own HTTP clients (OpenRouter) with a real status code."""

    def __init__(self, status: int, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


def _provider_message(exc: Exception) -> str:
    """Best-effort extraction of the provider's human-readable error message."""
    if isinstance(exc, ProviderError):
        return exc.message
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str):
            return err
    msg = getattr(exc, "message", None)
    return str(msg or exc)


def _status(exc: Exception) -> Optional[int]:
    if isinstance(exc, ProviderError):
        return exc.status
    code = getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


def _clip(text: str, limit: int = 320) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def friendly_error(exc: Exception, provider: str = "Groq") -> str:
    """Return a readable message for `exc`. `provider` is only used for wording."""
    detail = _clip(_provider_message(exc))
    lowered = detail.lower()
    status = _status(exc)
    name = type(exc).__name__

    # --- network problems (no HTTP status) ---------------------------------
    if name in ("APIConnectionError", "ConnectError", "ConnectTimeout", "NetworkError") or (
        status is None and any(w in name for w in ("Connect", "Network"))
    ):
        return f"Could not reach {provider}. Check your internet connection and try again."
    if name in ("APITimeoutError", "ReadTimeout", "TimeoutException", "WriteTimeout", "PoolTimeout"):
        return f"{provider} took too long to respond. Please try again (large files/images can be slow)."

    # --- HTTP status based ---------------------------------------------------
    if status == 401:
        return f"{provider} rejected the API key (401). Check it in Settings. — {detail}"
    if status == 402:
        return f"{provider}: insufficient credits (402). Top up your account or use a free model. — {detail}"
    if status == 403:
        return f"{provider}: access denied (403) — key disabled, spend limit reached or region blocked. — {detail}"
    if status == 404:
        return (
            f"{provider}: model or endpoint not found (404). The model may have been retired — "
            f"pick another one in Settings. — {detail}"
        )
    if status == 413 or "request too large" in lowered or "too large" in lowered:
        return (
            f"The request is too big for {provider}'s limits (image/PDF/history too large). "
            f"Try a smaller file, fewer pages or a new chat. — {detail}"
        )
    if status == 429 or "rate limit" in lowered:
        if "per day" in lowered or "tpd" in lowered or "rpd" in lowered:
            scope = "daily limit"
        elif "per minute" in lowered or "tpm" in lowered or "rpm" in lowered:
            scope = "per-minute limit"
        else:
            scope = "rate limit"
        return f"{provider} {scope} reached (429). Wait a bit and retry. — {detail}"
    if status == 400:
        return f"{provider} rejected the request (400). — {detail}"
    if status is not None and status >= 500:
        return f"{provider} had a server problem ({status}). Try again in a moment. — {detail}"

    return f"Unexpected error from {provider}: {detail}"


def is_rate_limited(exc: Exception) -> bool:
    return _status(exc) in (429, 413)


def is_model_missing(exc: Exception) -> bool:
    lowered = _provider_message(exc).lower()
    return _status(exc) == 404 or "decommissioned" in lowered or "model_not_found" in lowered
