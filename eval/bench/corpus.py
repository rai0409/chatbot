"""Corpus loading and re-chunking for experiments.

chunking "as_is" uses the canonical chunk JSONL as production does (child
chunks only; parents are expanded after ranking, not ranked). chunking
"window" rebuilds documents and cuts sentence-aligned windows of `chars`
characters with `overlap` (fraction) carried over, which lets chunk length
be swept mechanically. Documents come from documents_jsonl ({doc_id, text,
tenant_id}) when given, else from parent chunks, else from child chunks in
chunk_index order. Use match_mode "evidence" or "doc" for window chunking:
chunk ids change with the chunk length.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from eval.bench.dataset import evidence_key

_SENTENCE_RE = re.compile(r"[^。！？!?\n]*(?:[。！？!?]|\n|$)")
_CHILD_ROLES = {"", "child", "leaf"}


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    tenant_id: str
    text: str
    text_key: str


def _read_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _doc_id(row: Dict[str, Any]) -> str:
    return str(row.get("doc_id") or row.get("source_doc") or row.get("id") or "")


def _text(row: Dict[str, Any]) -> str:
    return str(row.get("searchable_text") or row.get("text") or "")


def _make_chunk(chunk_id: str, doc_id: str, tenant_id: str, text: str) -> Chunk:
    return Chunk(chunk_id, doc_id, tenant_id or "default", text, evidence_key(text))


def load_as_is(chunks_jsonl: str | Path) -> List[Chunk]:
    out = []
    for row in _read_jsonl(chunks_jsonl):
        if str(row.get("chunk_role") or "").strip().lower() not in _CHILD_ROLES:
            continue
        if int(row.get("searchable", 1) or 0) != 1:
            continue
        text = _text(row)
        if text:
            out.append(_make_chunk(str(row.get("id")), _doc_id(row), str(row.get("tenant_id") or ""), text))
    return out


def load_documents(chunks_jsonl: str | Path, documents_jsonl: Optional[str | Path] = None) -> List[Dict[str, str]]:
    if documents_jsonl:
        return [
            {"doc_id": str(r["doc_id"]), "text": str(r["text"]), "tenant_id": str(r.get("tenant_id") or "default")}
            for r in _read_jsonl(documents_jsonl)
        ]
    rows = _read_jsonl(chunks_jsonl)
    parents = [r for r in rows if str(r.get("chunk_role") or "").lower() == "parent"]
    source = parents or [r for r in rows if str(r.get("chunk_role") or "").lower() in _CHILD_ROLES]
    by_doc: Dict[str, List[Dict[str, Any]]] = {}
    for row in source:
        by_doc.setdefault(_doc_id(row), []).append(row)
    docs = []
    for doc_id, items in by_doc.items():
        items.sort(key=lambda r: int(r.get("chunk_index") or 0))
        docs.append(
            {
                "doc_id": doc_id,
                "text": "\n".join(str(r.get("display_text") or r.get("text") or "") for r in items),
                "tenant_id": str(items[0].get("tenant_id") or "default"),
            }
        )
    return docs


def window_chunks(text: str, chars: int, overlap: float) -> List[str]:
    """Sentence-aligned windows of about `chars` characters.

    A sentence longer than `chars` becomes its own window (never split
    mid-sentence); `overlap` is the fraction of `chars` repeated from the end
    of the previous window.
    """
    if chars <= 0 or not 0.0 <= overlap < 1.0:
        raise ValueError("chars must be > 0 and overlap in [0, 1)")
    sentences = [s for s in _SENTENCE_RE.findall(text) if s.strip()]
    windows: List[str] = []
    current: List[str] = []
    for sentence in sentences:
        if current and sum(len(s) for s in current) + len(sentence) > chars:
            windows.append("".join(current).strip())
            carry: List[str] = []
            budget = int(chars * overlap)
            for previous in reversed(current):
                if sum(len(s) for s in carry) + len(previous) > budget:
                    break
                carry.insert(0, previous)
            current = carry
        current.append(sentence)
    if current:
        windows.append("".join(current).strip())
    return [w for w in windows if w]


def load_windowed(
    chunks_jsonl: str | Path, chars: int, overlap: float, documents_jsonl: Optional[str | Path] = None
) -> List[Chunk]:
    out = []
    for doc in load_documents(chunks_jsonl, documents_jsonl):
        for i, text in enumerate(window_chunks(doc["text"], chars, overlap)):
            out.append(_make_chunk(f"{doc['doc_id']}#w{chars}o{overlap}:{i}", doc["doc_id"], doc["tenant_id"], text))
    return out


def build_chunks(spec: Dict[str, Any], chunks_jsonl: str | Path, documents_jsonl: Optional[str | Path]) -> List[Chunk]:
    mode = spec.get("mode", "as_is")
    if mode == "as_is":
        return load_as_is(chunks_jsonl)
    if mode == "window":
        return load_windowed(chunks_jsonl, int(spec["chars"]), float(spec.get("overlap", 0.1)), documents_jsonl)
    raise ValueError(f"unknown chunking mode '{mode}' (supported: as_is, window)")


def corpus_fingerprint(chunks: Sequence[Chunk]) -> str:
    h = hashlib.sha256()
    for chunk in chunks:
        h.update(chunk.chunk_id.encode("utf-8"))
        h.update(b"\x00")
        h.update(chunk.text.encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()
