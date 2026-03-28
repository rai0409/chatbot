from __future__ import annotations

import logging
import time
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import config
from rag_core.qa import answer_query, retrieve_chunks
from rag_core.utils import ensure_openai_client


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = FastAPI()
_start_time = time.time()
_total_requests = 0
_error_requests = 0


class ChatRequest(BaseModel):
    question: str
    top_k: Optional[int] = None
    max_context_chars: Optional[int] = None
    trace_id: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = None
    trace_id: Optional[str] = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return {
        "uptime_seconds": int(time.time() - _start_time),
        "total_requests": _total_requests,
        "error_requests": _error_requests,
    }


@app.post("/chat")
def chat(req: ChatRequest):
    global _total_requests, _error_requests
    _total_requests += 1
    try:
        client = ensure_openai_client(base_url=config.OPENAI_BASE_URL)
        ans = answer_query(
            req.question,
            client=client,
            top_k=req.top_k or config.TOP_K,
            max_context_chars=req.max_context_chars or config.MAX_CONTEXT_CHARS,
        )
        return {"answer": ans}
    except Exception:
        _error_requests += 1
        logging.exception("chat failed trace_id=%s", req.trace_id)
        raise HTTPException(status_code=500, detail="internal error")


@app.post("/search")
def search(req: SearchRequest):
    global _total_requests, _error_requests
    _total_requests += 1
    try:
        provider = (
            config.getenv_first("EMBED_PROVIDER", default="openai") or "openai"
        ).lower()
        client = None
        if provider != "local":
            client = ensure_openai_client(base_url=config.OPENAI_BASE_URL)
        hits = retrieve_chunks(
            req.query, client=client, top_k=req.top_k or config.TOP_K
        )
        return {
            "hits": [
                {"text": h.text, "metadata": h.metadata, "score": h.score}
                for h in hits
            ]
        }
    except Exception:
        _error_requests += 1
        logging.exception("search failed trace_id=%s", req.trace_id)
        raise HTTPException(status_code=500, detail="internal error")
