from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class CitationOut:
    number: int
    source_doc: str
    source_pages: List[int] = field(default_factory=list)
    chunk_id: Optional[str] = None


@dataclass(frozen=True)
class RetrievedChunkOut:
    text: str
    metadata: Dict[str, Any]
    score: float
    source_doc: str
    source_pages: List[int] = field(default_factory=list)


@dataclass(frozen=True)
class AnswerResult:
    answer_text: str
    answer_with_footnotes: str
    intent: str
    guard_reason: Optional[str]
    used_fallback: bool
    citations: List[CitationOut] = field(default_factory=list)
    retrieved: List[RetrievedChunkOut] = field(default_factory=list)
    rewritten_query: str = ""
    augmented_query: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
