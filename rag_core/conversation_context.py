"""Rule-based follow-up question completion from conversation history (5-11).

A follow-up such as "それの期限は？" or "パートの場合は？" has no searchable
subject. When the question refers back (demonstratives, "じゃあ", "他には")
or has at most one content term, the content terms of the previous
question(s) that are missing from it are put in front:

  previous: "有給休暇の申請方法を教えて"   now: "期限は？"
  -> "有給休暇 申請方法 期限は？"

The original question is kept for approved-QA exact matching and display;
the completed one is used for retrieval and the answer cache key.
No model is called. Enable with CONVERSATION_CONTEXT_ENABLED=true (and
CONVERSATION_HISTORY_ENABLED=true, with a thread_id on the request).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from rag_core import ja_terms

_REFERENCE_RE = re.compile(
    r"^(それ|その|これ|この|あれ|あの|そちら|こちら|そっち|こっち|じゃあ|では|なら|他に|ほかに|他は|ほかは|あと|それで|ちなみに)"
    r"|(それ|その|これ|この)(は|の|って|も|を|が)"
)
_MAX_CONTEXT_TERMS = 4
_MAX_PREVIOUS = 2
_SHORT_QUESTION_CHARS = 14


def _terms(text: str) -> List[str]:
    return ja_terms.content_terms(text, "regex")


def needs_context(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    if _REFERENCE_RE.search(q):
        return True
    return len(_terms(q)) <= 1 and len(q) <= _SHORT_QUESTION_CHARS


def complete_question(question: str, previous_questions: Sequence[str]) -> Dict[str, Any]:
    """Returns {"question": completed, "completed": bool, "added_terms": [...]}."""
    q = (question or "").strip()
    result: Dict[str, Any] = {"question": q, "completed": False, "added_terms": []}
    if not previous_questions or not needs_context(q):
        return result
    present = set(_terms(q))
    added: List[str] = []
    for previous in reversed(list(previous_questions)[-_MAX_PREVIOUS:]):
        for term in _terms(previous):
            if term not in present and term not in added:
                added.append(term)
            if len(added) >= _MAX_CONTEXT_TERMS:
                break
        if added:
            break  # the most recent question that has terms is the context
    if not added:
        return result
    # Remove a leading demonstrative so it does not compete with the terms.
    body = re.sub(r"^(それ|その|これ|この|あれ|あの|じゃあ|では)(の|は|って|も|で)?[、,\s]*", "", q) or q
    result.update({"question": " ".join(added + [body]), "completed": True, "added_terms": added})
    return result


def previous_questions_from_turns(turns: Sequence[Dict[str, Any]], limit: int = _MAX_PREVIOUS) -> List[str]:
    out = [str(t.get("question") or "") for t in turns if str(t.get("question") or "").strip()]
    return out[-limit:]


def contextualize(question: str, turns: Optional[Sequence[Dict[str, Any]]]) -> Dict[str, Any]:
    return complete_question(question, previous_questions_from_turns(turns or []))
