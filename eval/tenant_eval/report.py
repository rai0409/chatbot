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
    "latency_p95": "応答時間 p95 が予算内",
    "retrieval_ndcg": "検索精度 nDCG@10 が悪化していない（非劣性）",
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
    if name == "latency_p95":
        if check["status"] == "not_applicable":
            return "予算未設定"
        if check["status"] == "insufficient":
            return "応答時間の記録がない"
        return (f"基準 {_num(check.get('baseline_ms'), 0)} ms → 候補 {_num(check['candidate_ms'], 0)} ms"
                f"（予算 {_num(check['budget_ms'], 0)} ms）")
    if name == "retrieval_ndcg":
        if check["status"] == "not_applicable":
            return "正解の文書 ID を持つケースがない"
        if check["status"] == "insufficient":
            return f"{check['n']} 件（{check['needed']} 件以上必要）"
        return f"差 {check['diff']:+.3f}、下限 {check['low']:+.3f}、許容 -{check['margin']:.3f}"
    return ""


def _num(value: Optional[float], digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _measurement_lines(base: Mapping[str, Any], cand: Mapping[str, Any]) -> List[str]:
    """Retrieval metrics, latency and the abstention confusion matrix (sections only when the data exists)."""
    lines: List[str] = []
    b_ret, c_ret = base.get("retrieval"), cand.get("retrieval")
    if b_ret or c_ret:
        lines += ["", "## 検索精度（正解 ID を持つケース）", "", "| 指標 | 基準 | 候補 |", "|---|---|---|"]
        for metric in ("ndcg@10", "recall@20", "mrr@10", "hit@1"):
            def cell(summary):
                value = (summary or {}).get(metric)
                return "-" if not value else f"{value['mean']:.3f} [{value['low']:.3f}, {value['high']:.3f}]"
            lines.append(f"| {metric} | {cell(b_ret)} | {cell(c_ret)} |")
        lines.append(f"\n件数: 基準 {(b_ret or {}).get('n', 0)} / 候補 {(c_ret or {}).get('n', 0)}（平均と 95% 信頼区間）")
    b_cit, c_cit = base.get("citation_precision"), cand.get("citation_precision")
    if b_cit or c_cit:
        lines.append(f"\n出典の適合率（引用のうち正解チャンクを指す割合）: 基準 {_num((b_cit or {}).get('mean'))} / "
                     f"候補 {_num((c_cit or {}).get('mean'))}")
    if base.get("latency_ms") or cand.get("latency_ms"):
        lines += ["", "## 応答時間", "", "| 段階 | 基準 p50 | 基準 p95 | 候補 p50 | 候補 p95 |", "|---|---|---|---|---|"]
        stages = {"total": (base.get("latency_ms"), cand.get("latency_ms"))}
        for stage in sorted(set(base.get("stage_latency_ms") or {}) | set(cand.get("stage_latency_ms") or {})):
            stages[stage] = ((base.get("stage_latency_ms") or {}).get(stage), (cand.get("stage_latency_ms") or {}).get(stage))
        for stage, (b, c) in stages.items():
            lines.append(f"| {stage} | {_num((b or {}).get('p50_ms'), 0)} | {_num((b or {}).get('p95_ms'), 0)} | "
                         f"{_num((c or {}).get('p50_ms'), 0)} | {_num((c or {}).get('p95_ms'), 0)} |")
    b_abs, c_abs = base.get("abstention"), cand.get("abstention")
    if b_abs or c_abs:
        lines += ["", "## 棄権の混同行列（棄権すべきか × 棄権したか）", "",
                  "| | 棄権すべき・棄権した | 棄権すべき・回答した | 回答すべき・棄権した | 回答すべき・回答した | 適合率 | 再現率 |",
                  "|---|---|---|---|---|---|---|"]
        for label, a in (("基準", b_abs), ("候補", c_abs)):
            if a:
                lines.append(f"| {label} | {a['should_abstain_and_abstained']} | {a['should_abstain_but_answered']} | "
                             f"{a['should_answer_but_abstained']} | {a['should_answer_and_answered']} | "
                             f"{_pct(a['precision'])} | {_pct(a['recall'])} |")
    return lines


def customer_subjective_lines(
    review: Optional[Mapping[str, Any]], feedback: Optional[Mapping[str, Any]] = None
) -> List[str]:
    """Section "customer subjective": the reviewer's ratings of changed cases and the chat feedback (feedback.aggregate)."""
    lines: List[str] = ["", "## 顧客の主観評価（customer subjective）", ""]
    if review:
        combined = review["customer_accepted_correct_rate"]
        accept = review["accept_rate"]
        lines += [
            f"- 顧客採用かつ正解の率（customer-accepted correct answer rate）: **{_pct(combined['rate'])}** "
            f"（95% 区間 {_pct(combined['low'])} - {_pct(combined['high'])}、判定 {review['decided']} 件）",
            f"- 採用率: {_pct(accept['rate'])}（{review['accepted']} 採用 / {review['rejected']} 不採用）",
            f"- 平均評価（1-5）: {_num(review['mean_rating'], 2)}　分布: "
            + " ".join(f"{k}:{v}" for k, v in review["rating_histogram"].items()),
            f"- 数値では不合格だが顧客は採用: {len(review['accepted_but_numerically_failed'])} 件 / "
            f"数値では合格だが顧客は不採用: {len(review['rejected_but_numerically_passed'])} 件（判定基準の見直し候補）",
        ]
        if review.get("by_change"):
            lines += ["", "| 変化 | 件数 | 採用 | 平均評価 |", "|---|---|---|---|"]
            lines += [f"| {name} | {part['n']} | {part['accepted']} | {_num(part['mean_rating'], 2)} |"
                      for name, part in sorted(review["by_change"].items())]
    else:
        lines.append("顧客レビューは未実施。")
    if feedback and feedback.get("tenants"):
        lines += ["", "チャット画面のフィードバック（評価期間）:", "", "| テナント | 良い | 悪い | 悪い率 | 95% 区間 |", "|---|---|---|---|---|"]
        for tenant, row in sorted(feedback["tenants"].items()):
            lines.append(f"| {tenant} | {row['good']} | {row['bad']} | {_pct(row['bad_rate'])} | "
                         f"{_pct(row['bad_rate_low'])} - {_pct(row['bad_rate_high'])} |")
        if feedback.get("observations"):
            lines.append("\n多い理由: " + "、".join(f"{o['tenant_id']}/{o['reason']} {o['count']} 件" for o in feedback["observations"][:5]))
    return lines


def to_markdown(
    gate: Mapping[str, Any],
    *,
    title: str = "評価レポート",
    baseline_rows: Optional[Sequence[Dict[str, Any]]] = None,
    candidate_rows: Optional[Sequence[Dict[str, Any]]] = None,
    baseline_conditions: Optional[Mapping[str, Any]] = None,
    candidate_conditions: Optional[Mapping[str, Any]] = None,
    customer_review: Optional[Mapping[str, Any]] = None,
    feedback: Optional[Mapping[str, Any]] = None,
) -> str:
    """customer_review: customer_review.aggregate_reviews(); feedback: feedback.aggregate() of the evaluation period."""
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
    lines += _measurement_lines(base, cand)
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
    if customer_review is not None or feedback is not None:
        lines += customer_subjective_lines(customer_review, feedback)
    policy = gate["policy"]
    lines += ["", "## 基準値（初期値。実データで調整する）", "",
              f"許容低下 {policy['margin'] * 100:.1f} 点 / 全体の最小件数 {policy['min_cases']} / "
              f"タイプ別の最小件数 {policy['min_slice']} / 誤受理率の上限 {policy['false_accept_limit'] * 100:.1f}% / "
              f"保護ケースの優先度 {policy['protected_priority']} 以上", ""]
    return "\n".join(lines)
