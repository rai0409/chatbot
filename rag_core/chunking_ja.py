from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import config


DOC_TYPE_FAQ = "faq_glossary"
DOC_TYPE_PROCEDURE = "procedure_howto"
DOC_TYPE_POLICY = "policy_spec"
DOC_TYPE_TABLE = "table_like"

_DOC_TYPE_ALIASES = {
    "faq": DOC_TYPE_FAQ,
    "faq_glossary": DOC_TYPE_FAQ,
    "glossary": DOC_TYPE_FAQ,
    "term": DOC_TYPE_FAQ,
    "procedure": DOC_TYPE_PROCEDURE,
    "howto": DOC_TYPE_PROCEDURE,
    "how_to": DOC_TYPE_PROCEDURE,
    "procedure_howto": DOC_TYPE_PROCEDURE,
    "policy": DOC_TYPE_POLICY,
    "spec": DOC_TYPE_POLICY,
    "policy_spec": DOC_TYPE_POLICY,
    "table": DOC_TYPE_TABLE,
    "table_like": DOC_TYPE_TABLE,
}

_DEFAULT_TARGETS: Dict[str, Tuple[int, int]] = {
    DOC_TYPE_FAQ: (
        int(getattr(config, "JA_CHUNK_TARGET_FAQ_MIN_CHARS", 80)),
        int(getattr(config, "JA_CHUNK_TARGET_FAQ_MAX_CHARS", 300)),
    ),
    DOC_TYPE_PROCEDURE: (
        int(getattr(config, "JA_CHUNK_TARGET_PROCEDURE_MIN_CHARS", 300)),
        int(getattr(config, "JA_CHUNK_TARGET_PROCEDURE_MAX_CHARS", 900)),
    ),
    DOC_TYPE_POLICY: (
        int(getattr(config, "JA_CHUNK_TARGET_POLICY_MIN_CHARS", 400)),
        int(getattr(config, "JA_CHUNK_TARGET_POLICY_MAX_CHARS", 1200)),
    ),
    DOC_TYPE_TABLE: (
        int(getattr(config, "JA_CHUNK_TARGET_TABLE_MIN_CHARS", 80)),
        int(getattr(config, "JA_CHUNK_TARGET_TABLE_MAX_CHARS", 500)),
    ),
}

_HEADING_RE = re.compile(
    r"^(第[0-9一二三四五六七八九十]+[章節条]|[0-9]+[\.．\)]|[0-9]+\-[0-9]+|[■□◆◇●○▲△]+)\s*(.+)$"
)
_PROC_HEADING_RE = re.compile(r"^(第[0-9一二三四五六七八九十]+[章節条]|[■□◆◇●○▲△]+)\s*(.+)$")
_STEP_RE = re.compile(r"^\s*([0-9]+[\.\)]|[①-⑳]|手順[0-9一二三四五六七八九十]+)\s*(.+)$")
_TABLE_SPLIT_RE = re.compile(r"\s*\|\s*")
_BLANK_LINE_RE = re.compile(r"\n{2,}")


def normalize_doc_type(doc_type: str) -> str:
    key = (doc_type or "").strip().lower()
    return _DOC_TYPE_ALIASES.get(key, DOC_TYPE_POLICY)


