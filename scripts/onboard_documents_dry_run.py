"""One-command tenant document onboarding — dry-run by default (Prompt019/022).

Pipeline: discover supported documents in a directory → convert each to
canonical chunks (Prompt018 converters) → build an import manifest with
duplicate/tenant validation → print exactly what WOULD be ingested.

Nothing is ever written to a vectorstore unless --execute AND an explicitly
named non-production --collection are both given. The production/default
collection (config.VECTORSTORE_COLLECTION_NAME or CHROMA_COLLECTION) is
refused even with --execute.
"""

from __future__ import annotations

# --- bootstrap: add repo root to sys.path for script execution ---
import sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# --- end bootstrap ---

import argparse
import json
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import List

from rag_core.document_converters import SUPPORTED_FORMATS, convert_file_to_canonical_chunks
from scripts.import_manifest import build_manifest


def _production_collection_names() -> set[str]:
    import config

    names = {
        str(config.getenv_first("CHROMA_COLLECTION") or "").strip(),
        str(config.VECTORSTORE_COLLECTION_NAME or "").strip(),
    }
    return {n for n in names if n}


@contextmanager
def _patched_argv(argv: List[str]):
    original = sys.argv
    sys.argv = argv
    try:
        yield
    finally:
        sys.argv = original


def _run_ingest(merged_jsonl: Path, collection: str) -> None:
    # Separated for tests: monkeypatch this to assert dry-run never ingests.
    from scripts import ingest_canonical_jsonl

    with _patched_argv(
        ["ingest_canonical_jsonl.py", str(merged_jsonl), "--collection", collection]
    ):
        exit_code = ingest_canonical_jsonl.main()
    if exit_code not in (0, None):
        raise RuntimeError(f"ingest failed with exit code {exit_code}")


def discover_documents(input_dir: Path) -> List[Path]:
    return sorted(
        p
        for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower().lstrip(".") in SUPPORTED_FORMATS
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert a directory of documents for one tenant and show the import plan (dry-run by default)."
    )
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--output-dir", default=None, help="work dir for converted JSONL + manifest (default: runs/onboarding/<tenant>)")
    parser.add_argument("--collection", default=None, help="non-production collection name (required with --execute)")
    parser.add_argument("--existing-manifest", default=None)
    parser.add_argument("--execute", action="store_true", help="actually ingest after validation (still refuses production collections)")
    args = parser.parse_args(argv)

    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print(f"ERROR: input dir not found: {input_dir}", file=sys.stderr)
        return 2
    tenant_id = str(args.tenant_id).strip()
    if not tenant_id:
        print("ERROR: --tenant-id must be non-empty", file=sys.stderr)
        return 2

    if args.execute:
        if not args.collection:
            print("ERROR: --execute requires an explicit non-production --collection", file=sys.stderr)
            return 2
        if args.collection in _production_collection_names():
            print(
                f"ERROR: refusing to ingest into production/default collection '{args.collection}'",
                file=sys.stderr,
            )
            return 2

    documents = discover_documents(input_dir)
    if not documents:
        print(f"ERROR: no supported documents ({', '.join(SUPPORTED_FORMATS)}) in {input_dir}", file=sys.stderr)
        return 2

    out_dir = Path(args.output_dir or f"runs/onboarding/{tenant_id}")
    out_dir.mkdir(parents=True, exist_ok=True)

    part_paths: List[Path] = []
    all_chunks = []
    for doc in documents:
        chunks = convert_file_to_canonical_chunks(doc, tenant_id=tenant_id)
        part = out_dir / f"{doc.name}.jsonl"
        with part.open("w", encoding="utf-8") as f:
            for chunk in chunks:
                f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
        part_paths.append(part)
        all_chunks.extend(chunks)

    merged = out_dir / "merged_chunks.jsonl"
    with merged.open("w", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    existing = None
    if args.existing_manifest:
        existing = json.loads(Path(args.existing_manifest).read_text(encoding="utf-8"))
    manifest = build_manifest(
        [str(p) for p in part_paths],
        expected_tenant=tenant_id,
        existing_manifest=existing,
    )
    manifest_path = out_dir / "import_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    by_kind = Counter((c.get("source_type"), c.get("chunk_type")) for c in all_chunks)
    print("Onboarding plan")
    print(f"  tenant_id:   {tenant_id}")
    print(f"  documents:   {len(documents)} ({', '.join(d.name for d in documents)})")
    print(f"  chunks:      {len(all_chunks)}")
    for (src, kind), count in sorted(by_kind.items()):
        print(f"    {src}/{kind}: {count}")
    print(f"  manifest:    {manifest_path} (ok={manifest['ok']})")
    if not manifest["ok"]:
        issues = {k: v for k, v in manifest["issues"].items() if v}
        print(f"  ISSUES:      {json.dumps(issues, ensure_ascii=False)}")
        print("Validation failed: nothing will be ingested.")
        return 1
    target = args.collection or "(not specified)"
    if args.execute:
        _run_ingest(merged, args.collection)
        print(f"  ingested into non-production collection: {args.collection}")
    else:
        print(f"  target collection: {target}")
        print("  DRY-RUN: nothing was written to any vectorstore.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
