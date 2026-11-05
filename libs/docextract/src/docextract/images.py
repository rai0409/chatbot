"""Image decoding for OCR, including EMF/WMF (Office vector images).

Pillow reads raster formats. EMF/WMF (common in DOCX/PPTX made on Windows)
are converted to PNG with LibreOffice (soffice) or Inkscape when one of them
is installed; otherwise they are skipped and reported. Conversion runs in a
private temporary directory that is removed afterwards
(DOCEXTRACT_TMP_DIR chooses its parent).
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from docextract._util import env_float, env_str

_MIN_SIDE_PX = 48
# Decompression-bomb guard: images above this many pixels are skipped.
_MAX_PIXELS = 60_000_000


def vector_format(data: bytes, name: str = "") -> Optional[str]:
    """"emf" / "wmf" when the bytes (or the file name) are a Windows metafile."""
    suffix = Path(name).suffix.lower()
    if suffix in (".emf", ".emz"):
        return "emf"
    if suffix in (".wmf", ".wmz"):
        return "wmf"
    if len(data) >= 44 and data[40:44] == b" EMF":
        return "emf"
    placeable = data[:4] == b"\xd7\xcd\xc6\x9a"
    plain = data[:2] in (b"\x01\x00", b"\x02\x00") and data[2:4] == b"\x09\x00"
    if placeable or plain:
        return "wmf"
    return None


def converter() -> Optional[Tuple[str, str]]:
    """(kind, executable) of an available metafile converter."""
    for kind, env, names in (
        ("soffice", "DOCEXTRACT_SOFFICE", ("soffice", "libreoffice")),
        ("inkscape", "DOCEXTRACT_INKSCAPE", ("inkscape",)),
    ):
        configured = env_str(env)
        if configured and Path(configured).exists():
            return kind, configured
        for name in names:
            found = shutil.which(name)
            if found:
                return kind, found
    return None


def convert_vector(data: bytes, fmt: str) -> Optional[bytes]:
    """PNG bytes of an EMF/WMF image, or None when no converter works."""
    tool = converter()
    if tool is None:
        return None
    kind, exe = tool
    timeout = env_float("DOCEXTRACT_CONVERT_TIMEOUT_SECONDS", 60.0)
    parent = env_str("DOCEXTRACT_TMP_DIR") or None
    with tempfile.TemporaryDirectory(prefix="docextract-", dir=parent) as tmp:
        src = Path(tmp) / f"image.{fmt}"
        src.write_bytes(data)
        if kind == "soffice":
            # A private profile directory: concurrent conversions do not share
            # (and lock) the user's LibreOffice profile.
            profile = Path(tmp) / "profile"
            cmd = [exe, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--convert-to", "png", "--outdir", tmp, str(src)]
        else:
            cmd = [exe, str(src), "--export-type=png", f"--export-filename={Path(tmp) / 'image.png'}"]
        try:
            subprocess.run(cmd, check=True, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return None
        out = Path(tmp) / "image.png"
        return out.read_bytes() if out.exists() else None


def load_image(data: bytes, name: str = ""):
    """(PIL image or None, reason). Reasons: ok, too_small, too_large,
    unsupported_vector (no converter), undecodable, pillow_missing."""
    try:
        from PIL import Image
    except ImportError:
        return None, "pillow_missing"
    fmt = vector_format(data, name)
    if fmt:
        data = convert_vector(data, fmt) or b""
        if not data:
            return None, "unsupported_vector"
    try:
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
            if width * height > _MAX_PIXELS:
                return None, "too_large"
            if min(width, height) < _MIN_SIDE_PX:
                return None, "too_small"
            image = probe.convert("RGB")
    except Exception:  # noqa: BLE001 - truncated, unknown or bomb-protected image
        return None, "undecodable"
    return image, "ok"
