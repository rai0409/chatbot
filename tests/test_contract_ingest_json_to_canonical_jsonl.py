from __future__ import annotations

import json

from scripts.contract_ingest_json_to_canonical_jsonl import convert_contract_ingest


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _convert(src, out, **kwargs):
    return convert_contract_ingest(
        input_path=src,
        output_path=out,
        source_doc=kwargs.get("source_doc", "sample.pdf"),
        title=kwargs.get("title", "Sample"),
        doc_type=kwargs.get("doc_type", "contract"),
        tenant_id=kwargs.get("tenant_id", "default"),
        doc_version=kwargs.get("doc_version", "v1"),
        include_short=kwargs.get("include_short", False),
    )


def test_document_pages_blocks_input(tmp_path):
    src = tmp_path / "document.json"
    out = tmp_path / "canonical.jsonl"
    src.write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page": 1,
                        "blocks": [
                            {"type": "clause", "heading": "第1条", "text": "本契約の目的を定めます。"},
                            {"type": "clause", "heading": "第2条", "text": "委託料は月末締めです。"},
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = _convert(src, out, title="サンプル契約書")
    rows = _rows(out)

    assert summary["written_records"] == 2
    assert rows[0]["source_pages"] == [1]
    assert rows[0]["section_path"] == ["第1条"]
    assert rows[0]["original_record_type"] == "clause"
    assert rows[0]["searchable_text"] == rows[0]["display_text"] == rows[0]["text"]


def test_records_jsonl_input(tmp_path):
    src = tmp_path / "records.jsonl"
    out = tmp_path / "canonical.jsonl"
    records = [
        {"record_id": "a", "kind": "paragraph", "page_number": 2, "section": "支払", "content": "支払期限は翌月末です。"},
        {"record_id": "b", "kind": "clause", "page": 3, "heading": "解除", "text": "解除条件を定めます。"},
    ]
    src.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")

    _convert(src, out)
    rows = _rows(out)

    assert len(rows) == 2
    assert rows[0]["source_pages"] == [2]
    assert rows[0]["section_path"] == ["支払"]
    assert rows[0]["original_record_id"] == "a"


def test_directory_input(tmp_path):
    src_dir = tmp_path / "inputs"
    src_dir.mkdir()
    out = tmp_path / "canonical.jsonl"
    (src_dir / "a.json").write_text(
        json.dumps({"paragraphs": [{"page": 1, "text": "第一の本文です。"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (src_dir / "b.jsonl").write_text(
        json.dumps({"page": 2, "text": "第二の本文です。"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    summary = _convert(src_dir, out)
    rows = _rows(out)

    assert summary["detected_shapes"]["directory"] == 1
    assert len(rows) == 2
    assert [row["source_pages"] for row in rows] == [[1], [2]]


def test_source_pages_parsing_and_unknown_page(tmp_path):
    src = tmp_path / "records.jsonl"
    out = tmp_path / "canonical.jsonl"
    src.write_text(
        json.dumps({"source_pages": "[4,5]", "text": "複数ページの本文です。"}, ensure_ascii=False)
        + "\n"
        + json.dumps({"text": "ページ不明の本文です。"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    _convert(src, out)
    rows = _rows(out)

    assert rows[0]["source_pages"] == [4, 5]
    assert rows[1]["source_pages"] == [-1]


def test_table_flattening_and_required_fields(tmp_path):
    src = tmp_path / "document.json"
    out = tmp_path / "canonical.jsonl"
    src.write_text(
        json.dumps(
            {
                "tables": [
                    {
                        "type": "table",
                        "page": 7,
                        "caption": "料金表",
                        "rows": [{"項目": "委託料", "期限": "翌月末"}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    _convert(src, out)
    row = _rows(out)[0]

    assert "項目=委託料" in row["searchable_text"]
    assert row["section_path"] == ["料金表"]
    for key in [
        "id",
        "source_doc",
        "source_pages",
        "doc_type",
        "title",
        "chunk_role",
        "searchable_text",
        "display_text",
        "language",
        "tenant_id",
        "doc_version",
        "extraction_method",
    ]:
        assert row.get(key) not in (None, "", [])


def test_deterministic_id(tmp_path):
    src = tmp_path / "document.json"
    out1 = tmp_path / "one.jsonl"
    out2 = tmp_path / "two.jsonl"
    src.write_text(json.dumps({"text": "同じ本文から同じIDです。"}, ensure_ascii=False), encoding="utf-8")

    _convert(src, out1)
    _convert(src, out2)

    assert _rows(out1)[0]["id"] == _rows(out2)[0]["id"]


def test_empty_and_short_text_skipped_unless_include_short(tmp_path):
    src = tmp_path / "records.jsonl"
    out = tmp_path / "canonical.jsonl"
    src.write_text(
        json.dumps({"text": ""}, ensure_ascii=False)
        + "\n"
        + json.dumps({"text": "短い"}, ensure_ascii=False)
        + "\n"
        + json.dumps({"text": "十分な長さの本文です。"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    summary = _convert(src, out)
    rows = _rows(out)
    assert summary["written_records"] == 1
    assert rows[0]["display_text"] == "十分な長さの本文です。"

    out_short = tmp_path / "canonical_short.jsonl"
    summary_short = _convert(src, out_short, include_short=True)
    assert summary_short["written_records"] == 2
