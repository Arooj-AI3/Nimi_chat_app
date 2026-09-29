"""
api/openrouter_client.py

OpenRouter image generation via the dedicated Images API:

    POST {base}/images   {"model", "prompt", "n", "input_references"?}
    ->  {"data": [{"b64_json": "...", "media_type": "image/png"}], "usage": {...}}

(Verified against OpenRouter's llms.txt for the inclusionai/ming-image-0.1-design
models on 2026-09-29.) Chat-completions is NOT used: image-only models such as
Ming are served by /images.
"""

from __future__ import annotations

import base64
import time
from typing import List, Optional, Tuple

import httpx

from api.errors import ProviderError

_RETRY_STATUS = {429, 502, 503, 504}


def _parse_error(resp: httpx.Response) -> ProviderError:
    message = resp.text[:300]
    try:
        payload = resp.json()
        err = payload.get("error", payload)
        if isinstance(err, dict) and err.get("message"):
            message = str(err["message"])
    except ValueError:
        pass
    retry_after: Optional[float] = None
    try:
        retry_after = float(resp.headers.get("retry-after", ""))
    except ValueError:
        pass
    return ProviderError(resp.status_code, message, retry_after)


def generate_images(
    api_key: str,
    base_url: str,
    model: str,
    prompt: str,
    reference_data_url: Optional[str] = None,
    timeout: float = 180.0,
    max_attempts: int = 3,
) -> List[Tuple[bytes, str]]:
    """Return [(image_bytes, media_type), ...]. Raises ProviderError / httpx errors."""
    body: dict = {"model": model, "prompt": prompt, "n": 1}
    if reference_data_url:
        body["input_references"] = [{"type": "image_url", "image_url": {"url": reference_data_url}}]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "Groq Chat Desktop",
    }
    url = base_url.rstrip("/") + "/images"

    last_error: Optional[Exception] = None
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
        for attempt in range(max_attempts):
            try:
                resp = client.post(url, headers=headers, json=body)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
                last_error = exc
                if attempt + 1 < max_attempts:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
            if resp.status_code in _RETRY_STATUS and attempt + 1 < max_attempts:
                err = _parse_error(resp)
                last_error = err
                time.sleep(min(err.retry_after or 2.0 * (attempt + 1), 20.0))
                continue
            if resp.status_code >= 400:
                raise _parse_error(resp)

            payload = resp.json()
            images: List[Tuple[bytes, str]] = []
            for item in payload.get("data", []):
                media_type = item.get("media_type", "image/png")
                if item.get("b64_json"):
                    images.append((base64.b64decode(item["b64_json"]), media_type))
                elif item.get("url"):
                    dl = client.get(item["url"])
                    dl.raise_for_status()
                    images.append((dl.content, dl.headers.get("content-type", media_type).split(";")[0]))
            if not images:
                raise ProviderError(502, "The image API returned no images. Try rephrasing the prompt.")
            return images

    raise last_error or ProviderError(502, "Image generation failed.")
