from __future__ import annotations

# Stage 3 regression tests: ingestion (items 3-1 .. 3-9) and NLP preprocessing
# on the ingestion side (N1 .. N4).

import json
import re
import unicodedata
from pathlib import Path

import pytest

import config
from rag_core import approved_similar, ja_text, japanese_normalizer, retrieval
from rag_core.chunking_ja import build_ja_chunk_records
from rag_core.document_converters import convert_file_to_canonical_chunks
from rag_core.document_converters.base import stable_chunk_id
from rag_core.document_converters.csv_converter import detect_delimiter, detect_encoding, read_csv_rows
from rag_core.document_converters.xlsx_converter import _classify_format_code, format_number
from rag_core.question_normalization import exact_match_key
from scripts import ingest_canonical_jsonl as ingest
from scripts import pdf_to_canonical_jsonl as pdf
from scripts import qanda_table_pdf_to_approved_qa as qanda
from scripts.build_canonical_index import build_canonical_index


def _children(rows):
    return [r for r in rows if r.get("chunk_role") == "child"]


# --- 3-1 headings ----------------------------------------------------------------


def test_consecutive_headings_are_all_kept():
    text = "第1条 目的\n第2条 定義\n第3条 適用範囲\n本規程は全社員に適用します。"
    rows = build_ja_chunk_records(doc_id="d", source_doc="d.pdf", text=text, doc_type="policy", source_pages=[1])
    blob = "\n".join(r["display_text"] for r in _children(rows))
    paths = [r["section_path"][0] for r in _children(rows)]
    assert "第1条 目的" in blob and "第2条 定義" in blob
    assert any(p.startswith("第3条") for p in paths)


@pytest.mark.parametrize("line", ["3.5%の増加が見込まれます", "2024.4.1から施行します", "03-1234-5678まで連絡"])
def test_decimals_dates_and_phone_numbers_are_not_headings(line):
    rows = build_ja_chunk_records(
        doc_id="d", source_doc="d.pdf", text=f"第1章 総則\n{line}", doc_type="policy", source_pages=[1]
    )
    assert {tuple(r["section_path"]) for r in rows} == {("第1章 総則",)}


# --- 3-2 PDF line joining ----------------------------------------------------------


def test_numbers_on_separate_lines_are_not_concatenated():
    assert pdf._normalize_text("100\n200") == "100\n200"


def test_japanese_lines_join_without_space_and_english_with_space():
    ja = pdf._normalize_text("これは長い文章の途中で改行された\n行の続きです。")
    en = pdf._normalize_text("This is a fairly long English line\ncontinued here.")
    assert ja == "これは長い文章の途中で改行された行の続きです。"
    assert en == "This is a fairly long English line continued here."


def test_short_and_table_lines_are_not_joined_and_paragraphs_survive():
    out = pdf._normalize_text("第1章 総則\n本章では基本方針を定める内容\n\n区分  条件  対応\nA  7日以内  返金")
    # heading (short line) and table rows stay on their own lines; the blank
    # line survives as a paragraph break; inner spaces are collapsed.
    assert out == "第1章 総則\n本章では基本方針を定める内容\n\n区分 条件 対応\nA 7日以内 返金"


def test_ocr_spaces_between_japanese_characters_are_removed():
    assert pdf._normalize_text("日 本 語 の 文", ocr=True) == "日本語の文"


# --- 3-3 heading carried across pages ------------------------------------------------


def test_section_path_continues_on_the_next_page():
    rows = build_ja_chunk_records(
        doc_id="d",
        source_doc="d.pdf",
        text="前ページから続く本文です。",
        doc_type="policy",
        source_pages=[2],
        inherited_section_path=["第2章 取り扱い"],
    )
    assert all(r["section_path"] == ["第2章 取り扱い"] for r in rows)


# --- 3-4 tenant-safe ids ----------------------------------------------------------------


def test_same_file_in_two_tenants_gets_distinct_ids():
    kwargs = dict(doc_id="same.pdf", source_doc="same.pdf", text="本文です。", doc_type="policy", source_pages=[1])
    default_ids = {r["id"] for r in build_ja_chunk_records(**kwargs)}
    tenant_ids = {r["id"] for r in build_ja_chunk_records(tenant_id="tenant_a", **kwargs)}
    assert default_ids.isdisjoint(tenant_ids)
    assert all(i.startswith("tenant_a:") for i in tenant_ids)
    assert not any(i.startswith("default:") for i in default_ids)
    assert stable_chunk_id("csv", "a.csv", "r2", "x") != stable_chunk_id("csv", "a.csv", "r2", "x", tenant_id="t")


