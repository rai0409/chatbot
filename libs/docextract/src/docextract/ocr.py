"""Japanese OCR with interchangeable engines.

Engines (tried in DOCEXTRACT_OCR_ENGINES order, default "paddle,tesseract"):
the first installed engine that returns text with confidence of at least
DOCEXTRACT_OCR_ACCEPT_CONFIDENCE (default 60) wins; below that the next
engine is tried too and the more confident result is kept.

  paddle     PaddleOCR 3.7+ with PP-OCRv6 (DOCEXTRACT_PADDLE_OCR_VERSION,
             default "PP-OCRv6"; medium tier, lang "japan"). Apache-2.0.
             Published Japanese scores, PP-OCRv6_medium vs PP-OCRv5_server:
             recognition 90.5 vs 73.7, detection 84.3 vs 77.2. Older
             paddleocr (< 3.7) falls back to PP-OCRv5 automatically.
             DOCEXTRACT_PADDLE_TIER=small trades accuracy (88.2) for ~3x
             CPU speed; DOCEXTRACT_PADDLE_DET_MODEL_DIR / _REC_MODEL_DIR load
             models from local folders (offline / air-gapped sites).
  tesseract  Tesseract 5 LSTM through pytesseract, "jpn+eng"; vertical text
             is retried with "jpn_vert" when that model is installed and the
             horizontal pass is unsure. Apache-2.0. Light, lower accuracy.
  yomitoku   YomiToku (Japanese-specialised). Its open release is
             CC BY-NC-SA 4.0: commercial use needs the vendor's commercial
             license, so it only runs with DOCEXTRACT_YOMITOKU_LICENSED=true.

Confidence is reported 0..100 (mean over lines); callers decide what to do
with low-confidence text.
"""

from __future__ import annotations

from functools import lru_cache
from threading import Lock
from typing import Any, Dict, List, Optional, Sequence

from docextract._util import env_flag, env_float, env_list, env_str, normalize_ocr_text
from docextract.models import OcrLine, OcrResult

DEFAULT_ENGINES = "paddle,tesseract"
_ENGINE_LOCK = Lock()
_ENGINES: Dict[str, Any] = {}


class EngineUnavailable(RuntimeError):
    pass


# --- availability -------------------------------------------------------------------------


@lru_cache(maxsize=1)
def tesseract_languages() -> frozenset:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return frozenset(pytesseract.get_languages(config=""))
    except Exception:  # noqa: BLE001 - binary or wrapper missing
        return frozenset()


