from __future__ import annotations

# Stage 7 regression tests: Office converters (v2_08), neighbour batching and
# index versions (v2_02), Qdrant backend (v2_06), freshness report (v2_09).

import json
import zipfile
from datetime import datetime, timezone

import pytest

import config
from rag_core import retrieval, store, vector_backend
from rag_core.chunking_ja import CHUNKER_VERSION, build_ja_chunk_records
from rag_core.document_converters import convert_file_to_canonical_chunks
from rag_core.retrieval import RetrievedChunk
from scripts.check_freshness import freshness_report

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _p(text, *, style=None, num=None, footnote=None):
    ppr = ""
    if style or num:
        ppr = "<w:pPr>"
        if style:
            ppr += f'<w:pStyle w:val="{style}"/>'
        if num:
            ppr += f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num}"/></w:numPr>'
        ppr += "</w:pPr>"
    ref = f'<w:r><w:footnoteReference w:id="{footnote}"/></w:r>' if footnote else ""
    return f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r>{ref}</w:p>"


def _docx(path, body, *, numbering=None, footnotes=None, header=None):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {W}><w:body>{body}</w:body></w:document>')
        if numbering:
            zf.writestr("word/numbering.xml", f'<?xml version="1.0"?><w:numbering {W}>{numbering}</w:numbering>')
        if footnotes:
            zf.writestr("word/footnotes.xml", f'<?xml version="1.0"?><w:footnotes {W}>{footnotes}</w:footnotes>')
        if header:
            zf.writestr("word/header1.xml", f'<?xml version="1.0"?><w:hdr {W}>{_p(header)}</w:hdr>')


# --- v2_08 DOCX -------------------------------------------------------------------------


def test_docx_content_controls_numbering_footnotes_and_header(tmp_path):
    numbering = (
        '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum>'
        '<w:num w:numId="5"><w:abstractNumId w:val="1"/></w:num>'
    )
    footnotes = '<w:footnote w:id="1"><w:p><w:r><w:t>2026年4月改定</w:t></w:r></w:p></w:footnote>'
    body = (
        _p("申請は月末までに行います。", num="5", footnote="1")
        + _p("承認は翌月に行います。", num="5")
        + "<w:sdt><w:sdtContent>" + _p("内容コントロールの本文です。") + "</w:sdtContent></w:sdt>"
    )
    path = tmp_path / "rules.docx"
    _docx(path, body, numbering=numbering, footnotes=footnotes, header="社内規程 第2版")

    chunks = convert_file_to_canonical_chunks(path)
    texts = [c["text"] for c in chunks]

    assert texts[0].startswith("1. 申請は月末までに行います。")
    assert "注1: 2026年4月改定" in texts[0]
    assert texts[1].startswith("2. 承認は翌月に行います。")
    assert "内容コントロールの本文です。" in texts
    assert chunks[-1]["chunk_type"] == "header_footer" and "社内規程 第2版" in chunks[-1]["text"]


def test_docx_merged_cells_keep_columns(tmp_path):
    def tc(text, extra=""):
        return f"<w:tc><w:tcPr>{extra}</w:tcPr>{_p(text) if text else '<w:p/>'}</w:tc>"

    table = (
        "<w:tbl>"
        f"<w:tr>{tc('区分')}{tc('条件')}{tc('対応')}</w:tr>"
        f'<w:tr>{tc("A", chr(60) + "w:vMerge w:val=" + chr(34) + "restart" + chr(34) + "/>")}{tc("7日以内")}{tc("全額返金")}</w:tr>'
        f'<w:tr>{tc("", "<w:vMerge/>")}{tc("30日以内", "<w:gridSpan w:val=" + chr(34) + "2" + chr(34) + "/>")}</w:tr>'
        "</w:tbl>"
    )
    path = tmp_path / "table.docx"
    _docx(path, table)

    rows = convert_file_to_canonical_chunks(path)

    assert rows[1]["text"] == "区分: A\n条件: 30日以内\n対応: 30日以内"


# --- v2_08 PPTX -------------------------------------------------------------------------------