def test_ids_do_not_depend_on_running_chunk_index():
    kwargs = dict(doc_id="d", source_doc="d.pdf", text="本文です。", doc_type="policy", source_pages=[3])
    first = [r["id"] for r in build_ja_chunk_records(base_chunk_index=0, **kwargs)]
    later = [r["id"] for r in build_ja_chunk_records(base_chunk_index=57, **kwargs)]
    assert first == later


# --- 3-5 document-level replacement ---------------------------------------------------------


class _FakeCollection:
    name = "fake"

    def __init__(self):
        self.rows = {}

    def upsert(self, ids, documents, embeddings, metadatas):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[i] = (d, m)

    def get(self, where=None, include=None, ids=None):
        clauses = (where or {}).get("$and", [])
        wanted = {k: v for c in clauses for k, v in c.items()}
        out = [i for i, (_d, m) in self.rows.items() if all(m.get(k) == v for k, v in wanted.items())]
        return {"ids": out}

    def delete(self, ids):
        for i in ids:
            self.rows.pop(i, None)


@pytest.fixture
def fake_store(monkeypatch):
    collection = _FakeCollection()
    monkeypatch.setattr(ingest.store, "get_vectorstore", lambda **kwargs: collection)
    monkeypatch.setattr(ingest.store, "stamp_collection_fingerprint", lambda c: {})
    monkeypatch.setattr(ingest.embedder, "is_local_provider", lambda: True)
    seen = {}

    def _embed(texts, client=None):
        seen.setdefault("texts", []).extend(texts)
        return [[0.0] for _ in texts]

    monkeypatch.setattr(ingest.embedder, "embed_documents", _embed)
    collection.seen = seen
    return collection


def _row(i, source="doc.pdf", tenant="default", **extra):
    return {"id": i, "text": f"本文{i}", "source_doc": source, "tenant_id": tenant, **extra}


def test_reingesting_a_document_removes_its_stale_chunks(fake_store):
    ingest.ingest_canonical_rows([_row("a1"), _row("a2"), _row("b1", source="other.pdf")])
    result = ingest.ingest_canonical_rows([_row("a1")])
    assert set(fake_store.rows) == {"a1", "b1"}
    assert result["stale_deleted"] == 1


def test_same_document_name_in_another_tenant_is_not_touched(fake_store):
    ingest.ingest_canonical_rows([_row("t1", tenant="tenant_a")])
    ingest.ingest_canonical_rows([_row("d1")])
    assert set(fake_store.rows) == {"t1", "d1"}


def test_vectors_use_searchable_text_and_parents_can_be_skipped(fake_store):
    result = ingest.ingest_canonical_rows(
        [_row("c1", searchable_text="タイトル 節 本文c1"), _row("p1", chunk_role="parent")],
        embed_parents=False,
    )
    assert fake_store.seen["texts"] == ["タイトル 節 本文c1"]
    assert result["parents_not_embedded"] == 1
    assert fake_store.rows["c1"][0] == "本文c1"  # stored document stays the display text
    assert fake_store.rows["c1"][1]["index_generation"] == result["index_generation"]


def test_keyword_rows_are_replaced_per_document_atomically(tmp_path):
    path = tmp_path / "kw.jsonl"
    ingest.write_keyword_rows(path, [_row("a1"), _row("a2"), _row("b1", source="other.pdf")])
    ingest.write_keyword_rows(path, [_row("a1")], generation_id="g2")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert sorted(r["id"] for r in rows) == ["a1", "b1"]
    assert [r for r in rows if r["id"] == "a1"][0]["index_generation"] == "g2"
    assert not list(tmp_path.glob(".kw.jsonl.tmp-*"))


def test_inactive_manifest_sources_are_excluded(tmp_path):
    src = tmp_path / "a.jsonl"
    src.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in [_row("x1"), _row("y1", source="old.pdf")]), encoding="utf-8")
    out = tmp_path / "out.jsonl"
    stats = build_canonical_index([src], out, inactive_sources={"old.pdf"})
    assert [json.loads(line)["id"] for line in out.read_text(encoding="utf-8").splitlines()] == ["x1"]
    assert stats["excluded_inactive_sources"] == 1
    assert "excluded_inactive_sources" not in build_canonical_index([src], out)


# --- 3-6 web ingestion writes both indexes ------------------------------------------------


def test_staging_collection_has_its_own_keyword_corpus(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "INDEX_DIR", str(tmp_path / "index"))
    assert retrieval.keyword_index_path_for_collection(None) == str(config.CHUNKS_JSONL_PATH)
    staging = retrieval.keyword_index_path_for_collection("tenant_a_stage_v1")
    assert staging == str(tmp_path / "index" / "collections" / "tenant_a_stage_v1.jsonl")


