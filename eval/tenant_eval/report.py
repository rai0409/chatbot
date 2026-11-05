"""A Markdown report of one evaluation (the gate result, the rows of both runs, the conditions)."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from eval.tenant_eval.conditions import compare_conditions
from eval.tenant_eval.metrics import by_slice

_DECISIONS = {"pass": "合格（採用可）", "fail": "不合格", "insufficient": "判定不能（件数が足りない）"}  # nosec B105
_STATUS = {"pass": "合格", "fail": "不合格", "insufficient": "判定不能", "not_applicable": "対象外"}  # nosec B105
_CHECKS = {
    "overall_non_inferiority": "全体の合格率が悪化していない（非劣性）",
    "slice_regression": "質問タイプ別に有意な悪化がない",
    "protected_cases": "保護ケースが悪化していない",
    "false_accept": "誤受理率の上限",
    "errors": "新規エラーがない",
}
_REASONS = {
    "missing_all": "必須語が足りない", "missing_any": "期待語が 1 つもない", "forbidden": "禁止語を含む",
    "no_citation": "出典がない", "question_type": "質問タイプが違う", "error": "実行エラー",
}


def _pct(value: Optional[float]) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _detail(check: Mapping[str, Any]) -> str:
    name = check["name"]
    if name == "overall_non_inferiority":
        if check["status"] == "insufficient":
            return f"{check['n']} 件（{check['needed']} 件以上必要）"
        return f"差 {check['diff'] * 100:+.1f} 点、下限 {check['low'] * 100:+.1f} 点、許容 -{check['margin'] * 100:.1f} 点"
    if name == "slice_regression":
        regressed = "、".join(check.get("regressed") or []) or "なし"
        skipped = "、".join(f"{k}({v}件)" for k, v in (check.get("not_judged") or {}).items()) or "なし"
        return f"悪化: {regressed} / 件数不足で未判定: {skipped}"
    if name == "protected_cases":
        return "悪化: " + ("、".join(check["regressed"]) or "なし")
    if name == "false_accept":
        return (f"基準 {check['baseline']} 件 → 候補 {check['candidate']} 件（{check['n']} 件中）、"
                f"上限 {_pct(check['upper'])}、基準値 {_pct(check['limit'])}")
    if name == "errors":
        return "新規: " + ("、".join(check["new"]) or "なし")
    return ""


def to_markdown(
    gate: Mapping[str, Any],
    *,
    title: str = "評価レポート",
    baseline_rows: Optional[Sequence[Dict[str, Any]]] = None,
    candidate_rows: Optional[Sequence[Dict[str, Any]]] = None,
    baseline_conditions: Optional[Mapping[str, Any]] = None,
    candidate_conditions: Optional[Mapping[str, Any]] = None,
) -> str:
    lines: List[str] = [f"# {title}", "", f"**判定: {_DECISIONS.get(gate['decision'], gate['decision'])}**", ""]
    base, cand = gate["baseline"], gate["candidate"]
    lines += [
        "## 結果", "",
        "| | 件数 | 合格率 | 回答 | 棄権 | 誤受理 | 誤受理率の上限 | エラー |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for label, s in (("基準", base), ("候補", cand)):
        lines.append(
            f"| {label} | {s['n']} | {_pct(s['pass_rate'])} | {s['answered']} | {s['abstained']} | "
            f"{s['false_accepts']} | {_pct(s['false_accept_upper'])} | {s['errors']} |"
        )
    if baseline_conditions is not None and candidate_conditions is not None:
        changed = compare_conditions(baseline_conditions, candidate_conditions)
        lines += ["", "## 比較した条件（基準と候補で違うもの）", ""]
        if changed:
            lines += ["| 条件 | 基準 | 候補 |", "|---|---|---|"]
            lines += [f"| {key} | {v['baseline']} | {v['candidate']} |" for key, v in changed.items()]
        else:
            lines.append("違いはない（同じ条件で 2 回実行した）。")
        lines.append(f"\nプロファイル版: 基準 `{baseline_conditions.get('profile_version') or '-'}` / "
                     f"候補 `{candidate_conditions.get('profile_version') or '-'}`")
    lines += ["", "## チェック", "", "| チェック | 結果 | 内容 |", "|---|---|---|"]
    for check in gate["checks"]:
        lines.append(f"| {_CHECKS.get(check['name'], check['name'])} | {_STATUS.get(check['status'], check['status'])} | {_detail(check)} |")
    if baseline_rows is not None and candidate_rows is not None:
        b, c = by_slice(baseline_rows), by_slice(candidate_rows)
        lines += ["", "## 質問タイプ別", "", "| タイプ | 件数 | 基準の合格率 | 候補の合格率 |", "|---|---|---|---|"]
        lines += [f"| {name} | {b[name]['n']} | {_pct(b[name]['pass_rate'])} | {_pct(c[name]['pass_rate'])} |" for name in b]
        cand_by_id = {row["case_id"]: row for row in candidate_rows}
        for heading, ids in (("悪化したケース", gate["changed"]["worse"]), ("改善したケース", gate["changed"]["better"])):
            lines += ["", f"## {heading}（{len(ids)} 件）", ""]
            if not ids:
                lines.append("なし")
            for case_id in ids:
                reasons = "、".join(_REASONS.get(f, f) for f in cand_by_id[case_id].get("failures", [])) or "-"
                lines.append(f"- `{case_id}`" + (f": {reasons}" if heading == "悪化したケース" else ""))
    policy = gate["policy"]
    lines += ["", "## 基準値（初期値。実データで調整する）", "",
              f"許容低下 {policy['margin'] * 100:.1f} 点 / 全体の最小件数 {policy['min_cases']} / "
              f"タイプ別の最小件数 {policy['min_slice']} / 誤受理率の上限 {policy['false_accept_limit'] * 100:.1f}% / "
              f"保護ケースの優先度 {policy['protected_priority']} 以上", ""]
    return "\n".join(lines)