def build_ja_chunk_records(
    *,
    doc_id: str,
    source_doc: str,
    text: str,
    doc_type: str,
    title: str = "",
    source_pages: Sequence[int] | None = None,
    base_chunk_index: int = 0,
    chunk_type: str = "pdf",
    quality: str = "high",
    searchable: int = 1,
) -> List[Dict[str, Any]]:
    normalized_doc_type = normalize_doc_type(doc_type)
    norm_text = _normalize_text(text)
    if not norm_text:
        return []

    target_min, target_max = _DEFAULT_TARGETS[normalized_doc_type]
    sections = _build_sections(
        norm_text,
        doc_type=normalized_doc_type,
        title=title,
    )
    if not sections:
        sections = [
            {
                "section_path": [title] if title else [],
                "display_text": norm_text,
                "searchable_text": norm_text,
                "faq_question": "",
                "aliases": [],
            }
        ]

    pages = [int(p) for p in (source_pages or []) if str(p).strip() != ""]
    page_scope = _page_scope_token(pages)
    call_hash = hashlib.sha1(norm_text.encode("utf-8")).hexdigest()[:10]
    call_scope = f"{page_scope}:b{int(base_chunk_index)}:h{call_hash}"
    out: List[Dict[str, Any]] = []
    chunk_index = int(base_chunk_index)

    for sec_idx, section in enumerate(sections, start=1):
        section_path = [str(x).strip() for x in (section.get("section_path") or []) if str(x).strip()]
        parent_id = f"{doc_id}:ja:{call_scope}:parent:{sec_idx}"
        parent_display_text = str(section.get("display_text") or "").strip()
        parent_searchable_text = _join_non_empty(
            [title, " > ".join(section_path), str(section.get("searchable_text") or parent_display_text)]
        )

        child_texts = _split_by_target(
            parent_display_text,
            target_min=target_min,
            target_max=target_max,
        )
        if not child_texts:
            child_texts = [parent_display_text]
        child_ids = [f"{parent_id}:child:{idx}" for idx in range(1, len(child_texts) + 1)]

        parent_record = _base_record(
            chunk_id=parent_id,
            text=parent_display_text,
            doc_id=doc_id,
            source_doc=source_doc,
            source_pages=pages,
            chunk_index=chunk_index,
            chunk_type=chunk_type,
            quality=quality,
            searchable=searchable,
        )
        parent_record.update(
            {
                "doc_type": normalized_doc_type,
                "title": title,
                "section_path": section_path,
                "chunk_role": "parent",
                "parent_chunk_id": None,
                "child_chunk_ids": child_ids,
                "searchable_text": parent_searchable_text,
                "display_text": parent_display_text,
                "faq_question": str(section.get("faq_question") or ""),
                "aliases": list(section.get("aliases") or []),
            }
        )
        out.append(parent_record)
        chunk_index += 1

        for child_id, child_text in zip(child_ids, child_texts):
            child_display_text = child_text.strip()
            child_searchable_text = _join_non_empty(
                [
                    title,
                    " > ".join(section_path),
                    str(section.get("faq_question") or ""),
                    str(section.get("searchable_text") or ""),
                    child_display_text,
                ]
            )
            child_record = _base_record(
                chunk_id=child_id,
                text=child_display_text,
                doc_id=doc_id,
                source_doc=source_doc,
                source_pages=pages,
                chunk_index=chunk_index,
                chunk_type=chunk_type,
                quality=quality,
                searchable=searchable,
            )
            child_record.update(
                {
                    "doc_type": normalized_doc_type,
                    "title": title,
                    "section_path": section_path,
                    "chunk_role": "child",
                    "parent_chunk_id": parent_id,
                    "child_chunk_ids": [],
                    "searchable_text": child_searchable_text,
                    "display_text": child_display_text,
                    "faq_question": str(section.get("faq_question") or ""),
                    "aliases": list(section.get("aliases") or []),
                }
            )
            out.append(child_record)
            chunk_index += 1

    return out


def _base_record(
    *,
    chunk_id: str,
    text: str,
    doc_id: str,
    source_doc: str,
    source_pages: Sequence[int],
    chunk_index: int,
    chunk_type: str,
    quality: str,
    searchable: int,
) -> Dict[str, Any]:
    return {
        "id": chunk_id,
        "text": text,
        "source_doc": source_doc,
        "source_pages": list(source_pages),
        "doc_id": doc_id,
        "chunk_index": int(chunk_index),
        "searchable": int(searchable),
        "type": chunk_type,
        "quality": quality,
    }


def _normalize_text(text: str) -> str:
    norm = unicodedata.normalize("NFKC", text or "")
    norm = norm.replace("\r\n", "\n").replace("\r", "\n")
    norm = re.sub(r"[ \t]+", " ", norm)
    norm = re.sub(r"\n{3,}", "\n\n", norm)
    return norm.strip()


def _page_scope_token(pages: Sequence[int]) -> str:
    if not pages:
        return "pna"
    vals = [str(int(p)) for p in pages]
    return "p" + "-".join(vals)


def _join_non_empty(parts: Iterable[str]) -> str:
    return " ".join(str(p).strip() for p in parts if str(p).strip())


def _build_sections(text: str, *, doc_type: str, title: str) -> List[Dict[str, Any]]:
    if doc_type == DOC_TYPE_FAQ:
        return _build_faq_sections(text, title=title)
    if doc_type == DOC_TYPE_PROCEDURE:
        return _build_procedure_sections(text, title=title)
    if doc_type == DOC_TYPE_TABLE:
        return _build_table_sections(text, title=title)
    return _build_policy_sections(text, title=title)


