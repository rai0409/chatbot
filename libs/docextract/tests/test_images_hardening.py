"""PR C: image loading limits (input size, bombs, EXIF, converter output and time)."""
from __future__ import annotations

import io
import stat
import time

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from docextract import images  # noqa: E402


def _png(width=100, height=100, color=(200, 10, 10)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "PNG")
    return buf.getvalue()


def test_input_byte_cap_is_checked_before_decoding(monkeypatch):
    monkeypatch.setenv("DOCEXTRACT_MAX_IMAGE_BYTES", "100")
    image, reason = images.load_image(_png(200, 200))
    assert image is None and reason == "too_large"


def test_pillow_bomb_error_is_reported_as_too_large(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)  # Pillow raises above 2x this
    image, reason = images.load_image(_png(100, 100))
    assert image is None and reason == "too_large"


def test_pillow_bomb_warning_is_not_decoded_either(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 6000)  # 10000 px: warning zone (1x-2x)
    image, reason = images.load_image(_png(100, 100))
    assert image is None and reason == "too_large"


def test_truncated_image_is_undecodable():
    image, reason = images.load_image(_png()[:60])
    assert image is None and reason == "undecodable"


def test_exif_orientation_is_applied():
    buf = io.BytesIO()
    img = Image.new("RGB", (100, 60), (10, 200, 10))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise to display
    img.save(buf, "JPEG", exif=exif)
    image, reason = images.load_image(buf.getvalue())
    assert reason == "ok" and image.size == (60, 100)


def test_small_images_still_skipped():
    image, reason = images.load_image(_png(20, 20))
    assert image is None and reason == "too_small"


# --- converter -----------------------------------------------------------------------------------

_EMF = b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 8


def _fake_converter(tmp_path, monkeypatch, body: str):
    exe = tmp_path / "fake-inkscape"
    exe.write_text("#!/bin/sh\n" + body + "\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(images, "converter", lambda: ("inkscape", str(exe)))
    monkeypatch.setenv("DOCEXTRACT_TMP_DIR", str(tmp_path))


_WRITE_PNG = 'out="${{3#--export-filename=}}"; head -c {n} /dev/zero > "$out"'


def test_converter_output_over_cap_is_refused(tmp_path, monkeypatch):
    _fake_converter(tmp_path, monkeypatch, _WRITE_PNG.format(n=5000))
    monkeypatch.setenv("DOCEXTRACT_MAX_CONVERT_OUTPUT_BYTES", "1000")
    image, reason = images.load_image(_EMF, "a.emf")
    assert image is None and reason == "conversion_failed"


def test_converter_timeout_returns_promptly(tmp_path, monkeypatch):
    _fake_converter(tmp_path, monkeypatch, "sleep 30 & wait")
    monkeypatch.setenv("DOCEXTRACT_CONVERT_TIMEOUT_SECONDS", "1")
    start = time.time()
    image, reason = images.load_image(_EMF, "a.emf")
    assert image is None and reason == "conversion_failed"
    assert time.time() - start < 8


def test_metafile_input_cap(tmp_path, monkeypatch):
    _fake_converter(tmp_path, monkeypatch, "exit 0")
    monkeypatch.setenv("DOCEXTRACT_MAX_METAFILE_BYTES", "10")
    image, reason = images.load_image(_EMF, "a.emf")
    assert image is None and reason == "too_large"


def test_converter_runs_with_a_scrubbed_environment(tmp_path, monkeypatch):
    marker = tmp_path / "env.txt"
    _fake_converter(tmp_path, monkeypatch, f'env > "{marker}"; exit 1')
    monkeypatch.setenv("SOME_SECRET_TOKEN", "hunter2")
    images.load_image(_EMF, "a.emf")
    assert marker.exists() and "SOME_SECRET_TOKEN" not in marker.read_text()


def test_no_converter_is_still_reported_as_unsupported(monkeypatch):
    monkeypatch.setattr(images, "converter", lambda: None)
    image, reason = images.load_image(_EMF, "a.emf")
    assert image is None and reason == "unsupported_vector"
