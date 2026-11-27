"""Minimal stdlib PPTX reader (zipfile + ElementTree).

Emits one slide chunk per slide (title + body text) and table_row chunks for
slide tables. No python-pptx dependency. Text inside slide images is added as
"画像内テキスト: ..." only with OFFICE_OCR_IMAGES=true (stage 9).

Also handled (v2_08):
- slide order follows the presentation (ppt/presentation.xml sldIdLst), not
  the file names; slide_number is the position shown to the audience
- speaker notes (notesSlide) are appended to the slide text as "ノート: ..."
- hidden slides (show="0") are skipped unless PPTX_INCLUDE_HIDDEN_SLIDES=true

Reading order (2026-11 conversion-accuracy work):
- shapes are read top to bottom, then left to right, not in XML (z-order)
  order. Shapes whose tops differ by at most PPTX_ROW_TOLERANCE_EMU (default
  0.2 inch) count as one row. A placeholder without its own position takes
  the one of the matching placeholder in the slide layout / master; a shape
  with no position at all keeps its place after the shape before it.
- group shapes (grpSp) are read recursively; their child coordinates are
  mapped through the group transform (off/ext/chOff/chExt), nested groups
  accumulate
- charts: title, series names and category labels as "グラフ: ...",
  "系列: ...", "項目: ..." lines; SmartArt: the text of its data part
"""

from __future__ import annotations

import logging
import math
import os
import posixpath
import re
import xml.etree.ElementTree as ET  # element types only; parsing uses defusedxml  # nosec B405
from defusedxml.ElementTree import fromstring as _safe_fromstring
from rag_core.document_converters.limits import open_office_zip, read_member
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rag_core.document_converters.base import (
    build_chunk,
    compact,
    ocr_image_bytes,
    office_ocr_enabled,
    table_rows_to_chunks,
)

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_LOG = logging.getLogger(__name__)
_C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_DGM = "{http://schemas.openxmlformats.org/drawingml/2006/diagram}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_TITLE_PH_TYPES = {"title", "ctrTitle"}
_NOTES_BODY_PH_TYPES = {"body"}


def _shape_paragraph_lines(shape: ET.Element) -> List[str]:
    lines = []
    for paragraph in shape.iter(f"{_A}p"):
        line = compact("".join(t.text or "" for t in paragraph.iter(f"{_A}t")))
        if line:
            lines.append(line)
    return lines


def _is_title_shape(shape: ET.Element) -> bool:
    for ph in shape.iter(f"{_P}ph"):
        if (ph.get("type") or "") in _TITLE_PH_TYPES:
            return True
    return False


def _rels(zf: zipfile.ZipFile, part: str) -> Dict[str, Dict[str, str]]:
    directory, name = posixpath.split(part)
    rels_name = posixpath.join(directory, "_rels", name + ".rels")
    if rels_name not in zf.namelist():
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for rel in _safe_fromstring(read_member(zf, rels_name)).iter(_PKG_REL):
        target = rel.get("Target") or ""
        resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(directory, target))
        out[rel.get("Id") or ""] = {"target": resolved, "type": rel.get("Type") or ""}
    return out


def _slide_order(zf: zipfile.ZipFile) -> List[str]:
    names = zf.namelist()
    by_file = [m.group(0) for m in sorted((_SLIDE_RE.match(n) for n in names if _SLIDE_RE.match(n)), key=lambda m: int(m.group(1)))]
    if "ppt/presentation.xml" not in names:
        return by_file
    rels = _rels(zf, "ppt/presentation.xml")
    ordered: List[str] = []
    for sld in _safe_fromstring(read_member(zf, "ppt/presentation.xml")).iter(f"{_P}sldId"):
        target = rels.get(sld.get(f"{_R}id") or "", {}).get("target")
        if target and target in names:
            ordered.append(target)
    return ordered or by_file


