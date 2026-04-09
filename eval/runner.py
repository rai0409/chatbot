from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import config
from rag_core import qa, retrieval
from rag_core.retrieval import RetrievedChunk
from rag_core.utils import ensure_openai_client

SCHEMA_VERSION = "eval_runner.v1"
RUNNER_VERSION = "pr5a-lightweight"
_EXPECTATION_FIELDS = (
    "expected_top_chunk_id",
    "expected_top_source_doc",
    "expected_guard_reason",
    "expected_used_fallback",
    "answer_must_contain",
    "answer_must_not_contain",
    "selected_context_must_contain",
)


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    category: str
    query: str
    intent_override: Optional[str]
    expected_top_chunk_id: Optional[str]
    expected_top_source_doc: Optional[str]
    expected_guard_reason: Optional[str]
    expected_used_fallback: Optional[bool]
    answer_must_contain: Tuple[str, ...]
    answer_must_not_contain: Tuple[str, ...]
    selected_context_must_contain: Tuple[str, ...]
    notes: str
    expectation_keys: Tuple[str, ...]


class _StubEmbeddingItem:
    def __init__(self, embedding: List[float]):
        self.embedding = embedding


class _StubEmbeddingsResponse:
    def __init__(self, n_items: int):
        self.data = [_StubEmbeddingItem([0.0] * 8) for _ in range(n_items)]


class _StubEmbeddings:
    def create(self, model: str, input: Sequence[str]):
        return _StubEmbeddingsResponse(len(list(input)))


class _StubChatMessage:
    def __init__(self, content: str):
        self.content = content


class _StubChatChoice:
    def __init__(self, content: str):
        self.message = _StubChatMessage(content)


class _StubChatResponse:
    def __init__(self, content: str):
        self.choices = [_StubChatChoice(content)]


class _StubChatCompletions:
    def create(self, model: str, messages: Sequence[Dict[str, str]], temperature: float = 0):
        del model, messages, temperature
        return _StubChatResponse("- 根拠に基づく回答です [S1]\n不足: なし [S1]")


class _StubChat:
    def __init__(self):
        self.completions = _StubChatCompletions()


class _StubClient:
    def __init__(self):
        self.embeddings = _StubEmbeddings()
        self.chat = _StubChat()


class _ClientWithStubbedChat:
    def __init__(self, base_client: Any):
        self.embeddings = base_client.embeddings
        self.chat = _StubChat()


def _default_cases_path() -> Path:
    return Path(__file__).resolve().parent / "cases" / "smoke_cases.jsonl"


def _default_chunks_path() -> Path:
    return Path(__file__).resolve().parent / "cases" / "smoke_chunks.jsonl"


def _normalize_case_record(record: Dict[str, Any], line_no: int) -> EvalCase:
    required = ["case_id", "category", "query"]
    missing = [k for k in required if str(record.get(k, "")).strip() == ""]
    if missing:
        raise ValueError(f"case line {line_no}: missing required fields: {', '.join(missing)}")

    present_expectations = tuple(k for k in _EXPECTATION_FIELDS if k in record)
    must_contain = tuple(str(x) for x in (record.get("answer_must_contain") or []))
    must_not_contain = tuple(str(x) for x in (record.get("answer_must_not_contain") or []))
    context_must_contain = tuple(str(x) for x in (record.get("selected_context_must_contain") or []))

    expected_used_fallback = record.get("expected_used_fallback")
    if expected_used_fallback is not None:
        expected_used_fallback = bool(expected_used_fallback)

    return EvalCase(
        case_id=str(record["case_id"]),
        category=str(record["category"]),
        query=str(record["query"]),
        intent_override=(
            str(record["intent_override"]) if record.get("intent_override") not in (None, "") else None
        ),
        expected_top_chunk_id=(
            str(record["expected_top_chunk_id"])
            if record.get("expected_top_chunk_id") not in (None, "")
            else None
        ),
        expected_top_source_doc=(
            str(record["expected_top_source_doc"])
            if record.get("expected_top_source_doc") not in (None, "")
            else None
        ),
        expected_guard_reason=(
            str(record["expected_guard_reason"])
            if record.get("expected_guard_reason") not in (None, "")
            else None
        ),
        expected_used_fallback=expected_used_fallback,
        answer_must_contain=must_contain,
        answer_must_not_contain=must_not_contain,
        selected_context_must_contain=context_must_contain,
        notes=str(record.get("notes") or record.get("why") or ""),
        expectation_keys=present_expectations,
    )