_PNS = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _slide(text, *, hidden=False):
    show = ' show="0"' if hidden else ""
    return (
        f'<?xml version="1.0"?><p:sld {_PNS}{show}><p:cSld><p:spTree><p:sp><p:txBody>'
        f"<a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )


def _rels(entries):
    body = "".join(f'<Relationship Id="{i}" Type="{_REL}/{t}" Target="{target}"/>' for i, t, target in entries)
    return f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{body}</Relationships>'


def test_pptx_presentation_order_notes_and_hidden_slides(tmp_path):
    path = tmp_path / "deck.pptx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ppt/slides/slide1.xml", _slide("二枚目に表示"))
        zf.writestr("ppt/slides/slide2.xml", _slide("一枚目に表示"))
        zf.writestr("ppt/slides/slide3.xml", _slide("非表示", hidden=True))
        zf.writestr(
            "ppt/presentation.xml",
            f'<?xml version="1.0"?><p:presentation {_PNS}><p:sldIdLst>'
            '<p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId1"/><p:sldId id="258" r:id="rId3"/>'
            "</p:sldIdLst></p:presentation>",
        )
        zf.writestr(
            "ppt/_rels/presentation.xml.rels",
            _rels([("rId1", "slide", "slides/slide1.xml"), ("rId2", "slide", "slides/slide2.xml"), ("rId3", "slide", "slides/slide3.xml")]),
        )
        zf.writestr("ppt/slides/_rels/slide2.xml.rels", _rels([("rId9", "notesSlide", "../notesSlides/notesSlide1.xml")]))
        zf.writestr(
            "ppt/notesSlides/notesSlide1.xml",
            f'<?xml version="1.0"?><p:notes {_PNS}><p:cSld><p:spTree><p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>'
            "<p:txBody><a:p><a:r><a:t>補足の説明</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>",
        )

    chunks = convert_file_to_canonical_chunks(path)

    assert [c["slide_number"] for c in chunks] == [1, 2]
    assert chunks[0]["text"].startswith("一枚目に表示") and "ノート: 補足の説明" in chunks[0]["text"]
    assert all("非表示" not in c["text"] for c in chunks)


# --- v2_08 XLSX ---------------------------------------------------------------------------------

_SNS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def test_xlsx_merged_cells_hidden_rows_and_hidden_sheets(tmp_path):
    sheet = (
        f'<?xml version="1.0"?><worksheet {_SNS}><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>部署</t></is></c><c r="B1" t="inlineStr"><is><t>担当業務</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>総務</t></is></c><c r="B2" t="inlineStr"><is><t>備品</t></is></c></row>'
        '<row r="3"><c r="B3" t="inlineStr"><is><t>郵便</t></is></c></row>'
        '<row r="4" hidden="1"><c r="A4" t="inlineStr"><is><t>旧部署</t></is></c><c r="B4" t="inlineStr"><is><t>廃止</t></is></c></row>'
        '</sheetData><mergeCells count="1"><mergeCell ref="A2:A3"/></mergeCells></worksheet>'
    )
    hidden_sheet = f'<?xml version="1.0"?><worksheet {_SNS}><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>参照用</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>非公開</t></is></c></row></sheetData></worksheet>'
    workbook = (
        f'<?xml version="1.0"?><workbook {_SNS}><sheets>'
        '<sheet name="一覧" sheetId="1" r:id="rId1"/><sheet name="作業用" sheetId="2" state="hidden" r:id="rId2"/>'
        "</sheets></workbook>"
    )
    rels = (
        '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="x" Target="worksheets/sheet2.xml"/>'
        "</Relationships>"
    )
    path = tmp_path / "book.xlsx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
        zf.writestr("xl/worksheets/sheet2.xml", hidden_sheet)

    chunks = convert_file_to_canonical_chunks(path)
    texts = [c["text"] for c in chunks]

    assert texts == ["部署: 総務\n担当業務: 備品", "部署: 総務\n担当業務: 郵便"]


# --- v2_02 neighbours and versions -----------------------------------------------------------------


def test_neighbours_are_fetched_in_one_batch(monkeypatch):
    calls = []

    class _Collection:
        def get(self, where=None):
            calls.append(where)
            return {
                "documents": ["前", "後"],
                "metadatas": [
                    {"id": "n0", "doc_id": "d", "chunk_index": 0, "tenant_id": "default"},
                    {"id": "n3", "doc_id": "d", "chunk_index": 3, "tenant_id": "default"},
                ],
            }

    monkeypatch.setattr(retrieval.store, "get_vectorstore", lambda *a, **k: _Collection())
    seeds = [
        RetrievedChunk(text="s1", metadata={"id": "s1", "doc_id": "d", "chunk_index": 1}, score=0.2),
        RetrievedChunk(text="s2", metadata={"id": "s2", "doc_id": "d", "chunk_index": 2}, score=0.3),
    ]
    out = retrieval.add_neighbor_chunks(seeds)
    assert len(calls) == 1 and "$or" in calls[0]
    assert {c.metadata["id"] for c in out} == {"s1", "s2", "n0", "n3"}