def _notes_text(zf: zipfile.ZipFile, slide_part: str) -> str:
    for rel in _rels(zf, slide_part).values():
        if rel["type"].endswith("/notesSlide") and rel["target"] in zf.namelist():
            root = _safe_fromstring(read_member(zf, rel["target"]))
            lines: List[str] = []
            for shape in root.iter(f"{_P}sp"):
                ph_types = {ph.get("type") or "" for ph in shape.iter(f"{_P}ph")}
                if ph_types & _NOTES_BODY_PH_TYPES:
                    lines.extend(_shape_paragraph_lines(shape))
            return compact(" ".join(lines))
    return ""


def _image_text(zf: zipfile.ZipFile, slide_part: str) -> List[str]:
    lines: List[str] = []
    for rel in _rels(zf, slide_part).values():
        if rel["type"].endswith("/image") and rel["target"] in zf.namelist():
            text = ocr_image_bytes(read_member(zf, rel["target"]), rel["target"])
            if text:
                lines.append(compact(text))
    return lines


# --- reading order ------------------------------------------------------------------------------

_Transform = Tuple[float, float, float, float]  # x = a + b * x_child ; y = c + d * y_child
_IDENTITY: _Transform = (0.0, 1.0, 0.0, 1.0)
_MAX_CATEGORIES = 50


_DEFAULT_ROW_TOLERANCE = 182880  # 0.2 inch in EMU
_MAX_GROUP_DEPTH = 32  # a hostile deeply nested grpSp must not exhaust the Python stack


def _row_tolerance() -> int:
    raw = str(os.getenv("PPTX_ROW_TOLERANCE_EMU", "") or "").strip()
    if not raw:
        return _DEFAULT_ROW_TOLERANCE
    try:
        return max(0, int(raw))
    except ValueError:
        _LOG.warning("PPTX_ROW_TOLERANCE_EMU=%r is not an integer; using %d", raw, _DEFAULT_ROW_TOLERANCE)
        return _DEFAULT_ROW_TOLERANCE


def _offset(xfrm: Optional[ET.Element]) -> Optional[Tuple[float, float]]:
    off = xfrm.find(f"{_A}off") if xfrm is not None else None
    if off is None:
        return None
    try:
        x, y = float(off.get("x") or 0), float(off.get("y") or 0)
    except ValueError:
        return None
    return (x, y) if math.isfinite(x) and math.isfinite(y) else None


def _shape_xfrm(shape: ET.Element) -> Optional[ET.Element]:
    tag = shape.tag
    if tag == f"{_P}graphicFrame":
        return shape.find(f"{_P}xfrm")
    if tag == f"{_P}grpSp":
        return shape.find(f"{_P}grpSpPr/{_A}xfrm")
    return shape.find(f"{_P}spPr/{_A}xfrm")


def _group_transform(group: ET.Element, outer: _Transform) -> _Transform:
    xfrm = _shape_xfrm(group)
    if xfrm is None:
        return outer

    def _pair(tag: str, a: str, b: str) -> Tuple[float, float]:
        el = xfrm.find(f"{_A}{tag}")
        try:
            return (float(el.get(a) or 0), float(el.get(b) or 0)) if el is not None else (0.0, 0.0)
        except ValueError:
            return 0.0, 0.0

    (ox, oy), (ex, ey) = _pair("off", "x", "y"), _pair("ext", "cx", "cy")
    (cx, cy), (cex, cey) = _pair("chOff", "x", "y"), _pair("chExt", "cx", "cy")
    sx = ex / cex if cex else 1.0
    sy = ey / cey if cey else 1.0
    a, b, c, d = outer
    # child -> group space: ox + (x - cx) * sx ; then group space -> outer space
    return (a + b * (ox - cx * sx), b * sx, c + d * (oy - cy * sy), d * sy)


def _placeholder_key(shape: ET.Element) -> Optional[Tuple[str, str]]:
    ph = shape.find(f"{_P}nvSpPr/{_P}nvPr/{_P}ph")
    if ph is None:
        return None
    return (ph.get("type") or "body", ph.get("idx") or "")


