"""Scanning a folder into a manifest, and what changed between two manifests (step 5-1)."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from rag_core.source_registry import SourceUnavailable

DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024
_FALLBACK_EXTENSIONS = ("csv", "xlsx", "docx", "pptx", "pdf")  # as rag_core.document_converters.SUPPORTED_FORMATS
_TEMP_SUFFIXES = (".tmp", ".part", ".crdownload", "~")


def supported_extensions() -> frozenset:
    try:
        from rag_core.document_converters import SUPPORTED_FORMATS

        return frozenset(str(ext).lower().lstrip(".") for ext in SUPPORTED_FORMATS)
    except Exception:  # noqa: BLE001 - the converters are heavy; the scan must work without them
        return frozenset(_FALLBACK_EXTENSIONS)


def max_file_bytes() -> int:
    try:
        value = int(os.getenv("INGEST_MAX_FILE_BYTES", "") or DEFAULT_MAX_FILE_BYTES)
    except ValueError:
        return DEFAULT_MAX_FILE_BYTES
    return value if value > 0 else DEFAULT_MAX_FILE_BYTES


def sha256_of(path: Path, *, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class ScanResult:
    manifest: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    skipped: Dict[str, Any] = field(default_factory=lambda: {
        "unsupported": 0, "hidden": 0, "symlink": 0, "too_large": [], "unreadable": []})
    hashed: int = 0  # files read and hashed during this scan


def _skip_name(name: str) -> bool:
    return name.startswith(".") or name.startswith("~$") or name.lower().endswith(_TEMP_SUFFIXES)


def scan_folder(
    root: Path, *, previous: Optional[Mapping[str, Mapping[str, Any]]] = None, extensions: Optional[Iterable[str]] = None,
    size_limit: Optional[int] = None, hasher: Callable[[Path], str] = sha256_of,
    force_rehash: bool = False,
) -> ScanResult:
    """relative posix path -> {"size", "mtime_ns", "sha256"} of the supported files under root.

    Default fast mode reuses a previous SHA when size and mtime match; this
    does NOT guarantee detecting same-metadata content changes. Registered source
    synchronization requests force_rehash=True for reliable ingest decisions.
    A missing or unmounted directory raises SourceUnavailable, never an empty scan."""
    if not root.is_dir():
        raise SourceUnavailable("the source folder is not available")
    allowed = frozenset(e.lower().lstrip(".") for e in (extensions or supported_extensions()))
    limit = size_limit or max_file_bytes()
    previous = previous or {}
    result = ScanResult()
    for current, dirs, files in os.walk(root, followlinks=False):
        here = Path(current)
        kept_dirs = []
        for name in sorted(dirs):
            if _skip_name(name):
                result.skipped["hidden"] += 1
            elif (here / name).is_symlink():
                result.skipped["symlink"] += 1
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in sorted(files):
            path = here / name
            rel = path.relative_to(root).as_posix()
            if _skip_name(name):
                result.skipped["hidden"] += 1
                continue
            if path.is_symlink():
                result.skipped["symlink"] += 1
                continue
            if path.suffix.lower().lstrip(".") not in allowed:
                result.skipped["unsupported"] += 1
                continue
            try:
                stat = path.stat()
                if stat.st_size > limit:
                    result.skipped["too_large"].append(rel)
                    continue
                # Fast mode supports callers that only need a metadata scan.
                # Do not use cached digests for ingestion/change approvals:
                # source_sync always sets force_rehash=True.
                before = previous.get(rel)
                if (
                    not force_rehash
                    and before
                    and before.get("size") == stat.st_size
                    and before.get("mtime_ns") == stat.st_mtime_ns
                    and isinstance(before.get("sha256"), str)
                ):
                    digest = before["sha256"]
                else:
                    digest = hasher(path)
                    result.hashed += 1
            except OSError:
                result.skipped["unreadable"].append(rel)
                continue
            result.manifest[rel] = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "ctime_ns": stat.st_ctime_ns,
                "sha256": digest,
            }
    return result


def diff_manifests(
    applied: Mapping[str, Mapping[str, Any]], observed: Mapping[str, Mapping[str, Any]]
) -> Dict[str, List[str]]:
    """What the observed files changed against what is applied: added, modified (the content differs), deleted."""
    return {
        "added": sorted(set(observed) - set(applied)),
        "modified": sorted(p for p in set(observed) & set(applied) if observed[p].get("sha256") != applied[p].get("sha256")),
        "deleted": sorted(set(applied) - set(observed)),
    }


def document_name(rel_path: str) -> str:
    """The document name ingestion gives a file (its file name)."""
    return rel_path.rsplit("/", 1)[-1]


def name_conflicts(paths: Sequence[str]) -> Dict[str, List[str]]:
    """document name -> the paths that share it (ingestion would let them replace each other, so it skips them)."""
    groups: Dict[str, List[str]] = {}
    for rel in paths:
        groups.setdefault(document_name(rel), []).append(rel)
    return {name: sorted(items) for name, items in sorted(groups.items()) if len(items) > 1}
