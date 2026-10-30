"""python -m docextract check | tables <pdf> | ocr <image> | compare-ocr <images...>"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="docextract")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="installed backends, versions and license notes")
    check.add_argument("--require-ocr", action="store_true", help="exit 1 when no configured OCR engine is installed")
    check.add_argument("--require-tables", action="store_true", help="exit 1 when no table backend is installed")
    tables_cmd = sub.add_parser("tables", help="extract the tables of a PDF")
    tables_cmd.add_argument("pdf")
    tables_cmd.add_argument("--backend", default=None)
    ocr_cmd = sub.add_parser("ocr", help="OCR one image (PNG/JPEG/TIFF/EMF/WMF)")
    ocr_cmd.add_argument("image")
    cmp_cmd = sub.add_parser("compare-ocr", help="compare OCR engines / PaddleOCR versions on your images")
    cmp_cmd.add_argument("images", nargs="+")
    cmp_cmd.add_argument("--engines", default="paddle,tesseract")
    cmp_cmd.add_argument("--paddle-versions", default="PP-OCRv6,PP-OCRv5")
    cmp_cmd.add_argument("--expected", default=None, help="text file, one expected line per image")
    args = parser.parse_args(argv)

    if args.command == "check":
        from docextract.health import report

        result = report()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.require_ocr and not result["ocr_engines_installed"]:
            return 1
        if args.require_tables and not result["table_backend"]:
            return 1
        return 0
    if args.command == "tables":
        from docextract.tables import extract_pdf_tables

        found = extract_pdf_tables(args.pdf, backend=args.backend)
        print(json.dumps({**found, "tables": [t.to_dict() for t in found["tables"]]}, ensure_ascii=False, indent=2))
        return 0 if found["backend"] else 1
    if args.command == "compare-ocr":
        from docextract.benchmark import compare
        from docextract.images import load_image

        images = []
        for raw in args.images:
            image, reason = load_image(Path(raw).read_bytes(), Path(raw).name)
            if image is None:
                print(f"skip {raw}: {reason}", file=sys.stderr)
                continue
            images.append(image)
        expected = Path(args.expected).read_text(encoding="utf-8").splitlines() if args.expected else None
        rows = compare(
            images,
            engines=[e.strip() for e in args.engines.split(",") if e.strip()],
            paddle_versions=[v.strip() for v in args.paddle_versions.split(",") if v.strip()],
            expected=expected,
        )
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    from docextract.ocr import ocr_image

    path = Path(args.image)
    result = ocr_image(path.read_bytes(), name=path.name)
    print(json.dumps({"engine": result.engine, "confidence": result.confidence, "text": result.text, "errors": result.errors}, ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