def _placeholder_positions(zf: zipfile.ZipFile, slide_part: str) -> Dict[Tuple[str, str], Tuple[float, float]]:
    """Positions of the layout's (then the master's) placeholders, by (type, idx) and (type, "")."""
    positions: Dict[Tuple[str, str], Tuple[float, float]] = {}
    part = slide_part
    for _level in range(2):  # slide -> layout -> master
        target = next((r["target"] for r in _rels(zf, part).values()
                       if r["type"].endswith(("/slideLayout", "/slideMaster")) and r["target"] in zf.namelist()), None)
        if not target:
            break
        try:
            root = _safe_fromstring(read_member(zf, target))
        except Exception as exc:  # noqa: BLE001 - a broken layout only costs the position (no text is lost)
            _LOG.debug("pptx layout/master unreadable (%s)", type(exc).__name__)
            break
        for shape in root.iter(f"{_P}sp"):
            key = _placeholder_key(shape)
            position = _offset(_shape_xfrm(shape))
            if key is None or position is None:
                continue
            positions.setdefault(key, position)
            positions.setdefault((key[0], ""), position)
        part = target
    return positions


def _chart_lines(zf: zipfile.ZipFile, part: str) -> List[str]:
    root = _safe_fromstring(read_member(zf, part))
    lines: List[str] = []
    title = root.find(f"{_C}chart/{_C}title")
    if title is not None:
        text = compact(" ".join(t.text or "" for t in title.iter(f"{_A}t")))
        if text:
            lines.append(f"グラフ: {text}")
    series: List[str] = []
    categories: List[str] = []
    for ser in root.iter(f"{_C}ser"):
        tx = ser.find(f"{_C}tx")
        if tx is not None:
            name = compact(" ".join([v.text or "" for v in tx.iter(f"{_C}v")] + [t.text or "" for t in tx.iter(f"{_A}t")]))
            if name and name not in series:
                series.append(name)
        cat = ser.find(f"{_C}cat")
        if cat is not None:
            for v in cat.iter(f"{_C}v"):
                value = compact(v.text)
                if value and value not in categories and len(categories) < _MAX_CATEGORIES:
                    categories.append(value)
    if series:
        lines.append("系列: " + " ".join(series))
    if categories:
        lines.append("項目: " + " ".join(categories))
    return lines


def _smartart_lines(zf: zipfile.ZipFile, part: str) -> List[str]:
    root = _safe_fromstring(read_member(zf, part))
    lines: List[str] = []
    for pt in root.iter(f"{_DGM}pt"):
        text = compact("".join(t.text or "" for t in pt.iter(f"{_A}t")))
        if text:
            lines.append(text)
    return lines


def _frame_lines(zf: zipfile.ZipFile, frame: ET.Element, rels: Dict[str, Dict[str, str]],
                 warnings: Optional[List[Dict[str, str]]] = None) -> List[str]:
    """Chart / SmartArt text of a graphicFrame ([] for tables and anything else).

    An unreadable chart / diagram part is reported in `warnings` (and logged), never dropped silently."""
    data = frame.find(f"{_A}graphic/{_A}graphicData")
    if data is None:
        return []
    try:
        chart = data.find(f"{_C}chart")
        if chart is not None:
            target = rels.get(chart.get(f"{_R}id") or "", {}).get("target")
            return _chart_lines(zf, target) if target and target in zf.namelist() else []
        rel_ids = data.find(f"{_DGM}relIds")
        if rel_ids is not None:
            target = rels.get(rel_ids.get(f"{_R}dm") or "", {}).get("target")
            return _smartart_lines(zf, target) if target and target in zf.namelist() else []
    except Exception as exc:  # noqa: BLE001 - an unreadable chart never stops the slide, but is reported
        _LOG.warning("pptx chart/diagram text unreadable (%s)", type(exc).__name__)
        if warnings is not None:
            warnings.append({"code": "chart_or_diagram_unreadable", "error": type(exc).__name__})
        return []
    return []