def _importable(module: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def engine_available(name: str) -> bool:
    if name == "tesseract":
        return "jpn" in tesseract_languages()
    if name == "paddle":
        return _importable("paddleocr") and _importable("paddle")
    if name == "yomitoku":
        return _importable("yomitoku") and env_flag("DOCEXTRACT_YOMITOKU_LICENSED")
    return False


def available_engines() -> List[str]:
    return [name for name in ("paddle", "yomitoku", "tesseract") if engine_available(name)]


def configured_engines() -> List[str]:
    return [e for e in env_list("DOCEXTRACT_OCR_ENGINES", DEFAULT_ENGINES) if e in ("paddle", "tesseract", "yomitoku")]


# --- preprocessing --------------------------------------------------------------------------


def _prepare_for_tesseract(image):
    """Grayscale, stretched contrast, small images upscaled (Tesseract wants
    roughly 20-30 px tall characters, i.e. ~300 dpi)."""
    from PIL import Image, ImageOps

    gray = ImageOps.autocontrast(image.convert("L"))
    short = min(gray.size)
    if short < 1000:
        factor = min(3.0, 1000.0 / max(1, short))
        gray = gray.resize((int(gray.width * factor), int(gray.height * factor)), Image.LANCZOS)
    return gray


def _bgr(image):
    import numpy as np

    return np.array(image.convert("RGB"))[:, :, ::-1].copy()


def _result(engine: str, lines: List[OcrLine]) -> OcrResult:
    text = normalize_ocr_text("\n".join(line.text for line in lines))
    scored = [line.confidence for line in lines if line.confidence is not None]
    confidence = round(sum(scored) / len(scored), 1) if scored else None
    return OcrResult(text=text, engine=engine, confidence=confidence, lines=lines)


# --- engines ----------------------------------------------------------------------------------


def _tesseract_pass(image, lang: str, psm: str) -> OcrResult:
    import pytesseract

    data = pytesseract.image_to_data(
        image, lang=lang, config=f"--oem 1 --psm {psm}", output_type=pytesseract.Output.DICT
    )
    grouped: Dict[tuple, List[tuple]] = {}
    for i, word in enumerate(data.get("text") or []):
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 0 or not str(word).strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        grouped.setdefault(key, []).append((str(word), conf, data["left"][i], data["top"][i], data["width"][i], data["height"][i]))
    lines = []
    for key in sorted(grouped):
        words = grouped[key]
        x0 = min(w[2] for w in words)
        y0 = min(w[3] for w in words)
        x1 = max(w[2] + w[4] for w in words)
        y1 = max(w[3] + w[5] for w in words)
        lines.append(
            OcrLine(
                text=" ".join(w[0] for w in words),
                confidence=round(sum(w[1] for w in words) / len(words), 1),
                bbox=(float(x0), float(y0), float(x1), float(y1)),
            )
        )
    return _result("tesseract", lines)


def _tesseract(image, tesseract_lang: Optional[str] = None, **_options) -> OcrResult:
    langs = tesseract_languages()
    if "jpn" not in langs:
        raise EngineUnavailable("tesseract with the jpn model is not installed")
    lang = tesseract_lang or env_str("DOCEXTRACT_TESSERACT_LANG") or ("jpn+eng" if "eng" in langs else "jpn")
    prepared = _prepare_for_tesseract(image)
    result = _tesseract_pass(prepared, lang, env_str("DOCEXTRACT_TESSERACT_PSM", "3"))
    retry_below = env_float("DOCEXTRACT_VERTICAL_RETRY_BELOW", 55.0)
    if "jpn_vert" in langs and (result.confidence is None or result.confidence < retry_below):
        vertical = _tesseract_pass(prepared, "jpn_vert", "5")
        if (vertical.confidence or 0) > (result.confidence or 0) + 5:
            vertical.engine = "tesseract:vertical"
            return vertical
    return result


def _engine(name: str, factory):
    with _ENGINE_LOCK:
        if name not in _ENGINES:
            _ENGINES[name] = factory()
        return _ENGINES[name]


_PADDLE_STATE: Dict[str, str] = {}


def paddle_ocr_version() -> Optional[str]:
    """OCR model version the PaddleOCR engine was built with (None before first use)."""
    return _PADDLE_STATE.get("version")


def _paddle_kwargs(version: str) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": True,
    }
    tier = env_str("DOCEXTRACT_PADDLE_TIER", "medium").lower()
    det_dir = env_str("DOCEXTRACT_PADDLE_DET_MODEL_DIR")
    rec_dir = env_str("DOCEXTRACT_PADDLE_REC_MODEL_DIR")
    if version == "PP-OCRv6" and (tier in ("small", "tiny") or det_dir or rec_dir):
        # Explicit models (lang/ocr_version are ignored by PaddleOCR then).
        # The tiny tier has no Japanese: small is the smallest one used here.
        size = "small" if tier in ("small", "tiny") else "medium"
        kwargs["text_detection_model_name"] = f"PP-OCRv6_{size}_det"
        kwargs["text_recognition_model_name"] = f"PP-OCRv6_{size}_rec"
        if det_dir:
            kwargs["text_detection_model_dir"] = det_dir
        if rec_dir:
            kwargs["text_recognition_model_dir"] = rec_dir
    else:
        kwargs["lang"] = env_str("DOCEXTRACT_PADDLE_LANG", "japan")
        kwargs["ocr_version"] = version
    device = env_str("DOCEXTRACT_DEVICE")
    if device:
        kwargs["device"] = device
    # oneDNN (MKL-DNN) on CPU: DOCEXTRACT_PADDLE_MKLDNN=auto (default) turns it
    # off automatically after paddlepaddle reports the known "onednn ...
    # not support" error (seen with paddlepaddle 3.3 + PP-OCRv6); false/true force it.
    mkldnn = env_str("DOCEXTRACT_PADDLE_MKLDNN", "auto").lower()
    if mkldnn == "false" or _PADDLE_STATE.get("mkldnn") == "off":
        kwargs["enable_mkldnn"] = False
    elif mkldnn == "true":
        kwargs["enable_mkldnn"] = True
    engine = env_str("DOCEXTRACT_PADDLE_ENGINE")  # e.g. onnxruntime (PaddleOCR 3.7+)
    if engine:
        kwargs["engine"] = engine
    return kwargs


def _paddle_factory():
    from paddleocr import PaddleOCR

    version = env_str("DOCEXTRACT_PADDLE_OCR_VERSION", "PP-OCRv6")
    try:
        engine = PaddleOCR(**_paddle_kwargs(version))
    except ValueError:
        if version != "PP-OCRv6":
            raise
        # paddleocr < 3.7 does not know PP-OCRv6: use PP-OCRv5 (and say so).
        version = "PP-OCRv5"
        engine = PaddleOCR(**_paddle_kwargs(version))
    except TypeError:  # PaddleOCR 2.x
        version = "PP-OCRv4"
        engine = PaddleOCR(lang=env_str("DOCEXTRACT_PADDLE_LANG", "japan"), use_angle_cls=True, show_log=False)
    _PADDLE_STATE["version"] = version
    return engine


