"""Import manifest + duplicate detection for canonical chunk JSONL (Prompt019/022).

Given one or more canonical JSONL files, produces a manifest JSON describing
what an import would contain, and flags safety issues before any ingest:

- repeated chunk ids (within and across files)
- identical text under different ids (exact normalized-hash level only)
- tenant_id mismatches within one source_doc
- rows whose tenant differs from an --expected-tenant
- collisions against a previously written manifest (same source_doc with a
  different chunk-id hash, or chunk ids already present elsewhere)

Pure measurement: reads JSONL, writes JSON. Never touches the vectorstore.
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
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List

SCHEMA_VERSION = "import_manifest.v1"


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _read_jsonl(path: str | Path) -> List[dict]:
    rows: List[dict] = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                raise ValueError(f"{path}:{line_no}: canonical rows must be objects")
            rows.append(obj)
    return rows


def _ids_hash(chunk_ids: List[str]) -> str:
    return hashlib.sha256("\n".join(sorted(chunk_ids)).encode("utf-8")).hexdigest()[:16]


def build_manifest(
    input_paths: List[str | Path],
    *,
    expected_tenant: str | None = None,
    existing_manifest: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    files: List[Dict[str, Any]] = []
    by_source: Dict[str, Dict[str, Any]] = {}
    id_locations: Dict[str, List[str]] = {}
    text_hash_ids: Dict[str, set[str]] = {}

    for path in input_paths:
        rows = _read_jsonl(path)
        files.append({"path": str(path), "rows": len(rows)})
        for row in rows:
            chunk_id = _norm_text(row.get("id"))
            source_doc = _norm_text(row.get("source_doc")) or "(missing source_doc)"
            tenant = _norm_text(row.get("tenant_id")) or "default"
            source_type = _norm_text(row.get("source_type") or row.get("type"))
            id_locations.setdefault(chunk_id, []).append(str(path))
            text_hash = hashlib.sha256(_norm_text(row.get("text")).encode("utf-8")).hexdigest()[:16]
            text_hash_ids.setdefault(text_hash, set()).add(chunk_id)

            entry = by_source.setdefault(
                source_doc,
                {"chunk_count": 0, "chunk_ids": [], "tenant_ids": set(), "source_types": set()},
            )
            entry["chunk_count"] += 1
            entry["chunk_ids"].append(chunk_id)
            entry["tenant_ids"].add(tenant)
            if source_type:
                entry["source_types"].add(source_type)

    duplicate_ids = sorted(
        chunk_id
        for chunk_id, locations in id_locations.items()
        if len(locations) > 1 or not chunk_id
    )
    def _is_parent_child_chain(ids: set[str]) -> bool:
        # PDF chunking legitimately emits parent+child rows with identical
        # text whose ids form a prefix chain (…:parent:1 / …:parent:1:child:1).
        ordered = sorted(ids, key=len)
        shortest = ordered[0]
        return all(i == shortest or i.startswith(shortest + ":") for i in ordered)

    duplicate_texts = []
    parent_child_duplicates = []
    for text_hash, ids in sorted(text_hash_ids.items()):
        if len(ids) <= 1:
            continue
        group = {"text_hash": text_hash, "chunk_ids": sorted(ids)}
        if _is_parent_child_chain(ids):
            parent_child_duplicates.append(group)
        else:
            duplicate_texts.append(group)
    tenant_mismatches = sorted(
        source_doc
        for source_doc, entry in by_source.items()
        if len(entry["tenant_ids"]) > 1
    )
    unexpected_tenants = []
    if expected_tenant:
        for source_doc, entry in sorted(by_source.items()):
            others = sorted(t for t in entry["tenant_ids"] if t != expected_tenant)
            if others:
                unexpected_tenants.append({"source_doc": source_doc, "tenant_ids": others})

    collisions = []
    if existing_manifest:
        previous = existing_manifest.get("source_docs") or {}
        previous_ids = {
            chunk_id
            for entry in previous.values()
            for chunk_id in entry.get("chunk_ids", [])
        }
        for source_doc, entry in sorted(by_source.items()):
            prior = previous.get(source_doc)
            ids_hash = _ids_hash(entry["chunk_ids"])
            if prior is not None and prior.get("chunk_ids_hash") != ids_hash:
                collisions.append(
                    {"source_doc": source_doc, "reason": "source_doc already imported with different chunk ids"}
                )
            overlap = sorted(set(entry["chunk_ids"]) & previous_ids - set((prior or {}).get("chunk_ids", [])))
            if overlap:
                collisions.append(
                    {"source_doc": source_doc, "reason": "chunk ids already present under another source_doc", "chunk_ids": overlap[:10]}
                )

    issues = {
        "duplicate_ids": duplicate_ids,
        "duplicate_texts": duplicate_texts,
        "tenant_mismatches": tenant_mismatches,
        "unexpected_tenants": unexpected_tenants,
        "collisions": collisions,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "files": files,
        "expected_tenant": expected_tenant,
        "informational": {"parent_child_duplicates": parent_child_duplicates},
        "source_docs": {
            source_doc: {
                "chunk_count": entry["chunk_count"],
                "chunk_ids_hash": _ids_hash(entry["chunk_ids"]),
                "chunk_ids": sorted(entry["chunk_ids"]),
                "tenant_ids": sorted(entry["tenant_ids"]),
                "source_types": sorted(entry["source_types"]),
            }
            for source_doc, entry in sorted(by_source.items())
        },
        "issues": issues,
        "ok": not any(issues.values()),
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an import manifest for canonical chunk JSONL files.")
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-tenant", default=None)
    parser.add_argument("--existing", default=None, help="previous manifest JSON to check collisions against")
    args = parser.parse_args(argv)

    existing = None
    if args.existing:
        existing = json.loads(Path(args.existing).read_text(encoding="utf-8"))
    manifest = build_manifest(
        list(args.inputs), expected_tenant=args.expected_tenant, existing_manifest=existing
    )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    issues = {k: len(v) for k, v in manifest["issues"].items() if v}
    print(
        "Summary: "
        f"files={len(manifest['files'])} "
        f"source_docs={len(manifest['source_docs'])} "
        f"chunks={sum(e['chunk_count'] for e in manifest['source_docs'].values())} "
        f"ok={manifest['ok']} "
        f"issues={json.dumps(issues, ensure_ascii=False)} "
        f"output={out}"
    )
    return 0 if manifest["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