def _build_faq_sections(text: str, *, title: str) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    blocks = [blk.strip() for blk in _BLANK_LINE_RE.split(text) if blk.strip()]
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        question = ""
        answer = ""
        term_aliases: List[str] = []
        q_match = re.match(r"^(Q[:：]\s*|質問[:：]\s*)(.+)$", lines[0], flags=re.IGNORECASE)
        if q_match:
            question = q_match.group(2).strip()
            for line in lines[1:]:
                a_match = re.match(r"^(A[:：]\s*|回答[:：]\s*)(.+)$", line, flags=re.IGNORECASE)
                if a_match:
                    answer = a_match.group(2).strip()
                elif answer:
                    answer = f"{answer} {line}".strip()
        elif "：" in lines[0] or ":" in lines[0]:
            head, tail = re.split(r"[:：]", lines[0], maxsplit=1)
            if 1 <= len(head.strip()) <= 80:
                question = head.strip()
                answer = tail.strip()
                term_aliases.append(question)
        if not question:
            first = lines[0]
            if len(first) <= 80:
                question = first
                answer = " ".join(lines[1:]).strip()
        display = _join_non_empty([f"Q: {question}" if question else "", f"A: {answer}" if answer else block])
        if not display:
            display = block
        sections.append(
            {
                "section_path": [question or "FAQ項目"],
                "display_text": display,
                "searchable_text": _join_non_empty([question, answer, block]),
                "faq_question": question,
                "aliases": term_aliases,
            }
        )
    return sections


