"""The parameters of ingest_settings reach the code that uses them. Synthetic data only."""
from __future__ import annotations

import pytest

from rag_core import chunking_ja, ingest_preprocess

LONG = "。".join(f"これは{n}番目の説明文でありこの規程における手続きの内容を示すものである" for n in range(60)) + "。"


def test_chunk_sizes_follow_the_environment(monkeypatch):
    monkeypatch.delenv("JA_CHUNK_TARGET_POLICY_MIN_CHARS", raising=False)
    monkeypatch.delenv("JA_CHUNK_TARGET_POLICY_MAX_CHARS", raising=False)
    default = chunking_ja.build_ja_chunk_records(doc_id="d", source_doc="s.pdf", text=LONG, doc_type="policy", title="t", source_pages=[1])
    monkeypatch.setenv("JA_CHUNK_TARGET_POLICY_MIN_CHARS", "100")
    monkeypatch.setenv("JA_CHUNK_TARGET_POLICY_MAX_CHARS", "200")
    small = chunking_ja.build_ja_chunk_records(doc_id="d", source_doc="s.pdf", text=LONG, doc_type="policy", title="t", source_pages=[1])
    children = lambda rows: [r for r in rows if r.get("chunk_role") == "child"]  # noqa: E731
    assert len(children(small)) > len(children(default))
    assert max(len(r["text"]) for r in children(small)) <= 260


@pytest.mark.parametrize("value", ["abc", "-5", "0", ""])
def test_an_unusable_chunk_size_falls_back_to_the_default(monkeypatch, value):
    monkeypatch.setenv("JA_CHUNK_TARGET_FAQ_MAX_CHARS", value)
    assert chunking_ja._targets(chunking_ja.DOC_TYPE_FAQ) == chunking_ja._DEFAULT_TARGETS[chunking_ja.DOC_TYPE_FAQ]


def test_a_minimum_above_the_maximum_falls_back_to_the_defaults(monkeypatch):
    monkeypatch.setenv("JA_CHUNK_TARGET_FAQ_MIN_CHARS", "900")
    monkeypatch.setenv("JA_CHUNK_TARGET_FAQ_MAX_CHARS", "300")
    assert chunking_ja._targets(chunking_ja.DOC_TYPE_FAQ) == chunking_ja._DEFAULT_TARGETS[chunking_ja.DOC_TYPE_FAQ]


def test_preprocess_options_follow_the_environment(monkeypatch):
    for name in ("INGEST_PREPROCESS_REPEATED_LINES", "INGEST_PREPROCESS_REPEATED_MIN_PAGES", "INGEST_PREPROCESS_REPEATED_MIN_RATIO",
                 "INGEST_PREPROCESS_REPEATED_MAX_CHARS", "INGEST_PREPROCESS_JOIN_LINES", "INGEST_PREPROCESS_QUANTITIES"):
        monkeypatch.delenv(name, raising=False)
    default = ingest_preprocess.preprocess_options()
    assert default == ingest_preprocess.PreprocessOptions()
    monkeypatch.setenv("INGEST_PREPROCESS_REPEATED_LINES", "false")
    monkeypatch.setenv("INGEST_PREPROCESS_REPEATED_MIN_PAGES", "5")
    monkeypatch.setenv("INGEST_PREPROCESS_REPEATED_MIN_RATIO", "0.8")
    monkeypatch.setenv("INGEST_PREPROCESS_REPEATED_MAX_CHARS", "60")
    monkeypatch.setenv("INGEST_PREPROCESS_JOIN_LINES", "false")
    monkeypatch.setenv("INGEST_PREPROCESS_QUANTITIES", "false")
    changed = ingest_preprocess.preprocess_options()
    assert (changed.remove_repeated_lines, changed.repeated_line_min_pages, changed.repeated_line_min_ratio) == (False, 5, 0.8)
    assert (changed.repeated_line_max_chars, changed.join_broken_lines, changed.attach_quantities) == (60, False, False)


def test_office_files_never_join_lines_whatever_the_setting(monkeypatch):
    monkeypatch.setenv("INGEST_PREPROCESS_JOIN_LINES", "true")
    assert ingest_preprocess.preprocess_options(office=True).join_broken_lines is False


def test_bad_preprocess_values_fall_back(monkeypatch):
    monkeypatch.setenv("INGEST_PREPROCESS_REPEATED_MIN_PAGES", "x")
    monkeypatch.setenv("INGEST_PREPROCESS_REPEATED_MIN_RATIO", "9")
    options = ingest_preprocess.preprocess_options()
    assert options.repeated_line_min_pages == 3 and options.repeated_line_min_ratio == 0.6
