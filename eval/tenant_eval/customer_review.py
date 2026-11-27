"""Customer review of the cases a candidate changed: export a CSV, import the ratings, aggregate them.

1. export_review_csv(): the improved and worsened cases of a gate result (plus, optionally, a seeded sample of
   unchanged cases so the accepted rate is not only measured on changes), with the question and both answers.
   UTF-8 with BOM (Excel opens it as UTF-8), CRLF, and every cell that a spreadsheet would run as a formula
   (= + - @ tab CR, also their full-width forms) is prefixed with an apostrophe (CSV injection).
2. The customer fills in rating (1-5), decision (accept / reject) and comment per row and returns the file.
3. import_review_csv() reads it back (rows with invalid values are reported, not guessed);
   aggregate_reviews() gives the subjective numbers and the combined metric of docs/operations/benchmark_governance.md:
   customer-accepted correct answer rate = reviewed cases that pass the numeric check AND are accepted by the
   reviewer, over the reviewed cases, with a Clopper-Pearson 95% interval.

The file holds questions and answers of the tenant: keep it inside the tenant's evaluation folder (never in a
shared or temporary location) and move it to 即削除/ after sign-off.
"""
from __future__ import annotations

import csv
import io
import random
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from eval.tenant_eval.stats import clopper_pearson_lower, clopper_pearson_upper

COLUMNS = (
    "case_id", "change", "question", "baseline_answer", "candidate_answer", "baseline_passed", "candidate_passed",
    "rating", "decision", "comment", "reviewer",
)
CHANGE_LABELS = {"better": "改善", "worse": "悪化", "unchanged": "変化なし"}
DECISIONS = {"accept": "accept", "reject": "reject", "採用": "accept", "不採用": "reject", "ok": "accept", "ng": "reject"}
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "＝", "＋", "－", "＠")
ANSWER_LIMIT = 2000


def safe_cell(value: Any) -> str:
    """The cell text with an apostrophe in front when a spreadsheet would read it as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def _unescape(value: str) -> str:
    text = (value or "").strip()
    return text[1:] if text.startswith("'") and text[1:].startswith(_FORMULA_START) else text


def review_rows(
    gate: Mapping[str, Any],
    baseline_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
    cases: Sequence[Mapping[str, Any]],
    *,
    sample_unchanged: int = 0,
    seed: int = 0,
) -> List[Dict[str, Any]]:
    questions = {str(c["case_id"]): str(c.get("question") or "") for c in cases}
    base = {str(r["case_id"]): r for r in baseline_rows}
    cand = {str(r["case_id"]): r for r in candidate_rows}
    picked: List[Tuple[str, str]] = [(cid, "worse") for cid in gate["changed"]["worse"]]
    picked += [(cid, "better") for cid in gate["changed"]["better"]]
    if sample_unchanged > 0:
        changed = {cid for cid, _ in picked}
        unchanged = sorted(cid for cid in cand if cid not in changed)
        rng = random.Random(seed)
        picked += [(cid, "unchanged") for cid in sorted(rng.sample(unchanged, min(sample_unchanged, len(unchanged))))]
    rows = []
    for case_id, change in picked:
        b, c = base.get(case_id, {}), cand.get(case_id, {})
        rows.append({
            "case_id": case_id,
            "change": CHANGE_LABELS[change],
            "question": questions.get(case_id, ""),
            "baseline_answer": str(b.get("answer_text") or "")[:ANSWER_LIMIT],
            "candidate_answer": str(c.get("answer_text") or "")[:ANSWER_LIMIT],
            "baseline_passed": "1" if b.get("passed") else "0",
            "candidate_passed": "1" if c.get("passed") else "0",
            "rating": "", "decision": "", "comment": "", "reviewer": "",
        })
    return rows


def export_review_csv(path: str | Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([safe_cell(row.get(col, "")) for col in COLUMNS])
    path.write_bytes(buffer.getvalue().encode("utf-8-sig"))
    return path


def import_review_csv(path: str | Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """(ratings, problems). A row with an invalid rating or decision is reported in problems and left out."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp932")  # re-saved by Excel as Shift_JIS
    reader = csv.DictReader(io.StringIO(text, newline=""))
    missing = [col for col in ("case_id", "rating", "decision") if col not in (reader.fieldnames or [])]
    if missing:
        return [], [f"missing columns: {missing}"]
    ratings: List[Dict[str, Any]] = []
    problems: List[str] = []
    seen = set()
    for line_no, row in enumerate(reader, start=2):
        case_id = _unescape(row.get("case_id") or "")
        rating_text = _unescape(row.get("rating") or "")
        decision_text = _unescape(row.get("decision") or "").lower()
        if not case_id:
            problems.append(f"line {line_no}: no case_id")
            continue
        if not rating_text and not decision_text:
            continue  # not reviewed
        rating: Optional[int] = None
        if rating_text:
            try:
                number = float(rating_text.translate(str.maketrans("０１２３４５", "012345")))
                # "3.7" / "inf" / "nan" are not ratings: never truncate or crash, report the row
                rating = int(number) if number.is_integer() else None
            except (ValueError, OverflowError):
                rating = None
            if rating is None or not 1 <= rating <= 5:
                problems.append(f"line {line_no} ({case_id}): rating must be 1-5")
                continue
        decision = DECISIONS.get(decision_text) if decision_text else None
        if decision_text and decision is None:
            problems.append(f"line {line_no} ({case_id}): decision must be accept or reject")
            continue
        if case_id in seen:
            problems.append(f"line {line_no} ({case_id}): duplicate case_id")
            continue
        seen.add(case_id)
        ratings.append({
            "case_id": case_id, "rating": rating, "decision": decision, "change": _unescape(row.get("change") or ""),
            "comment": _unescape(row.get("comment") or ""), "reviewer": _unescape(row.get("reviewer") or ""),
        })
    return ratings, problems