def test_web_job_writes_vector_and_keyword_rows_with_one_generation(monkeypatch, tmp_path):
    from webapi import ingestion_jobs

    monkeypatch.setattr(config, "INDEX_DIR", str(tmp_path / "index"))
    calls = {}

    def _fake_ingest(chunks, *, collection_name, generation_id):
        calls["generation"] = generation_id
        return {"ingested": len(chunks), "skipped": 0, "index_generation": generation_id}

    monkeypatch.setattr(ingestion_jobs, "ingest_canonical_rows", _fake_ingest)
    result = ingestion_jobs._ingest_chunks([_row("s1", tenant="tenant_a")], "tenant_a_stage_v1")
    path = Path(retrieval.keyword_index_path_for_collection("tenant_a_stage_v1"))
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["index_generation"] == calls["generation"]
    assert result["keyword_index_rows"] == 1


# --- 3-7 / 3-8 chunk text ----------------------------------------------------------------------


def test_children_do_not_carry_the_whole_section():
    long_body = "\n".join(f"段落{i}の本文は十分に長い説明文です。" * 20 for i in range(6))
    rows = build_ja_chunk_records(
        doc_id="d", source_doc="d.pdf", text=f"第1章 総則\n{long_body}", doc_type="policy", source_pages=[1]
    )
    children = _children(rows)
    assert len(children) >= 2
    assert "段落5" not in children[0]["searchable_text"]


def test_table_empty_cells_keep_columns_aligned():
    rows = build_ja_chunk_records(
        doc_id="t", source_doc="t.pdf", text="| 区分 | 条件 | 対応 |\n| A |  | 全額返金 |", doc_type="table", source_pages=[1]
    )
    child = _children(rows)[0]["display_text"]
    assert "対応=全額返金" in child and "条件=" not in child


def test_faq_question_wrapped_over_lines_and_pairs_without_blank_lines():
    text = "Q: 申請の期限は\nいつまでですか？\nA: 月末です。\nQ: 窓口はどこですか？\nA: 管理部です。"
    rows = build_ja_chunk_records(doc_id="f", source_doc="f.pdf", text=text, doc_type="faq", source_pages=[1])
    questions = [r["faq_question"] for r in rows if r["chunk_role"] == "parent"]
    # Chunk text is NFKC-normalized, so the full-width "？" becomes "?".
    assert questions == ["申請の期限は いつまでですか?", "窓口はどこですか?"]


# --- 3-9 Q&A table PDF ---------------------------------------------------------------------------


def _w(text, x0, y0, page=1, x1=None):
    return qanda.Word(x0=x0, y0=y0, x1=x1 or x0 + 20, y1=y0 + 10, text=text, page=page)


def test_row_number_is_removed_only_from_the_number_column():
    words = [_w("1", 62, 100), _w("質問ですか。", 157, 100), _w("1", 471, 100), _w("件です。", 495, 100)]
    rows = qanda.words_to_rows(words)
    assert rows[0].answer == "1 件です。"


def test_ruling_lines_define_rows_and_page_continuation_is_merged():
    words = [
        _w("1", 62, 100), _w("質問ですか。", 157, 100), _w("回答の前半", 471, 100),
        _w("回答の後半です。", 471, 40, page=2),
        _w("2", 62, 100, page=2), _w("次の質問ですか。", 157, 100, page=2), _w("次の回答です。", 471, 100, page=2),
    ]
    hlines = {1: [90.0, 130.0], 2: [30.0, 60.0, 90.0, 130.0]}
    rows = qanda.words_to_rows(words, hlines=hlines)
    assert [r.question_no for r in rows] == [1, 2]
    assert rows[0].answer == "回答の前半回答の後半です。"  # Japanese-Japanese join has no space


def test_converter_never_writes_approved_records(tmp_path):
    with pytest.raises(ValueError):
        qanda.convert_pdf(pdf_path=tmp_path / "x.pdf", output_path=tmp_path / "o.jsonl", source_doc="x.pdf", status="approved")


# --- N3 converters ----------------------------------------------------------------------------------


def test_csv_shift_jis_and_tab_delimiter(tmp_path):
    path = tmp_path / "sjis.csv"
    path.write_bytes("質問\t回答\n営業時間は？\t9時から17時です\n".encode("cp932"))
    rows, encoding, delimiter = read_csv_rows(path)
    assert encoding == "cp932" and delimiter == "\t"
    assert rows[1] == ["営業時間は？", "9時から17時です"]
    chunks = convert_file_to_canonical_chunks(path)
    assert chunks[0]["text"] == "Q: 営業時間は？\nA: 9時から17時です"
    assert chunks[0]["source_encoding"] == "cp932"


