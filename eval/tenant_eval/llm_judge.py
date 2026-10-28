"""LLM scoring of answers and its calibration against a person's scoring (step 4-4)."""
from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional, Sequence

from eval.tenant_eval.stats import clopper_pearson_upper
from rag_core import llm

MIN_CASES = 50
MIN_KAPPA = 0.6
MAX_FALSE_PASS_UPPER = 0.10

SYSTEM = (
    "あなたは回答の採点者です。「回答」が「基準の回答」と同じ内容を述べているかを判定してください。表現が違っても"
    "内容が同じなら pass、基準と異なる事実・数値・条件を含む、または必要な内容が欠けているなら fail です。"
    "{\"verdict\": \"pass\" または \"fail\", \"reason\": \"一文の理由\"} の JSON だけを返してください。"
)


def _expectation(case: Mapping[str, Any]) -> str:
    if case.get("reference"):
        return str(case["reference"])
    parts = []
    if case.get("expected_all"):
        parts.append("すべて含む語: " + "、".join(case["expected_all"]))
    if case.get("expected_any"):
        parts.append("いずれかを含む語: " + "、".join(case["expected_any"]))
    if case.get("forbidden_any"):
        parts.append("含んではいけない語: " + "、".join(case["forbidden_any"]))
    return "\n".join(parts)


def judge_case(
    case: Mapping[str, Any], answer_text: str, *, tenant_id: str = "default", client: Any = None
) -> Dict[str, str]:
    """{"verdict": "pass" | "fail" | "error", "reason"}. Anything but a clear pass or fail is an error."""
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"<質問>\n{case['question']}\n</質問>\n\n<基準の回答>\n{_expectation(case)}\n</基準の回答>"
                                    f"\n\n<回答>\n{answer_text}\n</回答>"},
    ]
    try:
        text = llm.complete(messages, feature="judge", tenant_id=tenant_id, client=client, max_tokens=120)
    except llm.LLMNotAllowed as exc:
        return {"verdict": "error", "reason": f"not_allowed:{exc.decision.layer}"}
    except Exception as exc:  # noqa: BLE001
        return {"verdict": "error", "reason": f"error:{type(exc).__name__}"}
    try:
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
    except ValueError:
        return {"verdict": "error", "reason": "bad_output"}
    verdict = data.get("verdict") if isinstance(data, dict) else None
    if verdict not in {"pass", "fail"}:
        return {"verdict": "error", "reason": "bad_output"}
    return {"verdict": verdict, "reason": str(data.get("reason") or "")[:300]}


def judge_rows(
    cases: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], *, tenant_id: str = "default", client: Any = None
) -> Dict[str, Dict[str, str]]:
    """Judge the rows that have an answer text (tenant_eval.runner rows): case_id -> judgement."""
    by_id = {case["case_id"]: case for case in cases}
    out: Dict[str, Dict[str, str]] = {}
    for row in rows:
        case = by_id.get(row["case_id"])
        if case is None or not row.get("answered"):
            continue
        out[row["case_id"]] = judge_case(case, str(row.get("answer_text") or ""), tenant_id=tenant_id, client=client)
    return out


def agreement(human: Mapping[str, bool], judged: Mapping[str, Mapping[str, str]], *, alpha: float = 0.05) -> Dict[str, Any]:
    """How well the judge agrees with a person over the cases both scored (an "error" verdict is counted, not scored)."""
    errors = sum(1 for j in judged.values() if j.get("verdict") not in {"pass", "fail"})
    ids = sorted(i for i in human if judged.get(i, {}).get("verdict") in {"pass", "fail"})
    tp = sum(1 for i in ids if human[i] and judged[i]["verdict"] == "pass")
    fp = sum(1 for i in ids if not human[i] and judged[i]["verdict"] == "pass")
    fn = sum(1 for i in ids if human[i] and judged[i]["verdict"] == "fail")
    tn = sum(1 for i in ids if not human[i] and judged[i]["verdict"] == "fail")
    n = len(ids)
    if n == 0:
        return {"n": 0, "errors": errors, "accuracy": None, "kappa": None, "confusion": {"tp": 0, "fp": 0, "fn": 0, "tn": 0},
                "false_pass_rate": None, "false_pass_upper": None}  # nosec B105 - rates, not credentials
    observed = (tp + tn) / n
    expected = ((tp + fn) * (tp + fp) + (fp + tn) * (fn + tn)) / (n * n)
    kappa = 1.0 if expected == 1 else (observed - expected) / (1 - expected)
    human_fail = fp + tn
    return {
        "n": n, "errors": errors, "accuracy": observed, "kappa": kappa,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "false_pass_rate": fp / human_fail if human_fail else None,
        "false_pass_upper": clopper_pearson_upper(fp, human_fail, alpha) if human_fail else None,
    }


def usable(
    report: Mapping[str, Any], *, min_cases: int = MIN_CASES, min_kappa: float = MIN_KAPPA,
    max_false_pass_upper: float = MAX_FALSE_PASS_UPPER,
) -> Dict[str, Any]:
    """Whether the judge may be used: enough cases, enough agreement, and a bounded false-pass rate."""
    reasons = []
    if report["n"] < min_cases:
        reasons.append(f"{report['n']} cases scored by both (at least {min_cases} needed)")
    if report["kappa"] is None or report["kappa"] < min_kappa:
        reasons.append(f"kappa {report['kappa']} is below {min_kappa}")
    upper = report["false_pass_upper"]
    if upper is None or upper > max_false_pass_upper:
        reasons.append(f"false-pass upper bound {upper} is not within {max_false_pass_upper}")
    return {"usable": not reasons, "reasons": reasons}


def conservative_pass(rule_passed: bool, verdict: Optional[str], judge_usable: bool) -> bool:
    """The rule-based result, which a usable judge may only veto."""
    if not rule_passed:
        return False
    return not (judge_usable and verdict == "fail")