def aggregate_reviews(
    ratings: Sequence[Mapping[str, Any]], candidate_rows: Sequence[Mapping[str, Any]], *, alpha: float = 0.05
) -> Dict[str, Any]:
    passed = {str(r["case_id"]): bool(r.get("passed")) for r in candidate_rows}
    reviewed = [r for r in ratings if r["case_id"] in passed]
    unknown = [r["case_id"] for r in ratings if r["case_id"] not in passed]
    rated = [r["rating"] for r in reviewed if r.get("rating") is not None]
    decided = [r for r in reviewed if r.get("decision")]
    accepted = sum(1 for r in decided if r["decision"] == "accept")
    accepted_correct = sum(1 for r in decided if r["decision"] == "accept" and passed[r["case_id"]])
    n = len(decided)

    def interval(k: int) -> Dict[str, Optional[float]]:
        if not n:
            return {"rate": None, "low": None, "high": None}
        return {"rate": k / n, "low": clopper_pearson_lower(k, n, alpha / 2), "high": clopper_pearson_upper(k, n, alpha / 2)}

    by_change: Dict[str, Dict[str, Any]] = {}
    for r in reviewed:
        part = by_change.setdefault(r.get("change") or "-", {"n": 0, "accepted": 0, "ratings": []})
        part["n"] += 1
        part["accepted"] += 1 if r.get("decision") == "accept" else 0
        if r.get("rating") is not None:
            part["ratings"].append(r["rating"])
    for part in by_change.values():
        values = part.pop("ratings")
        part["mean_rating"] = sum(values) / len(values) if values else None
    return {
        "reviewed": len(reviewed),
        "decided": n,
        "unknown_case_ids": unknown,
        "mean_rating": sum(rated) / len(rated) if rated else None,
        "rating_histogram": {str(k): rated.count(k) for k in range(1, 6)},
        "accepted": accepted,
        "rejected": n - accepted,
        "accept_rate": interval(accepted),
        "customer_accepted_correct_rate": interval(accepted_correct),
        "accepted_but_numerically_failed": sorted(r["case_id"] for r in decided if r["decision"] == "accept" and not passed[r["case_id"]]),
        "rejected_but_numerically_passed": sorted(r["case_id"] for r in decided if r["decision"] == "reject" and passed[r["case_id"]]),
        "by_change": by_change,
    }


def _load_evaluation(path: str | Path) -> Dict[str, Any]:
    """An evaluation saved as JSON: {"gate", "baseline_rows", "candidate_rows", "cases"} (evaluate_candidate output + cases)."""
    import json

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("gate", "baseline_rows", "candidate_rows", "cases"):
        if key not in data:
            raise ValueError(f"{path}: '{key}' is missing")
    return data


def main(argv: Optional[Sequence[str]] = None) -> int:
    """python -m eval.tenant_eval.customer_review export|import ..."""
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="python -m eval.tenant_eval.customer_review", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("export", help="CSV of the improved / worsened cases for the customer")
    ex.add_argument("--evaluation", required=True, help="JSON with gate, baseline_rows, candidate_rows, cases")
    ex.add_argument("--out", required=True)
    ex.add_argument("--sample-unchanged", type=int, default=0)
    ex.add_argument("--seed", type=int, default=0)
    im = sub.add_parser("import", help="read the returned CSV and aggregate it")
    im.add_argument("--evaluation", required=True)
    im.add_argument("--csv", required=True)
    im.add_argument("--out", default=None, help="aggregate JSON (default: stdout)")
    args = parser.parse_args(argv)
    data = _load_evaluation(args.evaluation)
    if args.command == "export":
        rows = review_rows(data["gate"], data["baseline_rows"], data["candidate_rows"], data["cases"],
                           sample_unchanged=args.sample_unchanged, seed=args.seed)
        export_review_csv(args.out, rows)
        print(json.dumps({"rows": len(rows), "out": args.out}, ensure_ascii=False))
        return 0
    ratings, problems = import_review_csv(args.csv)
    result = {"aggregate": aggregate_reviews(ratings, data["candidate_rows"]), "problems": problems}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
