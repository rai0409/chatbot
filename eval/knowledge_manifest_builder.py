from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Sequence

from rag_core.knowledge_manifest import (
    build_manifest,
    compute_file_checksum,
    duplicate_source_ids,
    save_manifest,
)


DEFAULT_OUTPUT = Path("data/knowledge/manifest.json")
DEFAULT_SCAN_PATTERNS = (
    ("data/approved_qa", "*.jsonl"),
    ("data/source_pdfs", "*.pdf"),
    ("pdfs", "*.pdf"),
    ("index", "*.jsonl"),
    ("eval/cases", "*.jsonl"),
)


def infer_source_type(path: Path) -> str:
    normalized = path.as_posix()
    if normalized.startswith("data/approved_qa/"):
        return "approved_qa"
    if normalized.startswith("index/"):
        return "index_jsonl"
    if normalized.startswith("eval/cases/"):
        return "eval_case"
    if path.suffix.lower() == ".pdf":
        return "pdf"
    return "other"


def infer_category(path: Path) -> str | None:
    normalized = path.as_posix()
    if normalized.startswith("data/approved_qa/"):
        return "approved_qa"
    if normalized.startswith("data/source_pdfs/"):
        return "source_pdf"
    if normalized.startswith("pdfs/"):
        return "pdf"
    if normalized.startswith("index/"):
        return "index"
    if normalized.startswith("eval/cases/"):
        return "eval_case"
    return None


def source_id_for_path(path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-").lower() or "source"
    source_type = infer_source_type(path)
    return f"{source_type}:{stem}"


def _is_excluded(path: Path) -> bool:
    parts = set(path.parts)
    if "__pycache__" in parts:
        return True
    normalized = path.as_posix()
    if normalized.startswith("runs/") or normalized.startswith("artifacts/eval/"):
        return True
    if path.suffix in {".pyc", ".log"}:
        return True
    return False


def scan_knowledge_sources(
    root_dir: str | Path = ".",
    *,
    tenant_id: str = "default",
    status: str = "active",
    patterns: Sequence[tuple[str, str]] = DEFAULT_SCAN_PATTERNS,
) -> tuple[list[dict[str, Any]], list[str]]:
    root = Path(root_dir)
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    for directory, glob_pattern in patterns:
        scan_dir = root / directory
        if not scan_dir.exists():
            warnings.append(f"missing_scan_directory:{directory}")
            continue
        for path in sorted(scan_dir.glob(glob_pattern), key=lambda item: item.as_posix()):
            if not path.is_file() or _is_excluded(path):
                continue
            relative = path.relative_to(root)
            source_type = infer_source_type(relative)
            if source_type == "other":
                warnings.append(f"unsupported_file:{relative.as_posix()}")
                continue
            records.append(
                {
                    "source_id": source_id_for_path(relative),
                    "tenant_id": tenant_id,
                    "source_type": source_type,
                    "source_title": relative.name,
                    "source_path": relative.as_posix(),
                    "version": "1",
                    "checksum": compute_file_checksum(path),
                    "checksum_algorithm": "sha256",
                    "status": status,
                    "indexed_at": None,
                    "updated_at": None,
                    "category": infer_category(relative),
                    "metadata": {"file_size_bytes": path.stat().st_size},
                }
            )
    duplicates = duplicate_source_ids(records)
    for source_id in duplicates:
        warnings.append(f"duplicate_source_id:{source_id}")
    return records, warnings


def build_knowledge_manifest(
    *,
    root_dir: str | Path = ".",
    output_path: str | Path = DEFAULT_OUTPUT,
    tenant_id: str = "default",
) -> dict[str, Any]:
    records, warnings = scan_knowledge_sources(root_dir=root_dir, tenant_id=tenant_id)
    manifest = build_manifest(records, warnings)
    output = Path(output_path)
    save_manifest(manifest, output if output.is_absolute() else Path(root_dir) / output)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a local knowledge manifest for commercial RAG sources.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--tenant-id", default="default")
    args = parser.parse_args(argv)
    manifest = build_knowledge_manifest(root_dir=args.root, output_path=args.output, tenant_id=args.tenant_id)
    print(
        json.dumps(
            {
                "output_path": str(args.output),
                "record_count": len(manifest.get("records", [])),
                "warning_count": len(manifest.get("warnings", [])),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