def load_cases(path: Path) -> List[EvalCase]:
    cases: List[EvalCase] = []
    with path.open("r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            obj = json.loads(raw)
            cases.append(_normalize_case_record(obj, idx))
    return cases


def _reset_keyword_index_cache() -> None:
    retrieval._INDEX_CACHE["path"] = None  # type: ignore[attr-defined]
    retrieval._INDEX_CACHE["mtime"] = None  # type: ignore[attr-defined]
    retrieval._INDEX_CACHE["index"] = None  # type: ignore[attr-defined]


@contextmanager
def _eval_runtime(chunks_jsonl: Optional[Path], stub_vector: bool):
    prev_chunks_path = config.CHUNKS_JSONL_PATH
    prev_vector_retrieve = retrieval.vector_retrieve

    if chunks_jsonl is not None:
        config.CHUNKS_JSONL_PATH = str(chunks_jsonl.resolve())
    _reset_keyword_index_cache()

    if stub_vector:
        def _vector_stub(*args, **kwargs):
            del args, kwargs
            return []

        retrieval.vector_retrieve = _vector_stub  # type: ignore[assignment]

    try:
        yield
    finally:
        retrieval.vector_retrieve = prev_vector_retrieve
        config.CHUNKS_JSONL_PATH = prev_chunks_path
        _reset_keyword_index_cache()


def _build_eval_client(real_vector: bool, real_generation: bool):
    if real_vector or real_generation:
        client = ensure_openai_client(base_url=config.OPENAI_BASE_URL)
        if real_vector and not real_generation:
            return _ClientWithStubbedChat(client)
        return client
    return _StubClient()


def _chunk_to_view(rank: int, ch: RetrievedChunk) -> Dict[str, Any]:
    meta = ch.metadata or {}
    return {
        "rank": rank,
        "chunk_id": str(meta.get("id") or ""),
        "source_doc": str(meta.get("source_doc") or meta.get("doc") or "unknown"),
        "score": float(ch.score),
        "retrieval_source": str(meta.get("retrieval_source") or ""),
    }


def top_entries(chunks: Sequence[RetrievedChunk], limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rank, ch in enumerate(chunks[:limit], start=1):
        out.append(_chunk_to_view(rank, ch))
    return out


def _expectations_dict(case: EvalCase) -> Dict[str, Any]:
    values = {
        "expected_top_chunk_id": case.expected_top_chunk_id,
        "expected_top_source_doc": case.expected_top_source_doc,
        "expected_guard_reason": case.expected_guard_reason,
        "expected_used_fallback": case.expected_used_fallback,
        "answer_must_contain": list(case.answer_must_contain),
        "answer_must_not_contain": list(case.answer_must_not_contain),
        "selected_context_must_contain": list(case.selected_context_must_contain),
    }
    return {k: values[k] for k in case.expectation_keys}


def evaluate_expectations(
    case: EvalCase,
    *,
    after_rerank_top: Sequence[Dict[str, Any]],
    guard_reason: Optional[str],
    used_fallback: bool,
    answer_text: str,
    selected_context_preview: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    checks: Dict[str, Dict[str, Any]] = {}
    top = after_rerank_top[0] if after_rerank_top else {}

    if "expected_top_chunk_id" in case.expectation_keys:
        expected = case.expected_top_chunk_id
        actual = top.get("chunk_id") if top else None
        checks["expected_top_chunk_id"] = {
            "pass": actual == expected,
            "expected": expected,
            "actual": actual,
        }

    if "expected_top_source_doc" in case.expectation_keys:
        expected = case.expected_top_source_doc
        actual = top.get("source_doc") if top else None
        checks["expected_top_source_doc"] = {
            "pass": actual == expected,
            "expected": expected,
            "actual": actual,
        }

    if "expected_guard_reason" in case.expectation_keys:
        expected = case.expected_guard_reason
        actual = guard_reason
        checks["expected_guard_reason"] = {
            "pass": actual == expected,
            "expected": expected,
            "actual": actual,
        }

    if "expected_used_fallback" in case.expectation_keys:
        expected = case.expected_used_fallback
        actual = used_fallback
        checks["expected_used_fallback"] = {
            "pass": actual == expected,
            "expected": expected,
            "actual": actual,
        }

    if "answer_must_contain" in case.expectation_keys:
        missing = [term for term in case.answer_must_contain if term not in answer_text]
        checks["answer_must_contain"] = {
            "pass": not missing,
            "expected": list(case.answer_must_contain),
            "actual": {"missing": missing},
        }

    if "answer_must_not_contain" in case.expectation_keys:
        present = [term for term in case.answer_must_not_contain if term in answer_text]
        checks["answer_must_not_contain"] = {
            "pass": not present,
            "expected": list(case.answer_must_not_contain),
            "actual": {"present": present},
        }

    if "selected_context_must_contain" in case.expectation_keys:
        context_blob = "\n".join(str(x) for x in selected_context_preview)
        missing = [term for term in case.selected_context_must_contain if term not in context_blob]
        checks["selected_context_must_contain"] = {
            "pass": not missing,
            "expected": list(case.selected_context_must_contain),
            "actual": {"missing": missing},
        }

    return checks


def _overall_pass(checks: Dict[str, Dict[str, Any]]) -> bool:
    if not checks:
        return True
    return all(bool(v.get("pass")) for v in checks.values())


def _answer_summary(answer_text: str, max_chars: int = 80) -> str:
    lines = [ln.strip() for ln in answer_text.splitlines() if ln.strip()]
    first = lines[0] if lines else ""
    if len(first) <= max_chars:
        return first
    return first[: max_chars - 1] + "…"


def _format_top(items: Sequence[Dict[str, Any]]) -> str:
    if not items:
        return "-"
    return ", ".join(f"{it.get('chunk_id') or '?'}@{it.get('source_doc') or '?'}" for it in items)


def run_eval(
    *,
    cases_path: Path,
    chunks_jsonl: Optional[Path],
    output_path: Path,
    top_k: int,
    max_context_chars: int,
    top_n: int,
    real_vector: bool,
    real_generation: bool,
    quiet: bool = False,
) -> Dict[str, Any]:
    cases = load_cases(cases_path)
    mode = {
        "intent": "real",
        "query_rewrite": "real",
        "retrieval": (
            "real_hybrid(vector+keyword)"
            if real_vector
            else "real_hybrid(keyword_real,vector_stubbed_empty)"
        ),
        "unique_chunk_handling": "real",
        "neighbor_expansion": "real",
        "rerank": "real",
        "grounded_shaping": "real",
        "guard": "real",
        "fallback": "real",
        "generation": "real" if real_generation else "stubbed_deterministic",
    }

    client = _build_eval_client(real_vector=real_vector, real_generation=real_generation)
    results: List[Dict[str, Any]] = []

    with _eval_runtime(chunks_jsonl=chunks_jsonl, stub_vector=not real_vector):
        for case in cases:
            answer, trace = qa.answer_query_with_trace(
                case.query,
                client=client,
                top_k=top_k,
                max_context_chars=max_context_chars,
                intent_override=case.intent_override,
            )
            before = top_entries(trace.get("before_rerank", []), top_n)
            after = top_entries(trace.get("after_rerank", []), top_n)

            checks = evaluate_expectations(
                case,
                after_rerank_top=after,
                guard_reason=answer.guard_reason,
                used_fallback=answer.used_fallback,
                answer_text=answer.answer_text,
                selected_context_preview=trace.get("selected_context_preview", []),
            )
            case_pass = _overall_pass(checks)

            record = {
                "case_id": case.case_id,
                "category": case.category,
                "query": case.query,
                "intent": answer.intent,
                "before_rerank_top": before,
                "after_rerank_top": after,
                "final_guard_reason": answer.guard_reason,
                "final_used_fallback": answer.used_fallback,
                "final_answer_summary": _answer_summary(answer.answer_text),
                "selected_context_preview": trace.get("selected_context_preview", []),
                "expectations": _expectations_dict(case),
                "checks": checks,
                "overall_pass": case_pass,
                "evaluation_mode": mode,
                "rewritten_query": answer.rewritten_query,
                "augmented_query": answer.augmented_query,
            }
            results.append(record)

            if not quiet:
                status = "PASS" if case_pass else "FAIL"
                print(
                    f"[{status}] {case.case_id} ({case.category}) "
                    f"before={_format_top(before)} after={_format_top(after)} "
                    f"guard={answer.guard_reason} fallback={answer.used_fallback} "
                    f"answer={record['final_answer_summary']}"
                )

    passed = sum(1 for r in results if r["overall_pass"])
    failed = len(results) - passed
    summary = {
        "schema_version": SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(results),
        "passed_cases": passed,
        "failed_cases": failed,
    }
    payload = {
        "summary": summary,
        "cases": results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not quiet:
        print(
            f"\nSummary: passed={passed}/{len(results)} failed={failed} "
            f"output={output_path}"
        )
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Lightweight repo-native smoke evaluator for grounded JA RAG "
            "(retrieval/rerank/guard/fallback regression checks)."
        ),
        epilog=(
            "Default mode is deterministic/local-friendly: generation is stubbed and "
            "vector retrieval is stubbed empty unless --real-vector is enabled. "
            "This is not a full live end-to-end answer quality benchmark."
        ),
    )
    parser.add_argument("--cases", default=str(_default_cases_path()))
    parser.add_argument("--chunks-jsonl", default=str(_default_chunks_path()))
    parser.add_argument("--output", default="")
    parser.add_argument("--top-k", type=int, default=config.TOP_K)
    parser.add_argument("--max-context-chars", type=int, default=config.MAX_CONTEXT_CHARS)
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument(
        "--real-vector",
        action="store_true",
        help=(
            "Enable real vector retrieval (default: vector retrieval is stubbed empty; "
            "keyword retrieval remains real)."
        ),
    )
    parser.add_argument(
        "--real-generation",
        action="store_true",
        help="Enable real chat generation (default: deterministic stubbed generation).",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    cases_path = Path(args.cases)
    chunks_arg = str(args.chunks_jsonl).strip()
    chunks_jsonl = Path(chunks_arg) if chunks_arg else None

    if not args.output:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path(config.RUNS_DIR) / "eval" / f"results_{ts}.json"
    else:
        output_path = Path(args.output)

    run_eval(
        cases_path=cases_path,
        chunks_jsonl=chunks_jsonl,
        output_path=output_path,
        top_k=args.top_k,
        max_context_chars=args.max_context_chars,
        top_n=args.top_n,
        real_vector=bool(args.real_vector),
        real_generation=bool(args.real_generation),
        quiet=bool(args.quiet),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
