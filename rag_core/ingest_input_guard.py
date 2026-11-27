"""Which tenant may ingest a path: per-tenant upload folders and per-source mirrors are not shared.

Two folders under the ingest roots hold other tenants' documents:
- the upload folder (INGEST_UPLOAD_DIR, default <first ingest root>/uploads), one sub-folder per tenant;
- the source mirrors (SOURCES_DIR/mirror/<source_id>), one per registered remote source.

An input that is one of these folders or an ancestor of them (for example the ingest root itself) would let a
folder scan walk into every tenant's documents, so it is refused. Inside the upload folder only the caller's own
tenant folder is allowed; inside the mirror folder only the mirrors of sources the tenant owns.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, List, Optional

import config

_DEFAULT_TENANT = "default"  # as rag_core.retrieval.normalize_tenant_id


def _ingest_roots() -> List[Path]:
    roots: List[Path] = []
    for part in str(os.getenv("INGEST_ALLOWED_ROOTS", "")).split(os.pathsep):
        part = part.strip()
        if part:
            root = Path(part)
            roots.append((root if root.is_absolute() else Path(config.BASE_DIR) / root).resolve())
    return roots


def _absolute(raw: str | Path) -> Path:
    path = Path(str(raw).strip())
    return path if path.is_absolute() else Path(config.BASE_DIR) / path


def upload_root() -> Optional[Path]:
    """The resolved upload folder (the same rule as the upload route), or None when none can exist."""
    configured = str(os.getenv("INGEST_UPLOAD_DIR", "")).strip()
    if configured:
        return _absolute(configured).resolve()
    roots = _ingest_roots()
    return (roots[0] / "uploads").resolve() if roots else None


def mirror_root() -> Path:
    from rag_core import source_registry

    return (source_registry.sources_dir() / "mirror").resolve()


def protected_roots() -> List[Path]:
    """Folders a scan must never walk into from above (their content belongs to individual tenants)."""
    roots = [mirror_root()]
    upload = upload_root()
    if upload is not None:
        roots.append(upload)
    return roots


def tenant_segment(tenant: Optional[str]) -> str:
    """The upload folder name of a tenant (the same rule as the upload route)."""
    normalized = str(tenant or "").strip() or _DEFAULT_TENANT
    return re.sub(r"[^A-Za-z0-9_.-]", "_", normalized)[:128]


def _owned_source_ids(tenant: str) -> set:
    from rag_core import source_registry

    try:
        return {r["source_id"] for r in source_registry.list_sources(tenant)}
    except source_registry.SourceError:
        return set()  # an unreadable registry grants nothing


def check_path(raw: str | Path, tenant: Optional[str], *, upload: Optional[Path] = None) -> None:
    """Raise PermissionError when `tenant` may not ingest or scan `raw` (a file or a folder).

    Paths outside both protected folders are not judged here (the ingest-root confinement is a separate check).
    `upload`: the upload folder as the caller resolved it (default: upload_root())."""
    normalized = str(tenant or "").strip() or _DEFAULT_TENANT
    resolved = _absolute(raw).resolve()
    upload = upload.resolve() if upload is not None else upload_root()
    mirror = mirror_root()
    for protected in (upload, mirror):
        if protected is not None and protected.is_relative_to(resolved):
            raise PermissionError("the input contains other tenants' upload or mirror folders")
    if upload is not None and resolved.is_relative_to(upload):
        parts = resolved.relative_to(upload).parts
        if not parts or parts[0] != tenant_segment(normalized):
            raise PermissionError("input belongs to another tenant's upload folder")
    if resolved.is_relative_to(mirror):
        parts = resolved.relative_to(mirror).parts
        if not parts or parts[0] not in _owned_source_ids(normalized):
            raise PermissionError("input belongs to another tenant's source mirror")


def check_paths(inputs: Iterable[str | Path], tenant: Optional[str], *, upload: Optional[Path] = None) -> None:
    """check_path for each non-blank input."""
    for raw in inputs:
        if str(raw).strip():
            check_path(raw, tenant, upload=upload)
