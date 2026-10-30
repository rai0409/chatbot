from __future__ import annotations

from typing import Optional

from config import OPENAI_API_KEY, OPENAI_BASE_URL


def ensure_openai_client(base_url: Optional[str] = None):
    url = base_url if base_url else OPENAI_BASE_URL
    key = "" if OPENAI_API_KEY is None else str(OPENAI_API_KEY).strip()
    if not key:
        if not url:
            raise RuntimeError("OPENAI_API_KEY is missing")
        key = "not-needed"  # a local OpenAI-compatible endpoint that checks no key (the SDK wants a non-empty one)
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError("OpenAI SDK is required") from exc
    if url:
        return OpenAI(base_url=url, api_key=key)
    return OpenAI(api_key=key)
