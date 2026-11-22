"""Batch mail adapter for the existing canonical ingest contract.

No vectorstore or job-registry side effects. The original source emails remain
unchanged; restored quoted mail is not indexed as an independent original.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Sequence

from rag_core import mail_ingest
from scripts.mail_to_canonical_jsonl import _canonical_rows

_MAIL_TYPES = frozenset({".eml", ".csv", ".tsv", ".xlsx", ".xlsm"})
_TENANT_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _positive_limit(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise ValueError(f"invalid_{name.lower()}") from None
    if value < 1:
        raise ValueError(f"invalid_{name.lower()}")
    return value


def validate_mail_inputs(paths: Sequence[Path], *, tenant_id: str) -> None:
    """Bound input count/size/type before accepting or queueing a mail job."""
    if not _TENANT_RE.fullmatch(tenant_id):
        raise ValueError("invalid_mail_tenant_id")
    if not paths:
        raise ValueError("mail_inputs_required")
    max_files = _positive_limit("MAIL_INGEST_MAX_FILES", 100)
    max_bytes = _positive_limit("MAIL_INGEST_MAX_TOTAL_BYTES", 50 * 1024 * 1024)
    max_file = _positive_limit("MAIL_INGEST_MAX_FILE_BYTES", 15 * 1024 * 1024)
    if len(paths) > max_files:
        raise ValueError("mail_file_count_limit")
    total = 0
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("mail_input_must_be_regular_file")
        if path.suffix.lower() not in _MAIL_TYPES:
            raise ValueError("unsupported_mail_format")
        size = path.stat().st_size
        total += size
        if size > max_file or total > max_bytes:
            raise ValueError("mail_input_size_limit")


def convert_mail_inputs(paths: Sequence[Path], *, tenant_id: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Convert an explicitly authorized set of files in a *single* mail batch.

    Authorization and INGEST_ALLOWED_ROOTS confinement are enforced by the
    ingestion job before calling this function. No directory walk is accepted
    here: batch boundaries are explicit and countable.
    """
    validate_mail_inputs(paths, tenant_id=tenant_id)
    max_messages = _positive_limit("MAIL_INGEST_MAX_MESSAGES", 2000)
    failures: list[dict] = []
    raws = []
    for raw_path in paths:
        path = Path(raw_path)
        # mail_ingest deliberately raises SystemExit on invalid table columns;
        # present a safe error instead of terminating an API worker.
        try:
            parsed = mail_ingest.iter_inputs(path, {}, failures=failures)
        except SystemExit:
            raise ValueError("invalid_mail_input_format") from None
        if failures:
            raise ValueError("mail_parse_failure")
        raws.extend(parsed)
        if len(raws) > max_messages:
            raise ValueError("mail_message_count_limit")
    if not raws:
        raise ValueError("mail_batch_empty")

    result = mail_ingest.process(
        raws, model=None, profile=mail_ingest.PROFILES["evidence"],
        threshold=0.9, smooth=0.0, sentence_split=False,
        match_threshold=0.6, jobs=1, failures=failures,
    )
    if failures or result.failures:
        raise ValueError("mail_processing_failure")
    # Use a consistent batch-wide basis for identifying EML paths without a
    # Message-ID. Such IDs are path-dependent, and MUST NOT be presented as
    # stable across an upload to a different directory.
    rows, summary = _canonical_rows(result, tenant_id, Path("/"))
    return rows, summary