def _walk_shapes(container: ET.Element, transform: _Transform, out: List[Dict[str, object]], depth: int = 0) -> None:
    """Flatten the shape tree in XML order with absolute positions (None when unknown)."""
    if depth > _MAX_GROUP_DEPTH:
        raise ValueError(f"pptx_group_depth_limit（上限 {_MAX_GROUP_DEPTH}）")
    for child in container:
        tag = child.tag
        if tag == f"{_MC}AlternateContent":
            choice = child.find(f"{_MC}Choice")
            branch = choice if choice is not None else child.find(f"{_MC}Fallback")
            if branch is not None:
                _walk_shapes(branch, transform, out, depth + 1)
            continue
        if tag == f"{_P}grpSp":
            _walk_shapes(child, _group_transform(child, transform), out, depth + 1)
            continue
        if tag not in (f"{_P}sp", f"{_P}graphicFrame"):
            continue
        offset = _offset(_shape_xfrm(child))
        position = None
        if offset is not None:
            a, b, c, d = transform
            position = (c + d * offset[1], a + b * offset[0])  # (top, left)
        out.append({"shape": child, "position": position})


def _reading_order(items: List[Dict[str, object]], placeholders: Dict[Tuple[str, str], Tuple[float, float]]) -> List[Dict[str, object]]:
    previous: Tuple[float, float] = (float("-inf"), float("-inf"))
    for item in items:
        if item["position"] is None:
            key = _placeholder_key(item["shape"]) if item["shape"].tag == f"{_P}sp" else None  # type: ignore[union-attr]
            inherited = placeholders.get(key) or placeholders.get((key[0], "")) if key else None
            if inherited is not None:
                item["position"] = (inherited[1], inherited[0])
        if item["position"] is None:
            item["position"] = previous  # keeps its place after the shape before it
        previous = item["position"]  # type: ignore[assignment]
    ordered = sorted(enumerate(items), key=lambda pair: (pair[1]["position"][0], pair[0]))  # type: ignore[index]
    tolerance = _row_tolerance()
    rows: List[List[Tuple[int, Dict[str, object]]]] = []
    for pair in ordered:
        top = pair[1]["position"][0]  # type: ignore[index]
        if rows and top - rows[-1][0][1]["position"][0] <= tolerance:  # type: ignore[index]
            rows[-1].append(pair)
        else:
            rows.append([pair])
    result: List[Dict[str, object]] = []
    for row in rows:
        row.sort(key=lambda pair: (pair[1]["position"][1], pair[1]["position"][0], pair[0]))  # type: ignore[index]
        result.extend(item for _i, item in row)
    return result


def _include_hidden() -> bool:
    return str(os.getenv("PPTX_INCLUDE_HIDDEN_SLIDES", "")).strip().lower() in {"1", "true", "yes", "on"}


