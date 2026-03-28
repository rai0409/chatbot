from __future__ import annotations

from typing import Optional

from config import OPENAI_API_KEY, OPENAI_BASE_URL


def ensure_openai_client(base_url: Optional[str] = None):
    if OPENAI_API_KEY is None or str(OPENAI_API_KEY).strip() == "":
        raise RuntimeError("OPENAI_API_KEY is missing")
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError("OpenAI SDK is required") from exc
    url = base_url if base_url else OPENAI_BASE_URL
    if url:
        return OpenAI(base_url=url, api_key=OPENAI_API_KEY)
    return OpenAI(api_key=OPENAI_API_KEY)