def test_csv_encoding_detection_variants():
    text = "品名,金額\nりんご,1万2千円\n"
    assert detect_encoding(text.encode("utf-8")) == "utf-8"
    assert detect_encoding(b"\xef\xbb\xbf" + text.encode("utf-8")) == "utf-8-sig"
    assert detect_encoding(text.encode("utf-16")) == "utf-16"
    assert detect_encoding(text.encode("euc_jp")) in {"euc_jp", "cp932"}
    assert detect_delimiter("a;b;c\n1;2;3\n") == ";"


def test_csv_rows_carry_quantities(tmp_path):
    path = tmp_path / "price.csv"
    path.write_text("品名,金額\nりんご,1万2千円\n", encoding="utf-8")
    chunk = convert_file_to_canonical_chunks(path)[0]
    assert any(q.get("kind") == "money" for q in chunk["quantities"])


@pytest.mark.parametrize(
    "raw,kind,expected",
    [
        ("45383", "date", "2024-04-01"),
        ("45383.5", "datetime", "2024-04-01 12:00:00"),
        ("0.5", "time", "12:00:00"),
        ("0.155", "percent", "15.5%"),
        ("1200", "currency:¥", "¥1200"),
        ("1200", "currency:円", "1200円"),
        ("12", "", "12"),
    ],
)
def test_xlsx_number_formats(raw, kind, expected):
    assert format_number(raw, kind) == expected


def test_xlsx_1904_dates_and_format_codes():
    assert format_number("0", "date", date1904=True) == "1904-01-01"
    assert _classify_format_code('yyyy"年"m"月"d"日"') == "date"
    assert _classify_format_code("0.0%") == "percent"
    assert _classify_format_code("[h]:mm") == "time"
    assert _classify_format_code("0.00E+00") == ""
    assert _classify_format_code("General") == ""
    assert _classify_format_code("[$¥-411]#,##0") == "currency:¥"


# --- N1 / N2 PDF preprocessing ------------------------------------------------------------------


def test_pdf_repeated_header_is_removed_and_pages_are_reported(tmp_path):
    pytest.importorskip("pdfplumber")
    from rag_core import extraction

    extraction.load()  # puts libs/docextract/src on sys.path when it is not installed
    from docextract.testing import simple_pdf

    path = tmp_path / "doc.pdf"
    pages = [
        {"texts": [(72, 48, 11, "CONFIDENTIAL"), (72, 88, 11, f"Operations manual page {n}. Backups are verified every morning.")]}
        for n in range(1, 4)
    ]
    pages.append({})  # empty page
    path.write_bytes(simple_pdf(pages))

    rows, report = pdf.convert_pdf_to_rows(path)

    assert rows and all("CONFIDENTIAL" not in r["text"] for r in rows)
    assert report["pages_total"] == 4
    assert report["pages_low_text_without_ocr"] == [4]
    assert all(r.get("doc_checksum") == report["doc_checksum"] for r in rows)


# --- N4 normalization ----------------------------------------------------------------------------


_SAMPLES = ("ｻｰﾊﾞｰ　ＡＢＣ－１２３", "データ–ベース〜の利用", "１，２００円（税込）", "Ｑ＆Ａ サーバー")


@pytest.mark.parametrize("profile", ["legacy", "index"])
def test_index_paths_share_one_normalization(monkeypatch, profile):
    monkeypatch.setenv("NORMALIZATION_PROFILE", profile)
    for sample in _SAMPLES:
        expected = ja_text.normalize_for_index(sample)
        assert retrieval._normalize(sample) == expected
        assert approved_similar._norm(sample) == expected
        assert japanese_normalizer.normalize_japanese_text(sample) == expected


def test_normalization_version_changes_with_profile(monkeypatch):
    monkeypatch.setenv("NORMALIZATION_PROFILE", "legacy")
    legacy = ja_text.active_normalization_version()
    monkeypatch.setenv("NORMALIZATION_PROFILE", "index")
    assert ja_text.active_normalization_version() != legacy


def test_long_vowel_mark_is_kept_for_matching():
    assert "サーバー" in japanese_normalizer.normalize_for_match("サーバーの設定")


def _old_exact_key(text):
    # Definition before N4, kept here to prove the moved function is identical.
    s = unicodedata.normalize("NFKC", str(text or "")).replace("　", " ").lower()
    s = re.sub(r"[\"'“”‘’「」『』（）()\[\]【】]", "", s)
    s = re.sub(r"[、。，．,.・:：;；!?！？]", "", s)
    s = re.sub(r"[‐-‒–—―ー－\-]", "", s)
    return re.sub(r"\s+", "", s).strip()


@pytest.mark.parametrize("text", ["「営業時間」は？", "パスワード・再設定！", "Ｑ＆Ａ ー テスト", "A-1 (b) [c]"])
def test_exact_match_key_is_unchanged(text):
    assert exact_match_key(text) == _old_exact_key(text)
