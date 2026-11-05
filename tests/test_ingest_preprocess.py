from __future__ import annotations

from rag_core.ingest_preprocess import PreprocessOptions, preprocess_rows

_BODIES = [
    ("申請の流れを", "説明します。"),
    ("提出書類を", "確認します。"),
    ("審査の期間を", "示します。"),
    ("結果の通知を", "行います。"),
]


def _pdf_rows():
    return [
        {
            "id": f"c{page}",
            "doc_id": "manual.pdf",
            "source_pages": [page],
            "text": f"社内規程集\n{first}\n{second}\n- {page} -",
        }
        for page, (first, second) in enumerate(_BODIES, start=1)
    ]


def test_repeated_header_footer_removed_and_broken_lines_joined():
    rows, report = preprocess_rows(_pdf_rows(), PreprocessOptions(attach_quantities=False))

    assert [row["text"] for row in rows] == [first + second for first, second in _BODIES]
    assert report["repeated_lines_removed"] == 8
    assert report["lines_joined"] == 4
    assert report["rows_changed"] == 4


def test_rows_without_page_numbers_keep_repeated_lines():
    rows = [
        {"id": f"r{i}", "doc_id": "faq.csv", "source_pages": [-1], "text": "区分: 共通\nQ: 質問\nA: 回答"}
        for i in range(5)
    ]

    out, report = preprocess_rows(rows, PreprocessOptions(attach_quantities=False))

    assert [row["text"] for row in out] == [row["text"] for row in rows]
    assert report["repeated_lines_removed"] == 0


def test_quantities_attached_and_invalid_dates_counted():
    rows = [
        {"id": "a", "doc_id": "d", "source_pages": [1], "text": "手数料は1万5千円です。"},
        {"id": "b", "doc_id": "d", "source_pages": [2], "text": "平成31年5月1日に改定。"},
    ]

    out, report = preprocess_rows(rows)

    assert [q["canonical"] for q in out[0]["quantities"]] == ["15000JPY"]
    assert out[0]["quantities"][0]["value"] == "15000"
    assert report["quantities_by_kind"] == {"date": 1, "money": 1}
    assert report["invalid_quantities"] == 1


def test_input_rows_are_not_mutated():
    rows = _pdf_rows()
    original = [dict(row) for row in rows]

    preprocess_rows(rows)

    assert rows == original