def _paddle(image, **_options) -> OcrResult:
    if not engine_available("paddle"):
        raise EngineUnavailable("paddleocr / paddlepaddle are not installed")
    engine = _engine("paddle", _paddle_factory)
    array = _bgr(image)
    lines: List[OcrLine] = []
    if hasattr(engine, "predict"):
        try:
            results = engine.predict(array) or []
        except NotImplementedError as exc:
            auto = env_str("DOCEXTRACT_PADDLE_MKLDNN", "auto").lower() == "auto"
            if not (auto and "onednn" in str(exc).lower() and _PADDLE_STATE.get("mkldnn") != "off"):
                raise
            _PADDLE_STATE["mkldnn"] = "off"
            with _ENGINE_LOCK:
                _ENGINES.pop("paddle", None)
            engine = _engine("paddle", _paddle_factory)
            results = engine.predict(array) or []
        for res in results:
            data = res.json.get("res", res.json) if hasattr(res, "json") and isinstance(res.json, dict) else res
            texts = list(data.get("rec_texts") or [])
            scores = list(data.get("rec_scores") or [])
            boxes = data.get("rec_boxes")
            boxes = boxes.tolist() if hasattr(boxes, "tolist") else list(boxes or [])
            for i, text in enumerate(texts):
                bbox = tuple(float(v) for v in boxes[i][:4]) if i < len(boxes) and len(boxes[i]) >= 4 else None
                score = float(scores[i]) * 100 if i < len(scores) else None
                lines.append(OcrLine(text=str(text), confidence=score, bbox=bbox))
    else:
        for page in engine.ocr(array, cls=True) or []:
            for box, (text, score) in page or []:
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                lines.append(OcrLine(text=str(text), confidence=float(score) * 100, bbox=(min(xs), min(ys), max(xs), max(ys))))
    # Reading order: top-to-bottom, then left-to-right within a text row.
    lines.sort(key=lambda line: ((line.bbox[1] // 12, line.bbox[0]) if line.bbox else (0, 0)))
    return _result(f"paddle:{paddle_ocr_version() or 'unknown'}", lines)


def _yomitoku(image, **_options) -> OcrResult:
    if not env_flag("DOCEXTRACT_YOMITOKU_LICENSED"):
        raise EngineUnavailable("yomitoku requires a commercial license (DOCEXTRACT_YOMITOKU_LICENSED=true)")
    if not _importable("yomitoku"):
        raise EngineUnavailable("yomitoku is not installed")

    def factory():
        from yomitoku import OCR

        return OCR(device=env_str("DOCEXTRACT_DEVICE", "cpu"), visualize=False)

    engine = _engine("yomitoku", factory)
    output = engine(_bgr(image))
    results = output[0] if isinstance(output, tuple) else output
    lines = []
    for word in getattr(results, "words", None) or []:
        score = getattr(word, "rec_score", None)
        lines.append(OcrLine(text=str(getattr(word, "content", "") or ""), confidence=float(score) * 100 if score is not None else None))
    return _result("yomitoku", lines)


_RUNNERS = {"paddle": _paddle, "tesseract": _tesseract, "yomitoku": _yomitoku}


def ocr_image(
    image, *, engines: Optional[Sequence[str]] = None, name: str = "", tesseract_lang: Optional[str] = None
) -> OcrResult:
    """OCR one image (PIL image or encoded bytes, EMF/WMF included).

    tesseract_lang overrides DOCEXTRACT_TESSERACT_LANG for this call.
    """
    if isinstance(image, (bytes, bytearray)):
        from docextract.images import load_image

        image, reason = load_image(bytes(image), name)
        if image is None:
            return OcrResult(text="", engine="none", errors=[reason])
    order = list(engines) if engines else configured_engines()
    accept = env_float("DOCEXTRACT_OCR_ACCEPT_CONFIDENCE", 60.0)
    errors: List[str] = []
    best: Optional[OcrResult] = None

    def score(result: OcrResult) -> float:
        if not result.ok:
            return -1.0
        return result.confidence if result.confidence is not None else accept

    for engine_name in order:
        runner = _RUNNERS.get(engine_name)
        if runner is None:
            continue
        try:
            result = runner(image, tesseract_lang=tesseract_lang)
        except EngineUnavailable as exc:
            errors.append(f"{engine_name}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - one engine failing must not stop ingestion
            errors.append(f"{engine_name}: {type(exc).__name__}: {str(exc)[:200]}")
            continue
        if best is None or score(result) > score(best):
            best = result
        if score(best) >= accept:
            break  # confident enough: do not spend time on the next engine
    chosen = best or OcrResult(text="", engine="none")
    chosen.errors = errors
    return chosen


def ocr_available(engines: Optional[Sequence[str]] = None) -> bool:
    return any(engine_available(e) for e in (engines or configured_engines()))