def test_records_carry_chunker_version():
    rows = build_ja_chunk_records(doc_id="d", source_doc="d.pdf", text="本文です。", doc_type="policy", source_pages=[1])
    assert {r["chunker_version"] for r in rows} == {CHUNKER_VERSION}


def test_keyword_index_versions_are_counted(monkeypatch, tmp_path):
    corpus = tmp_path / "chunks.jsonl"
    rows = [
        {"id": "a", "text": "本文", "normalization_version": "ja-norm-v1", "chunker_version": CHUNKER_VERSION},
        {"id": "b", "text": "本文"},
    ]
    corpus.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(corpus))
    retrieval._INDEX_CACHE.update({"path": None, "mtime": None, "index": None})
    versions = retrieval.keyword_index_versions()
    assert versions["normalization_version"] == {"ja-norm-v1": 1, "": 1}


# --- v2_06 Qdrant ------------------------------------------------------------------------------------


def test_store_switches_to_qdrant(monkeypatch):
    sentinel = object()
    monkeypatch.setenv("VECTOR_BACKEND", "qdrant")
    monkeypatch.setattr(vector_backend, "get_collection", lambda name, create_if_missing=True: sentinel)
    assert store.get_vectorstore("anything", verify_embedding_fingerprint=False) is sentinel


def test_qdrant_collection_round_trip(monkeypatch, tmp_path):
    pytest.importorskip("qdrant_client")
    monkeypatch.setenv("QDRANT_PATH", str(tmp_path / "qdrant"))
    monkeypatch.setattr(config, "VECTORSTORE_DIR", str(tmp_path / "vs"))
    vector_backend._CLIENT.clear()
    col = vector_backend.QdrantCollection("unit_test")
    col.upsert(
        ids=["c1", "c2"],
        documents=["一", "二"],
        embeddings=[[1.0, 0.0], [0.0, 1.0]],
        metadatas=[{"tenant_id": "default", "doc_id": "d", "chunk_index": 1}, {"tenant_id": "t2", "doc_id": "d", "chunk_index": 2}],
    )
    res = col.query(query_embeddings=[[1.0, 0.0]], n_results=5, where={"tenant_id": "default"})
    assert res["ids"] == [["c1"]] and res["distances"][0][0] == pytest.approx(0.0, abs=1e-6)
    got = col.get(where={"$or": [{"$and": [{"doc_id": "d"}, {"chunk_index": 2}]}]})
    assert got["ids"] == ["c2"]
    col.delete(where={"$and": [{"tenant_id": "t2"}, {"doc_id": "d"}]})
    assert col.count() == 1
    col.modify(metadata={"embed_model": "m"})
    assert col.metadata["embed_model"] == "m"


# --- v2_09 freshness ------------------------------------------------------------------------------------


def test_freshness_report_flags_changes_and_stale_sources(tmp_path):
    current = tmp_path / "current.pdf"
    current.write_bytes(b"new content")
    manifest = {
        "records": [
            {"source_id": "a", "source_path": "current.pdf", "status": "active", "checksum": "0" * 64,
             "updated_at": "2026-10-01T00:00:00+00:00", "indexed_at": "2026-09-01T00:00:00+00:00"},
            {"source_id": "b", "source_path": "gone.pdf", "status": "active", "updated_at": "2020-01-01T00:00:00+00:00"},
            {"source_id": "c", "source_path": "old.pdf", "status": "archived"},
        ]
    }
    index = tmp_path / "index.jsonl"
    index.write_text('{"id":"x","source_doc":"old.pdf"}\n{"id":"y","source_doc":"unknown.pdf"}\n', encoding="utf-8")

    report = freshness_report(
        manifest, root_dir=tmp_path, index_path=index, max_age_days=365, now=datetime(2026, 10, 20, tzinfo=timezone.utc)
    )

    assert report["changed_not_reingested"] == ["a"]
    assert report["updated_after_index"] == ["a"]
    assert report["missing_files"] == ["b"]
    assert report["stale"] == ["b"]
    assert report["inactive_still_indexed"] == ["old.pdf"]
    assert report["indexed_not_in_manifest"] == ["unknown.pdf"]
    assert report["action_needed"] is True
