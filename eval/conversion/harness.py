"""Run the converters over a fixture directory and score them (conversion-run-v1).

Truth file: <document>.truth.json next to the document, schema
"conversion-truth-v1":

    {
      "schema": "conversion-truth-v1",
      "document": "policy.docx",            # file name in the same directory
      "format": "docx",                     # csv | xlsx | docx | pptx | pdf
      "text_blocks": ["見出し", "段落", ...],  # non-table text in reading order
      "headings": ["経費精算規程", ...],       # headings / slide titles / table titles
      "tables": [{"cells": [[...], ...],      # as the reader sees it, merged cells
                  "merged": [{"row": 0, "col": 1, "rowspan": 1, "colspan": 2}],
                  "header_rows": 2,           # rows that form the column header
                  "title_rows": 0}],          # rows above the header (not data)
      "typed_cells": [{"header": "金額", "value": "12,000円", "kind": "number"}],
      "header_footer": ["株式会社サンプル 社内規程集"],   # must not leak into body chunks
      "excluded_text": ["ヤマダタロウ"],                # must not appear anywhere (furigana ...)
      "expected_warnings": ["formula_without_cached_value"],
      "expected_encoding": "utf-16-le"                   # csv only, optional
    }

Every list is optional; a metric whose truth is missing is reported as null
and left out of the aggregates.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess  # nosec B404 - fixed git commands only
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from eval.conversion import metrics as M

RUN_SCHEMA = "conversion-run-v1"
TRUTH_SCHEMA = "conversion-truth-v1"
COMPARE_SCHEMA = "conversion-compare-v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
TABLE_CHUNK_TYPES = {"row", "table_row", "qa_pair"}
NON_BODY_CHUNK_TYPES = {"header_footer"}

# metric path -> True when higher is better
METRIC_DIRECTIONS: Dict[str, bool] = {
    "text.cer": False,
    "text.normalized_levenshtein_similarity": True,
    "text.char_accuracy": True,
    "reading_order.kendall_tau": True,
    "table.precision": True,
    "table.recall": True,
    "table.f1": True,
    "table.value_only_f1": True,
    "headings.precision": True,
    "headings.recall": True,
    "headings.f1": True,
    "typed_cells.exact_match_rate": True,
    "header_footer_leakage.rate": False,
    "excluded_text_leakage.rate": False,
    "warnings.recall": True,
    "encoding.correct": True,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(args: Sequence[str], cwd: Path) -> Optional[str]:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True, timeout=20)  # nosec B603 B607
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):  # not a checkout / git missing / timeout (provenance is optional)
        return None


def converter_versions(code_root: Path) -> Dict[str, Any]:
    """sha256 (first 16 hex) of every converter module: the converters carry no version strings."""
    package = code_root / "rag_core" / "document_converters"
    files = {p.name: sha256_file(p)[:16] for p in sorted(package.glob("*.py"))}
    combined = hashlib.sha256("".join(f"{k}:{v}\n" for k, v in files.items()).encode()).hexdigest()[:16]
    return {"package": "rag_core.document_converters", "combined_sha256_16": combined, "modules": files}


# --- reconstruction of what a converter produced ---------------------------------------------------------


def linear_text(chunks: Sequence[Dict[str, Any]]) -> str:
    """Non-table body text in chunk order, headings re-inserted where the section path changes."""
    lines: List[str] = []
    previous_path: List[str] = []
    for chunk in chunks:
        kind = chunk.get("chunk_type")
        if kind in NON_BODY_CHUNK_TYPES:
            continue
        # Table rows add no text (scored as cells) but a heading that only
        # precedes a table still opens a section in the reading flow.
        path = [str(p) for p in (chunk.get("section_path") or [])]
        common = 0
        while common < min(len(path), len(previous_path)) and path[common] == previous_path[common]:
            common += 1
        lines.extend(path[common:])
        previous_path = path
        if kind not in TABLE_CHUNK_TYPES:
            lines.append(M.strip_labels(str(chunk.get("text") or "")))
    return "\n".join(lines)


def actual_headings(chunks: Sequence[Dict[str, Any]]) -> List[str]:
    found: Dict[str, None] = {}  # insertion-ordered set
    for chunk in chunks:
        for value in list(chunk.get("section_path") or []) + list(chunk.get("heading_path") or []) + [chunk.get("slide_title")]:
            if value:
                found.setdefault(str(value))
    return list(found)


def actual_warnings(chunks: Sequence[Dict[str, Any]]) -> List[str]:
    codes: Dict[str, None] = {}  # insertion-ordered set
    for chunk in chunks:
        for warning in chunk.get("conversion_warnings") or []:
            code = warning.get("code") if isinstance(warning, dict) else str(warning)
            if code:
                codes.setdefault(str(code))
    return list(codes)


def score_document(truth: Dict[str, Any], chunks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    body = [c for c in chunks if c.get("chunk_type") not in NON_BODY_CHUNK_TYPES]
    table_chunks = [c for c in chunks if c.get("chunk_type") in TABLE_CHUNK_TYPES]
    text = linear_text(chunks)
    blocks = list(truth.get("text_blocks") or [])
    out: Dict[str, Any] = {"chunks": len(chunks), "table_chunks": len(table_chunks)}
    out["text"] = M.text_scores("\n".join(blocks), text) if blocks else None
    out["reading_order"] = M.reading_order(blocks, text) if len(blocks) >= 2 else None
    out["table"] = M.table_scores(truth["tables"], table_chunks) if truth.get("tables") else None
    out["headings"] = M.heading_scores(truth["headings"], actual_headings(chunks)) if truth.get("headings") else None
    out["typed_cells"] = M.typed_cell_scores(truth["typed_cells"], table_chunks) if truth.get("typed_cells") else None
    out["header_footer_leakage"] = M.leakage(truth["header_footer"], body) if truth.get("header_footer") else None
    out["excluded_text_leakage"] = M.leakage(truth["excluded_text"], list(chunks)) if truth.get("excluded_text") else None
    if truth.get("expected_warnings"):
        got = actual_warnings(chunks)
        want = list(truth["expected_warnings"])
        out["warnings"] = {"recall": round(sum(w in got for w in want) / len(want), 6), "expected": want, "actual": got}
    else:
        out["warnings"] = None
    if truth.get("expected_encoding"):
        got_enc = next((c.get("source_encoding") for c in chunks if c.get("source_encoding")), None)
        out["encoding"] = {"correct": float(str(got_enc).lower() == str(truth["expected_encoding"]).lower()),
                           "expected": truth["expected_encoding"], "actual": got_enc}
    else:
        out["encoding"] = None
    return out


def flatten(doc_metrics: Dict[str, Any]) -> Dict[str, float]:
    flat: Dict[str, float] = {}
    for path in METRIC_DIRECTIONS:
        group, key = path.split(".", 1)
        section = doc_metrics.get(group)
        if isinstance(section, dict) and isinstance(section.get(key), (int, float)):
            flat[path] = float(section[key])
    return flat


def _mean(values: Iterable[float]) -> Optional[float]:
    values = list(values)
    return round(sum(values) / len(values), 6) if values else None


def aggregate(documents: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    groups: Dict[str, List[Dict[str, float]]] = {"overall": []}
    for doc in documents:
        if doc.get("error"):
            continue
        flat = flatten(doc["metrics"])
        groups["overall"].append(flat)
        groups.setdefault(f"format:{doc['format']}", []).append(flat)
    out: Dict[str, Any] = {}
    for name, rows in groups.items():
        out[name] = {"documents": len(rows)}
        for path in METRIC_DIRECTIONS:
            mean = _mean(r[path] for r in rows if path in r)
            if mean is not None:
                out[name][path] = mean
    out["overall"]["errors"] = sum(1 for d in documents if d.get("error"))
    return out


# --- run ---------------------------------------------------------------------------------------------------


def load_truths(fixtures: Path) -> List[Dict[str, Any]]:
    truths = []
    for path in sorted(fixtures.glob("*.truth.json")):
        truth = json.loads(path.read_text(encoding="utf-8"))
        if truth.get("schema") != TRUTH_SCHEMA:
            raise ValueError(f"{path.name}: schema must be {TRUTH_SCHEMA}")
        truth["_truth_path"] = path
        truths.append(truth)
    if not truths:
        raise ValueError(f"no *.truth.json in {fixtures}")
    return truths


def run(fixtures: Path, *, code_root: Optional[Path] = None) -> Dict[str, Any]:
    """Convert every document that has a truth file and score it."""
    fixtures = Path(fixtures).resolve()
    code_root = Path(code_root or REPO_ROOT).resolve()
    if not (code_root / "rag_core" / "document_converters").is_dir():
        raise ValueError(f"{code_root} has no rag_core/document_converters")
    if str(code_root) not in sys.path:
        sys.path.insert(0, str(code_root))
    import rag_core.document_converters as package
    from rag_core.document_converters import convert_file_to_canonical_chunks

    loaded_from = Path(package.__file__).resolve().parents[2]
    if loaded_from != code_root:
        raise RuntimeError(f"rag_core was imported from {loaded_from}, not from {code_root}: run --root in a fresh process")

    documents: List[Dict[str, Any]] = []
    fixture_hashes: Dict[str, str] = {}
    for truth in load_truths(fixtures):
        doc_path = fixtures / truth["document"]
        truth_path: Path = truth.pop("_truth_path")
        fixture_hashes[doc_path.name] = sha256_file(doc_path)
        fixture_hashes[truth_path.name] = sha256_file(truth_path)
        entry: Dict[str, Any] = {"document": doc_path.name, "format": truth.get("format") or doc_path.suffix.lstrip(".")}
        try:
            chunks = convert_file_to_canonical_chunks(doc_path, tenant_id="default", source_doc=doc_path.name)
            entry["metrics"] = score_document(truth, chunks)
        except Exception as exc:  # noqa: BLE001 - a failing converter is a result, not a crash
            entry["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        documents.append(entry)
    combined = hashlib.sha256("".join(f"{k}:{v}\n" for k, v in sorted(fixture_hashes.items())).encode()).hexdigest()
    try:
        fixtures_label = str(fixtures.relative_to(REPO_ROOT))
    except ValueError:
        fixtures_label = str(fixtures)
    return {
        "schema": RUN_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git(["rev-parse", "HEAD"], code_root),
        "git_dirty_converters": bool(_git(["status", "--porcelain", "--", "rag_core/document_converters"], code_root)),
        "python": platform.python_version(),
        "code_root": "repo" if code_root == REPO_ROOT else str(code_root),
        "converter_versions": converter_versions(code_root),
        "fixtures_dir": fixtures_label,
        "fixtures_sha256": combined,
        "fixture_files": fixture_hashes,
        "documents": documents,
        "aggregate": aggregate(documents),
    }


# --- thresholds ---------------------------------------------------------------------------------------------


def check_thresholds(result: Dict[str, Any], thresholds: Dict[str, Any]) -> List[str]:
    """{"min": {metric: v}, "max": {metric: v}, "per_document": {name: {"min": ..., "max": ...}}}."""
    violations: List[str] = []
    # A typo'd metric name or document name would otherwise make a threshold silently inert.
    names = {d["document"] for d in result["documents"]}
    for rule_name, rule in [("global", thresholds), *sorted((thresholds.get("per_document") or {}).items())]:
        for bound_kind in ("min", "max"):
            for path in (rule.get(bound_kind) or {}):
                if path not in METRIC_DIRECTIONS:
                    violations.append(f"thresholds[{rule_name}].{bound_kind}: unknown metric '{path}'")
        if rule_name not in ("global", *names):
            violations.append(f"thresholds.per_document: document '{rule_name}' was not run")
    for doc in result["documents"]:
        if doc.get("error"):
            violations.append(f"{doc['document']}: {doc['error']}")
            continue
        flat = flatten(doc["metrics"])
        rules = [thresholds, (thresholds.get("per_document") or {}).get(doc["document"], {})]
        for rule in rules:
            for path, bound in (rule.get("min") or {}).items():
                if path in flat and flat[path] < bound:
                    violations.append(f"{doc['document']}: {path}={flat[path]:.4f} < {bound}")
            for path, bound in (rule.get("max") or {}).items():
                if path in flat and flat[path] > bound:
                    violations.append(f"{doc['document']}: {path}={flat[path]:.4f} > {bound}")
    return violations


# --- compare ------------------------------------------------------------------------------------------------


def compare(base: Dict[str, Any], candidate: Dict[str, Any], *, tolerance: float = 0.0) -> Dict[str, Any]:
    for run_ in (base, candidate):
        if run_.get("schema") != RUN_SCHEMA:
            raise ValueError(f"not a {RUN_SCHEMA} file")
    same_fixtures = base.get("fixtures_sha256") == candidate.get("fixtures_sha256")
    base_docs = {d["document"]: d for d in base["documents"]}
    rows: List[Dict[str, Any]] = []
    regressions: List[str] = []
    improvements: List[str] = []
    candidate_names = {d["document"] for d in candidate["documents"]}
    regressions.extend(f"{name}: missing from the candidate run" for name in base_docs if name not in candidate_names)
    for doc in candidate["documents"]:
        name = doc["document"]
        old = base_docs.get(name)
        if old is None:
            continue
        if doc.get("error") and not old.get("error"):
            regressions.append(f"{name}: now fails ({doc['error']})")
            continue
        if old.get("error") and not doc.get("error"):
            improvements.append(f"{name}: now converts (was {old['error'][:80]})")
        old_flat = flatten(old.get("metrics") or {}) if not old.get("error") else {}
        new_flat = flatten(doc.get("metrics") or {}) if not doc.get("error") else {}
        for path, higher_better in METRIC_DIRECTIONS.items():
            if path not in new_flat and path not in old_flat:
                continue
            a, b = old_flat.get(path), new_flat.get(path)
            delta = None if a is None or b is None else round(b - a, 6)
            rows.append({"document": name, "metric": path, "base": a, "candidate": b, "delta": delta})
            if delta is None:
                continue
            better = delta > tolerance if higher_better else delta < -tolerance
            worse = delta < -tolerance if higher_better else delta > tolerance
            if worse:
                regressions.append(f"{name}: {path} {a:.4f} -> {b:.4f}")
            elif better:
                improvements.append(f"{name}: {path} {a:.4f} -> {b:.4f}")
    agg_rows = []
    for path in METRIC_DIRECTIONS:
        a = base.get("aggregate", {}).get("overall", {}).get(path)
        b = candidate.get("aggregate", {}).get("overall", {}).get(path)
        if a is not None or b is not None:
            agg_rows.append({"metric": path, "base": a, "candidate": b,
                             "delta": None if a is None or b is None else round(b - a, 6)})
    return {
        "schema": COMPARE_SCHEMA,
        "same_fixtures": same_fixtures,
        "base": {"git_commit": base.get("git_commit"), "converters": base.get("converter_versions", {}).get("combined_sha256_16")},
        "candidate": {"git_commit": candidate.get("git_commit"), "converters": candidate.get("converter_versions", {}).get("combined_sha256_16")},
        "tolerance": tolerance,
        "overall": agg_rows,
        "per_document": rows,
        "regressions": regressions,
        "improvements": improvements,
    }


def markdown(result: Dict[str, Any]) -> str:
    """Short per-document table of a conversion-run-v1 result."""
    cols = ["text.cer", "reading_order.kendall_tau", "table.f1", "headings.recall",
            "typed_cells.exact_match_rate", "header_footer_leakage.rate", "excluded_text_leakage.rate", "warnings.recall"]
    lines = [f"# Conversion accuracy ({result.get('git_commit') or 'no git'}, converters {result['converter_versions']['combined_sha256_16']})", "",
             "| document | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for doc in result["documents"]:
        if doc.get("error"):
            lines.append(f"| {doc['document']} | ERROR: {doc['error']} |")
            continue
        flat = flatten(doc["metrics"])
        lines.append(f"| {doc['document']} | " + " | ".join("" if c not in flat else f"{flat[c]:.3f}" for c in cols) + " |")
    overall = result["aggregate"]["overall"]
    lines.append("| **mean** | " + " | ".join("" if c not in overall else f"{overall[c]:.3f}" for c in cols) + " |")
    return "\n".join(lines) + "\n"
