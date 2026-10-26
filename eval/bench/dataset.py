"""Evaluation set schema, loader and validation.

One JSON object per line:
  query_id    unique string
  query       question text
  split       "dev" or "test" (tune on dev; touch test once at the end)
  tenant_id   optional, default "default"
  answerable  optional, default true; false = the system must abstain
  relevant    list of evidence items (required when answerable), each with at
              least one of chunk_id / doc_id / evidence, and grade 1-3
              (default 1; nDCG uses gain 2^grade - 1)
  slices      optional tags, e.g. formal, colloquial, short, typo, alnum, table
  group       optional cluster id for the bootstrap; default: the first
              relevant doc_id, else the query_id (questions written from the
              same document are not independent)

match_mode decides how a retrieved chunk is matched to an evidence item:
  chunk     chunk_id equality (only while chunking is unchanged)
  doc       doc_id equality
  evidence  normalized evidence text contained in the chunk text; stays
            valid when chunk length changes, so use it for chunking sweeps
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

from rag_core.ja_text import normalize_for_index

MATCH_MODES = ("chunk", "doc", "evidence")
SPLITS = ("dev", "test")
_SPACE_RE = re.compile(r"\s+")
_MAX_ERRORS = 20


@dataclass(frozen=True)
class Relevant:
    chunk_id: Optional[str]
    doc_id: Optional[str]
    evidence: Optional[str]
    grade: int


@dataclass(frozen=True)
class EvalQuery:
    query_id: str
    query: str
    split: str
    tenant_id: str
    answerable: bool
    relevant: Tuple[Relevant, ...]
    slices: Tuple[str, ...]
    group: str


@lru_cache(maxsize=65536)
def evidence_key(text: str) -> str:
    """Normalization used on both sides of evidence matching (cached: the
    same evidence strings are matched against every ranked chunk)."""
    return _SPACE_RE.sub("", normalize_for_index(text or ""))


def file_fingerprint(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _parse_relevant(item, where: str, errors: List[str]) -> Optional[Relevant]:
    if not isinstance(item, dict):
        errors.append(f"{where}: relevant item must be an object")
        return None
    keys = {k: (str(item[k]).strip() if item.get(k) is not None else None) for k in ("chunk_id", "doc_id", "evidence")}
    if not any(keys.values()):
        errors.append(f"{where}: relevant item needs chunk_id, doc_id or evidence")
        return None
    grade = item.get("grade", 1)
    if not isinstance(grade, int) or isinstance(grade, bool) or not 1 <= grade <= 3:
        errors.append(f"{where}: grade must be an integer 1-3")
        return None
    return Relevant(keys["chunk_id"] or None, keys["doc_id"] or None, keys["evidence"] or None, grade)


def load_eval_set(path: str | Path, *, split: Optional[str] = None) -> List[EvalQuery]:
    errors: List[str] = []
    queries: List[EvalQuery] = []
    seen = set()
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        where = f"line {line_no}"
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{where}: invalid JSON ({exc.msg})")
            continue
        query_id, query = str(obj.get("query_id") or "").strip(), str(obj.get("query") or "").strip()
        if not query_id or not query:
            errors.append(f"{where}: query_id and query are required")
            continue
        if query_id in seen:
            errors.append(f"{where}: duplicate query_id {query_id}")
            continue
        seen.add(query_id)
        row_split = str(obj.get("split") or "").strip()
        if row_split not in SPLITS:
            errors.append(f"{where}: split must be one of {SPLITS}")
            continue
        answerable = bool(obj.get("answerable", True))
        relevant = [_parse_relevant(item, where, errors) for item in obj.get("relevant") or []]
        relevant = [item for item in relevant if item is not None]
        if answerable and not relevant:
            errors.append(f"{where}: answerable query needs at least one relevant item")
            continue
        default_group = next((item.doc_id for item in relevant if item.doc_id), None) or query_id
        queries.append(
            EvalQuery(
                query_id=query_id,
                query=query,
                split=row_split,
                tenant_id=str(obj.get("tenant_id") or "default"),
                answerable=answerable,
                relevant=tuple(relevant),
                slices=tuple(str(s) for s in obj.get("slices") or []),
                group=str(obj.get("group") or default_group),
            )
        )
    if errors:
        shown = "\n".join(errors[:_MAX_ERRORS])
        raise ValueError(f"{len(errors)} problem(s) in {path}:\n{shown}")
    if split is not None:
        queries = [q for q in queries if q.split == split]
    return queries


def check_match_mode(queries: List[EvalQuery], mode: str) -> None:
    if mode not in MATCH_MODES:
        raise ValueError(f"match_mode must be one of {MATCH_MODES}")
    field = {"chunk": "chunk_id", "doc": "doc_id", "evidence": "evidence"}[mode]
    missing = [q.query_id for q in queries if q.answerable and any(getattr(r, field) is None for r in q.relevant)]
    if missing:
        raise ValueError(f"match_mode={mode} needs '{field}' on every relevant item; missing in {missing[:10]}")