def _split_headed_blocks(text: str, *, fallback_title: str) -> List[Tuple[List[str], str]]:
    blocks: List[Tuple[List[str], str]] = []
    lines = [ln.rstrip() for ln in text.splitlines()]
    current_path: List[str] = [fallback_title] if fallback_title else []
    current_body: List[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if current_body and current_body[-1] != "":
                current_body.append("")
            continue
        match = _HEADING_RE.match(line)
        if match:
            if current_body:
                body = "\n".join(current_body).strip()
                if body:
                    blocks.append((list(current_path), body))
            heading = _join_non_empty([match.group(1), match.group(2)])
            current_path = [heading]
            current_body = []
            continue
        current_body.append(line)
    if current_body:
        body = "\n".join(current_body).strip()
        if body:
            blocks.append((list(current_path), body))
    return blocks


def _build_procedure_sections(text: str, *, title: str) -> List[Dict[str, Any]]:
    blocks = _split_procedure_blocks(text, fallback_title=title or "手順")
    if not blocks:
        blocks = [([title or "手順"], text)]
    sections: List[Dict[str, Any]] = []
    for path, body in blocks:
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        pre: List[str] = []
        steps: List[str] = []
        notes: List[str] = []
        for ln in lines:
            if ln.startswith(("前提", "事前", "準備")):
                pre.append(ln)
                continue
            if ln.startswith(("注意", "補足", "注記")):
                notes.append(ln)
                continue
            sm = _STEP_RE.match(ln)
            if sm:
                steps.append(_join_non_empty([sm.group(1), sm.group(2)]))
                continue
            if steps:
                steps[-1] = _join_non_empty([steps[-1], ln])
            else:
                pre.append(ln)
        display = "\n".join(
            part
            for part in [
                "\n".join(pre).strip(),
                "\n".join(steps).strip(),
                "\n".join(notes).strip(),
            ]
            if part
        ).strip()
        if not display:
            display = body
        sections.append(
            {
                "section_path": path,
                "display_text": display,
                "searchable_text": _join_non_empty([" ".join(path), display]),
                "faq_question": "",
                "aliases": [],
            }
        )
    return sections


def _split_procedure_blocks(text: str, *, fallback_title: str) -> List[Tuple[List[str], str]]:
    blocks: List[Tuple[List[str], str]] = []
    lines = [ln.rstrip() for ln in text.splitlines()]
    current_path: List[str] = [fallback_title] if fallback_title else []
    current_body: List[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if current_body and current_body[-1] != "":
                current_body.append("")
            continue
        match = _PROC_HEADING_RE.match(line)
        if match:
            if current_body:
                body = "\n".join(current_body).strip()
                if body:
                    blocks.append((list(current_path), body))
            heading = _join_non_empty([match.group(1), match.group(2)])
            current_path = [heading]
            current_body = []
            continue
        current_body.append(line)
    if current_body:
        body = "\n".join(current_body).strip()
        if body:
            blocks.append((list(current_path), body))
    return blocks


def _build_policy_sections(text: str, *, title: str) -> List[Dict[str, Any]]:
    blocks = _split_headed_blocks(text, fallback_title=title or "本文")
    if not blocks:
        blocks = [([title or "本文"], text)]
    sections: List[Dict[str, Any]] = []
    for path, body in blocks:
        cleaned = _normalize_text(body)
        sections.append(
            {
                "section_path": path,
                "display_text": cleaned,
                "searchable_text": _join_non_empty([" ".join(path), cleaned]),
                "faq_question": "",
                "aliases": [],
            }
        )
    return sections


def _build_table_sections(text: str, *, title: str) -> List[Dict[str, Any]]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    table_title = title or "テーブル"
    header: List[str] = []
    rows: List[List[str]] = []
    for line in lines:
        if "|" not in line:
            if not rows and not header:
                table_title = line
            continue
        cols = [c.strip() for c in _TABLE_SPLIT_RE.split(line.strip("| ")) if c.strip()]
        if not cols:
            continue
        if not header:
            header = cols
            continue
        rows.append(cols)
    if not rows and header:
        rows = [header]
        header = [f"列{idx}" for idx in range(1, len(rows[0]) + 1)]
    sections: List[Dict[str, Any]] = []
    if not rows:
        return [
            {
                "section_path": [table_title],
                "display_text": text,
                "searchable_text": text,
                "faq_question": "",
                "aliases": [],
            }
        ]
    for idx, row in enumerate(rows, start=1):
        pairs: List[str] = []
        for col_idx, val in enumerate(row):
            key = header[col_idx] if col_idx < len(header) else f"列{col_idx + 1}"
            pairs.append(f"{key}={val}")
        flat = f"{table_title} / " + " / ".join(pairs)
        sections.append(
            {
                "section_path": [table_title, f"row-{idx}"],
                "display_text": flat,
                "searchable_text": flat,
                "faq_question": "",
                "aliases": header,
            }
        )
    return sections


def _split_by_target(text: str, *, target_min: int, target_max: int) -> List[str]:
    body = (text or "").strip()
    if not body:
        return []
    if len(body) <= target_max:
        return [body]

    paragraphs = [p.strip() for p in _BLANK_LINE_RE.split(body) if p.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [p.strip() for p in body.split("\n") if p.strip()] or [body]

    out: List[str] = []
    cur = ""
    for para in paragraphs:
        candidate = para if not cur else f"{cur}\n{para}"
        if len(candidate) <= target_max:
            cur = candidate
            continue
        if cur:
            out.append(cur.strip())
            cur = ""
        if len(para) > target_max:
            out.extend(_split_by_sentence_then_window(para, target_min=target_min, target_max=target_max))
        else:
            cur = para
    if cur:
        out.append(cur.strip())

    merged: List[str] = []
    for chunk in out:
        if merged and len(merged[-1]) < target_min:
            candidate = f"{merged[-1]}\n{chunk}".strip()
            if len(candidate) <= int(target_max * 1.25):
                merged[-1] = candidate
                continue
        merged.append(chunk)
    return [m.strip() for m in merged if m.strip()]


def _split_by_sentence_then_window(text: str, *, target_min: int, target_max: int) -> List[str]:
    sentences = [s.strip() for s in re.split(r"(?<=[。！？])\s*", text) if s.strip()]
    if len(sentences) <= 1:
        fallback_overlap = int(getattr(config, "JA_CHUNK_FALLBACK_OVERLAP_CHARS", 80))
        return _split_fixed(text, size=target_max, overlap=fallback_overlap)

    out: List[str] = []
    cur = ""
    for sent in sentences:
        candidate = sent if not cur else f"{cur}{sent}"
        if len(candidate) <= target_max:
            cur = candidate
            continue
        if cur:
            out.append(cur.strip())
            cur = sent
        else:
            out.extend(_split_fixed(sent, size=target_max, overlap=config.JA_CHUNK_FALLBACK_OVERLAP_CHARS))
            cur = ""
    if cur:
        out.append(cur.strip())

    if any(len(chunk) > target_max * 1.25 for chunk in out):
        repaired: List[str] = []
        for chunk in out:
            if len(chunk) > target_max * 1.25:
                fallback_overlap = int(getattr(config, "JA_CHUNK_FALLBACK_OVERLAP_CHARS", 80))
                repaired.extend(_split_fixed(chunk, size=target_max, overlap=fallback_overlap))
            else:
                repaired.append(chunk)
        out = repaired

    merged: List[str] = []
    for chunk in out:
        if merged and len(merged[-1]) < target_min:
            merged[-1] = f"{merged[-1]}{chunk}".strip()
        else:
            merged.append(chunk)
    return [m for m in merged if m]


def _split_fixed(text: str, *, size: int, overlap: int) -> List[str]:
    size = max(1, int(size))
    overlap = max(0, min(int(overlap), size - 1))
    out: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + size)
        part = text[start:end].strip()
        if part:
            out.append(part)
        if end >= n:
            break
        start = max(0, end - overlap)
    return out
