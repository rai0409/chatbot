"""Frozen general retrieval benchmark (general_v1): deterministic build and validation of the dataset.

The benchmark is described by a MANIFEST.json (eval/bench_data/general_v1/MANIFEST.json). Large public data is
NOT committed: the manifest names each source, how to fetch it (pinned revision), its licence, the sha256 of
the downloaded input, the sampling seed and sizes, and the sha256 the built files must have. build() turns the
inputs into the two files eval.bench reads:

  eval_set.jsonl   one query per line (eval/bench/dataset.py schema), split "dev", match_mode "chunk"
  chunks.jsonl     the corpus (canonical chunk rows: id, doc_id, tenant_id, text)

Source kinds:
  jqara             JQaRA dev Parquet (eval/bench/jqara.py contract). A sample of q_ids (seeded); the corpus is the
                    union of their candidate passages (so this is an open-corpus task, harder than JQaRA's own
                    candidate-limited protocol); relevant = passages with label 1.
  jagovfaqs         JaGovFaqs-22k JSONL (Question / Answer). A seeded sample of FAQ entries is the corpus (the
                    answer text); a seeded subset of their questions are the queries; relevant = its own answer.
  canonical_cases   a committed labelled case file (eval/cases/*_cases.jsonl: query, gold_chunk_ids,
                    expected_abstain) and its chunk file; used for the synthetic multi-format part.

Every id is prefixed with the source name, rows are written sorted with sorted keys, so the same inputs and seed
always give byte-identical files (and the same sha256). validate() refuses a dataset whose inputs or outputs do
not have the pinned sha256.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from eval.bench.synthetic import auto_slices

MANIFEST_SCHEMA = "general-benchmark-manifest-v1"
OUTPUT_NAMES = ("eval_set", "chunks")


class DatasetMismatch(ValueError):
    """A source or built file does not have the sha256 pinned in the manifest."""


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_manifest(path: str | Path) -> Dict[str, Any]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"{path}: schema must be {MANIFEST_SCHEMA}")
    for key in ("name", "version", "seed", "sources", "outputs", "expected_sha256"):
        if key not in manifest:
            raise ValueError(f"{path}: '{key}' is required")
    return manifest


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _dump_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _query(source: str, query_id: str, text: str, relevant: List[Dict[str, Any]], group: str, *, answerable: bool = True) -> Dict[str, Any]:
    return {
        "query_id": f"{source}:{query_id}",
        "query": text,
        "split": "dev",
        "tenant_id": "default",
        "answerable": answerable,
        "relevant": relevant,
        "slices": [f"src:{source}", *auto_slices(text)],
        "group": f"{source}:{group}",
    }


def _chunk(source: str, chunk_id: str, doc_id: str, text: str) -> Dict[str, Any]:
    return {"id": f"{source}:{chunk_id}", "doc_id": f"{source}:{doc_id}", "tenant_id": "default", "text": text}


def _build_jqara(name: str, spec: Dict[str, Any], path: Path, rng: random.Random) -> Tuple[List[Dict], List[Dict]]:
    from eval.bench.jqara import load_jqara

    queries = load_jqara(path, expected_candidates=int(spec.get("expected_candidates", 50)))
    queries = [q for q in queries if any(c.label == 1 for c in q.candidates)]
    n = min(int(spec.get("n_queries", len(queries))), len(queries))
    chosen = sorted(rng.sample([q.q_id for q in queries], n))
    by_id = {q.q_id: q for q in queries}
    chunks: Dict[str, Dict[str, Any]] = {}
    out_queries: List[Dict[str, Any]] = []
    for q_id in chosen:
        q = by_id[q_id]
        relevant = []
        for cand in q.candidates:
            chunks.setdefault(cand.passage_row_id, _chunk(name, cand.passage_row_id, cand.passage_row_id, cand.searchable_text))
            if cand.label == 1:
                relevant.append({"chunk_id": f"{name}:{cand.passage_row_id}", "doc_id": f"{name}:{cand.passage_row_id}", "grade": 1})
        out_queries.append(_query(name, q_id, q.question, relevant, q_id))
    return out_queries, list(chunks.values())


def _build_jagovfaqs(name: str, spec: Dict[str, Any], path: Path, rng: random.Random) -> Tuple[List[Dict], List[Dict]]:
    q_min, q_max = spec.get("question_chars", [10, 120])
    a_min, a_max = spec.get("answer_chars", [20, 600])
    rows = []
    with path.open(encoding="utf-8") as handle:  # streamed; line_no stays the file's line index (ids are pinned)
        for line_no, line in enumerate(handle):
            if not line.strip():
                continue
            row = json.loads(line)
            question, answer = str(row.get("Question") or "").strip(), str(row.get("Answer") or "").strip()
            if q_min <= len(question) <= q_max and a_min <= len(answer) <= a_max:
                rows.append((line_no, question, answer))
    n_docs = min(int(spec.get("n_docs", len(rows))), len(rows))
    docs = sorted(rng.sample(rows, n_docs))
    n_queries = min(int(spec.get("n_queries", n_docs)), n_docs)
    asked = sorted(rng.sample(docs, n_queries))
    chunks = [_chunk(name, f"l{line_no}", f"l{line_no}", answer) for line_no, _q, answer in docs]
    queries = [
        _query(name, f"l{line_no}", question, [{"chunk_id": f"{name}:l{line_no}", "doc_id": f"{name}:l{line_no}", "grade": 1}], f"l{line_no}")
        for line_no, question, _a in asked
    ]
    return queries, chunks


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _build_cases(name: str, spec: Dict[str, Any], root: Path) -> Tuple[List[Dict], List[Dict]]:
    from eval.bench.synthetic import _is_abstain

    chunk_rows = _read_jsonl(_resolve(root, spec["chunks"]))
    chunks = [
        _chunk(name, str(r.get("id")), str(r.get("doc_id") or r.get("source_doc") or r.get("id")), str(r.get("text") or ""))
        for r in chunk_rows
    ]
    doc_of = {str(r.get("id")): str(r.get("doc_id") or r.get("source_doc") or r.get("id")) for r in chunk_rows}
    queries = []
    for case in _read_jsonl(_resolve(root, spec["cases"])):
        case_id = str(case.get("case_id") or case.get("query_id"))
        text = str(case.get("query") or case.get("question") or "").strip()
        declared = [str(c) for c in case.get("gold_chunk_ids") or []]
        gold = [c for c in declared if c in doc_of]
        if len(gold) != len(declared):
            # a gold id missing from the corpus must not silently turn an answerable case into an abstain case
            missing = sorted(set(declared) - set(gold))
            raise ValueError(f"source '{name}': case {case_id} names gold chunk ids not in the corpus: {missing[:5]}")
        abstain = _is_abstain(case) or not gold
        relevant = [] if abstain else [{"chunk_id": f"{name}:{c}", "doc_id": f"{name}:{doc_of[c]}", "grade": 1} for c in gold]
        queries.append(_query(name, case_id, text, relevant, case_id, answerable=not abstain))
    return queries, chunks


def source_inputs(manifest: Dict[str, Any], root: Path) -> Dict[str, List[Path]]:
    out: Dict[str, List[Path]] = {}
    for name, spec in manifest["sources"].items():
        if spec["kind"] == "canonical_cases":
            out[name] = [_resolve(root, spec["cases"]), _resolve(root, spec["chunks"])]
        else:
            out[name] = [_resolve(root, spec["input"])]
    return out


def check_inputs(manifest: Dict[str, Any], root: Path, *, allow_unpinned: bool = False) -> Dict[str, Dict[str, str]]:
    """sha256 of every source input; DatasetMismatch when one differs from the pin (or is unpinned, unless allowed)."""
    found: Dict[str, Dict[str, str]] = {}
    problems: List[str] = []
    for name, paths in source_inputs(manifest, root).items():
        pinned = manifest["sources"][name].get("input_sha256") or {}
        if isinstance(pinned, str):
            pinned = {paths[0].name: pinned}
        found[name] = {}
        for path in paths:
            if not path.is_file():
                fetch = manifest["sources"][name].get("fetch", {}).get("command", "see MANIFEST.json")
                raise FileNotFoundError(f"source '{name}': {path} does not exist (fetch: {fetch})")
            digest = sha256_file(path)
            found[name][path.name] = digest
            want = pinned.get(path.name)
            if want is None and not allow_unpinned:
                problems.append(f"source '{name}': {path.name} has no pinned input_sha256 (actual {digest})")
            elif want is not None and want != digest:
                problems.append(f"source '{name}': {path.name} sha256 {digest} != pinned {want}")
    if problems:
        raise DatasetMismatch("; ".join(problems))
    return found


def build(manifest: Dict[str, Any], root: Path, out_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Build eval_set.jsonl and chunks.jsonl; returns their sha256 and the composition. Does not check pins."""
    seed = int(manifest["seed"])
    queries: List[Dict[str, Any]] = []
    chunks: List[Dict[str, Any]] = []
    composition: Dict[str, Dict[str, int]] = {}
    for name in sorted(manifest["sources"]):
        spec = manifest["sources"][name]
        # one RNG per source, seeded from (seed, name): adding a source never changes another's sample
        rng = random.Random(f"{seed}:{name}")
        kind = spec["kind"]
        if kind == "jqara":
            q, c = _build_jqara(name, spec, _resolve(root, spec["input"]), rng)
        elif kind == "jagovfaqs":
            q, c = _build_jagovfaqs(name, spec, _resolve(root, spec["input"]), rng)
        elif kind == "canonical_cases":
            q, c = _build_cases(name, spec, root)
        else:
            raise ValueError(f"source '{name}': unknown kind '{kind}'")
        composition[name] = {"queries": len(q), "answerable": sum(1 for x in q if x["answerable"]), "chunks": len(c)}
        queries.extend(q)
        chunks.extend(c)
    ids = [c["id"] for c in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate chunk ids across sources")
    target = out_dir or _resolve(root, manifest["outputs"]["dir"])
    paths = {"eval_set": target / "eval_set.jsonl", "chunks": target / "chunks.jsonl"}
    _dump_jsonl(paths["eval_set"], sorted(queries, key=lambda r: r["query_id"]))
    _dump_jsonl(paths["chunks"], sorted(chunks, key=lambda r: r["id"]))
    return {
        "paths": {k: str(v) for k, v in paths.items()},
        "sha256": {k: sha256_file(v) for k, v in paths.items()},
        "composition": composition,
    }


def check_outputs(manifest: Dict[str, Any], built_sha256: Dict[str, str]) -> None:
    expected = manifest.get("expected_sha256") or {}
    problems = []
    for name in OUTPUT_NAMES:
        want = expected.get(name)
        if want is None:
            problems.append(f"{name}: no expected sha256 pinned in the manifest (built {built_sha256.get(name)})")
        elif want != built_sha256.get(name):
            problems.append(f"{name}: built sha256 {built_sha256.get(name)} != expected {want}")
    if problems:
        raise DatasetMismatch("; ".join(problems))


def pin(manifest_path: Path, inputs: Dict[str, Dict[str, str]], built: Dict[str, Any]) -> Dict[str, Any]:
    """Write the observed input and output sha256 into the manifest (first build on the measurement PC; commit it)."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, digests in inputs.items():
        manifest["sources"][name]["input_sha256"] = digests
    manifest["expected_sha256"] = dict(built["sha256"])
    manifest["composition_built"] = built["composition"]
    manifest["status"] = "pinned"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest
