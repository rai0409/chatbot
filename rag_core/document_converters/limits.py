"""Limits for untrusted Office files (stage 14).

Uploaded DOCX/XLSX/PPTX are zip archives: a small upload can declare members
that expand to gigabytes (zip bomb), and sheet or table markup can ask for
billions of cells. Every member is read through read_member() with a byte
cap, and the converters cap spreadsheet sizes, so one hostile file fails as
DocumentTooLarge instead of exhausting the ingestion worker.

  OFFICE_MAX_MEMBER_BYTES        one member, uncompressed (default 200 MB)
  OFFICE_MAX_UNCOMPRESSED_BYTES  all members together (default 1 GB)
  OFFICE_MAX_MEMBERS             members in the archive (default 20,000)
  OFFICE_MAX_RATIO               uncompressed/compressed of a member above 1 MB (default 1,000;
                                 repetitive spreadsheets reach 100, zip bombs thousands)
  XLSX_MAX_COLUMNS               columns of one sheet (default 2,048)
  XLSX_MAX_CELLS                 rows x columns of one sheet (default 5,000,000)
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

_MB = 1024 * 1024
# Word tables have at most 63 columns; a larger gridSpan is hostile markup.
MAX_GRID_SPAN = 63


class DocumentTooLarge(ValueError):
    """An Office file exceeds a size limit (possible zip bomb)."""


def _limit(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, "") or "").strip() or default)
    except ValueError:
        return default


def max_columns() -> int:
    return _limit("XLSX_MAX_COLUMNS", 2048)


def max_cells() -> int:
    return _limit("XLSX_MAX_CELLS", 5_000_000)


def open_office_zip(path: str | Path) -> zipfile.ZipFile:
    """ZipFile after checking member count, declared sizes and ratios."""
    zf = zipfile.ZipFile(path)
    try:
        infos = zf.infolist()
        if len(infos) > _limit("OFFICE_MAX_MEMBERS", 20_000):
            raise DocumentTooLarge(f"{len(infos)} zip members")
        member_cap = _limit("OFFICE_MAX_MEMBER_BYTES", 200 * _MB)
        ratio_cap = _limit("OFFICE_MAX_RATIO", 1000)
        total = 0
        for info in infos:
            if info.file_size > member_cap:
                raise DocumentTooLarge(f"member {info.filename[:80]} expands to {info.file_size} bytes")
            if info.file_size > _MB and info.file_size > ratio_cap * max(1, info.compress_size):
                raise DocumentTooLarge(f"member {info.filename[:80]} compression ratio too high")
            total += info.file_size
        if total > _limit("OFFICE_MAX_UNCOMPRESSED_BYTES", 1024 * _MB):
            raise DocumentTooLarge(f"archive expands to {total} bytes")
    except BaseException:
        zf.close()
        raise
    return zf


def read_member(zf: zipfile.ZipFile, name: str) -> bytes:
    """Member bytes, stopping at the cap even if the declared size lies."""
    cap = _limit("OFFICE_MAX_MEMBER_BYTES", 200 * _MB)
    with zf.open(name) as f:
        data = f.read(cap + 1)
    if len(data) > cap:
        raise DocumentTooLarge(f"member {name[:80]} is larger than {cap} bytes")
    return data
