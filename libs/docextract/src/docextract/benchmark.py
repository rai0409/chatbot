"""Compare OCR engines / model versions on your own images.

    python -m docextract compare-ocr scan1.png scan2.png --paddle-versions PP-OCRv6,PP-OCRv5 \
        --engines paddle,tesseract [--expected expected.txt]

For every image and configuration: recognised text, mean confidence, seconds
and, with --expected (one line per image), the character accuracy
(1 - edit distance / length of the expected text). Use representative
documents of the deployment (scans, photos, vertical text) before choosing.
"""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional, Sequence


def char_accuracy(expected: str, actual: str) -> float:
    """1 - Levenshtein(expected, actual) / len(expected), ignoring whitespace."""
    a = "".join(expected.split())
    b = "".join(actual.split())
    if not a:
        return 1.0 if not b else 0.0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return max(0.0, 1.0 - previous[-1] / len(a))


def _configurations(engines: Sequence[str], paddle_versions: Sequence[str]) -> List[Dict[str, str]]:
    configs: List[Dict[str, str]] = []
    for engine in engines:
        if engine == "paddle":
            for version in paddle_versions or ["PP-OCRv6"]:
                configs.append({"engine": "paddle", "DOCEXTRACT_PADDLE_OCR_VERSION": version})
        else:
            configs.append({"engine": engine})
    return configs


def compare(images: Sequence, *, engines: Sequence[str], paddle_versions: Sequence[str] = (),
            expected: Optional[Sequence[str]] = None) -> List[Dict]:
    from docextract import ocr

    rows: List[Dict] = []
    for config in _configurations(engines, paddle_versions):
        saved = {k: os.environ.get(k) for k in config if k != "engine"}
        os.environ.update({k: v for k, v in config.items() if k != "engine"})
        ocr._ENGINES.pop(config["engine"], None)  # build the engine for this configuration
        try:
            if images:
                ocr.ocr_image(images[0], engines=[config["engine"]])  # warm-up: model loading is not timed
            for index, image in enumerate(images):
                started = time.perf_counter()
                result = ocr.ocr_image(image, engines=[config["engine"]])
                row = {
                    "image": index,
                    "engine": result.engine if result.ok else config["engine"],
                    "config": {k: v for k, v in config.items() if k != "engine"},
                    "seconds": round(time.perf_counter() - started, 3),
                    "confidence": result.confidence,
                    "text": result.text,
                    "errors": result.errors,
                }
                if expected is not None and index < len(expected):
                    row["char_accuracy"] = round(char_accuracy(expected[index], result.text), 4)
                rows.append(row)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            ocr._ENGINES.pop(config["engine"], None)
    return rows
