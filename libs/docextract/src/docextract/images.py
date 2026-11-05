"""Image decoding for OCR, including EMF/WMF (Office vector images).

Pillow reads raster formats. EMF/WMF (common in DOCX/PPTX made on Windows)
are converted to PNG with LibreOffice (soffice) or Inkscape when one of them
is installed; otherwise they are skipped and reported. Conversion runs in a
private temporary directory that is removed afterwards
(DOCEXTRACT_TMP_DIR chooses its parent).
"""

from __future__ import annotations

import io
import os
import shutil
import signal
import subprocess
import tempfile
import warnings
from pathlib import Path
from typing import Optional, Tuple

from docextract._util import env_float, env_str

_MB = 1024 * 1024

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


def _limits(cpu_seconds: int, file_bytes: int, memory_bytes: int):
    """preexec_fn for the converter: CPU, written-file size and (optionally) memory limits."""

    def apply() -> None:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
        if memory_bytes > 0:
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))

    return apply


def _converter_env(tmp: str) -> dict:
    # No inherited secrets (API keys, tokens): only what a converter needs.
    keep = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "DISPLAY") if k in os.environ}
    keep["HOME"] = tmp
    return keep


def _convert(data: bytes, fmt: str) -> Tuple[Optional[bytes], str]:
    """(PNG bytes, "ok") or (None, reason): unsupported_vector, too_large, conversion_failed."""
    tool = converter()
    if tool is None:
        return None, "unsupported_vector"
    if len(data) > int(env_float("DOCEXTRACT_MAX_METAFILE_BYTES", 20 * _MB)):
        return None, "too_large"
    kind, exe = tool
    timeout = env_float("DOCEXTRACT_CONVERT_TIMEOUT_SECONDS", 60.0)
    max_out = int(env_float("DOCEXTRACT_MAX_CONVERT_OUTPUT_BYTES", 50 * _MB))
    memory = int(env_float("DOCEXTRACT_CONVERT_MAX_MEMORY_MB", 0) * _MB)  # 0 = no limit
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
        kwargs = {}
        if os.name == "posix":
            # Own process group (the whole tree can be killed on timeout) and
            # kernel limits on CPU time, output file size and memory.
            kwargs = {
                "start_new_session": True,
                "preexec_fn": _limits(int(timeout) + 1, max_out + 1, memory),
            }
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_converter_env(tmp), **kwargs
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return None, "conversion_failed"
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            return None, "conversion_failed"
        finally:
            if proc.poll() is None:
                _kill_tree(proc)
        out = Path(tmp) / "image.png"
        if proc.returncode != 0 or not out.exists():
            return None, "conversion_failed"
        if out.stat().st_size > max_out:
            return None, "conversion_failed"
        return out.read_bytes(), "ok"


def _kill_tree(proc: "subprocess.Popen") -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.SubprocessError:
        pass


def convert_vector(data: bytes, fmt: str) -> Optional[bytes]:
    """PNG bytes of an EMF/WMF image, or None when no converter works."""
    return _convert(data, fmt)[0]


def load_image(data: bytes, name: str = ""):
    """(PIL image or None, reason). Reasons: ok, too_small, too_large,
    unsupported_vector (no converter), conversion_failed, undecodable,
    pillow_missing."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None, "pillow_missing"
    if len(data) > int(env_float("DOCEXTRACT_MAX_IMAGE_BYTES", 50 * _MB)):
        return None, "too_large"
    fmt = vector_format(data, name)
    if fmt:
        data, reason = _convert(data, fmt)
        if not data:
            return None, reason
    try:
        with warnings.catch_warnings():
            # Pillow only warns between 1x and 2x its pixel limit; treat that as a bomb too.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                width, height = probe.size
                if width * height > _MAX_PIXELS:
                    return None, "too_large"
                if min(width, height) < _MIN_SIDE_PX:
                    return None, "too_small"
                probe.load()
                # Phone photos carry their rotation in EXIF; OCR needs it applied.
                image = ImageOps.exif_transpose(probe).convert("RGB")
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None, "too_large"
    except Exception:  # noqa: BLE001 - truncated or unknown image
        return None, "undecodable"
    return image, "ok"