def convert_pptx(
    path: str | Path,
    *,
    tenant_id: str = "default",
    source_doc: str | None = None,
    doc_type: str | None = None,
) -> List[Dict]:
    path = Path(path)
    source_doc = source_doc or path.name
    resolved_doc_type = doc_type or "presentation"
    chunks: List[Dict] = []

    with open_office_zip(path) as zf:
        for slide_number, member in enumerate(_slide_order(zf), start=1):
            root = _safe_fromstring(read_member(zf, member))
            if root.get("show") == "0" and not _include_hidden():
                continue
            title_lines: List[str] = []
            body_lines: List[str] = []
            tables: List[ET.Element] = []
            slide_warnings: List[Dict[str, str]] = []
            tree = root.find(f"{_P}cSld/{_P}spTree")
            if tree is None:  # not a regular slide: keep the plain XML-order reading
                for shape in root.iter(f"{_P}sp"):
                    lines = _shape_paragraph_lines(shape)
                    (title_lines if _is_title_shape(shape) else body_lines).extend(lines)
                tables.extend(root.iter(f"{_A}tbl"))
            else:
                items: List[Dict[str, object]] = []
                _walk_shapes(tree, _IDENTITY, items)
                slide_rels = _rels(zf, member)
                for item in _reading_order(items, _placeholder_positions(zf, member)):
                    shape = item["shape"]
                    if shape.tag == f"{_P}graphicFrame":  # type: ignore[union-attr]
                        tables.extend(shape.iter(f"{_A}tbl"))  # type: ignore[union-attr]
                        body_lines.extend(_frame_lines(zf, shape, slide_rels, slide_warnings))  # type: ignore[arg-type]
                        continue
                    lines = _shape_paragraph_lines(shape)  # type: ignore[arg-type]
                    if not lines:
                        continue
                    if _is_title_shape(shape):  # type: ignore[arg-type]
                        title_lines.extend(lines)
                    else:
                        body_lines.extend(lines)
            notes = _notes_text(zf, member)
            image_lines = _image_text(zf, member) if office_ocr_enabled() else []

            slide_title: Optional[str] = compact(" ".join(title_lines)) or None
            slide_text_lines = ([slide_title] if slide_title else []) + body_lines
            if notes:
                slide_text_lines.append(f"ノート: {notes}")
            for image_line in image_lines:
                slide_text_lines.append(f"画像内テキスト: {image_line}")
            if slide_text_lines:
                text = "\n".join(slide_text_lines)
                chunks.append(
                    build_chunk(
                        source_type="pptx",
                        source_doc=source_doc,
                        location=f"s{slide_number}",
                        text=text,
                        chunk_type="slide",
                        chunk_index=len(chunks) + 1,
                        tenant_id=tenant_id,
                        doc_type=resolved_doc_type,
                        parser="stdlib_ooxml",
                        title=slide_title or source_doc,
                        extra={
                            "slide_number": slide_number,
                            "slide_title": slide_title,
                            "slide_part": member,
                            "has_notes": bool(notes),
                            "has_image_text": bool(image_lines),
                            **({"conversion_warnings": slide_warnings} if slide_warnings else {}),
                        },
                    )
                )

            for table_no, table in enumerate(tables, start=1):
                cell_rows = []
                for tr in table.findall(f"{_A}tr"):
                    row: List[str] = []
                    for tc in tr.findall(f"{_A}tc"):
                        # Merged cells: hMerge/vMerge continuation cells repeat
                        # the value to their left / above.
                        if tc.get("hMerge") == "1" and row:
                            row.append(row[-1])
                        elif tc.get("vMerge") == "1" and cell_rows and len(cell_rows[-1]) > len(row):
                            row.append(cell_rows[-1][len(row)])
                        else:
                            row.append(compact(" ".join(_shape_paragraph_lines(tc))))
                    cell_rows.append(row)
                if not cell_rows:
                    continue
                headers = cell_rows[0]
                data = [(row_no, cells) for row_no, cells in enumerate(cell_rows[1:], start=2)]
                table_chunks = table_rows_to_chunks(
                    headers=headers,
                    rows=data,
                    source_type="pptx",
                    source_doc=source_doc,
                    location_prefix=f"s{slide_number}:t{table_no}:",
                    tenant_id=tenant_id,
                    doc_type=resolved_doc_type,
                    parser="stdlib_ooxml",
                    start_index=len(chunks) + 1,
                    extra_builder=lambda row_number, values, _s=slide_number, _t=slide_title: {
                        "slide_number": _s,
                        "slide_title": _t,
                    },
                )
                for chunk in table_chunks:
                    if chunk["chunk_type"] == "row":
                        chunk["chunk_type"] = "table_row"
                chunks.extend(table_chunks)
    return chunks
