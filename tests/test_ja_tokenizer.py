from __future__ import annotations

import logging

import pytest

import config
from rag_core import ja_tokenizer, retrieval


class _FakeMorpheme:
    def __init__(self, surface, pos, normalized):
        self._surface = surface
        self._pos = (pos, "*", "*", "*", "*", "*")
        self._normalized = normalized

    def surface(self):
        return self._surface

    def part_of_speech(self):
        return self._pos

    def normalized_form(self):
        return self._normalized


class _FakeTokenizer:
    def __init__(self, morphemes):
        self.morphemes = morphemes
        self.inputs = []

    def tokenize(self, text):
        self.inputs.append(text)
        return list(self.morphemes)


@pytest.fixture(autouse=True)
def _reset_tokenizer_state():
    ja_tokenizer.reset_cache()
    yield
    ja_tokenizer.reset_cache()


def _use_fake_sudachi(monkeypatch, morphemes):
    fake = _FakeTokenizer(morphemes)
    monkeypatch.setattr(ja_tokenizer, "_create_sudachi_tokenizer", lambda dict_name, split_mode: fake)
    return fake


def _missing_sudachi(dict_name, split_mode):
    raise ImportError("No module named 'sudachipy'")


def test_heuristic_mode_never_loads_sudachi(monkeypatch):
    monkeypatch.setattr(config, "KEYWORD_TOKENIZER", "heuristic")
    monkeypatch.setattr(
        ja_tokenizer,
        "_create_sudachi_tokenizer",
        lambda *args: pytest.fail("heuristic mode must not load Sudachi"),
    )

    assert ja_tokenizer.active_tokenizer_name() == "heuristic"


def test_auto_mode_falls_back_with_single_warning(monkeypatch, caplog):
    monkeypatch.setattr(config, "KEYWORD_TOKENIZER", "auto")
    monkeypatch.setattr(ja_tokenizer, "_create_sudachi_tokenizer", _missing_sudachi)

    with caplog.at_level(logging.WARNING, logger="rag_core.ja_tokenizer"):
        first = ja_tokenizer.active_tokenizer_name()
        second = ja_tokenizer.active_tokenizer_name()

    assert first == second == "heuristic"
    warnings = [r for r in caplog.records if "keyword_tokenizer_fallback" in r.getMessage()]
    assert len(warnings) == 1


def test_explicit_sudachi_mode_fails_loudly(monkeypatch):
    monkeypatch.setattr(config, "KEYWORD_TOKENIZER", "sudachi")
    monkeypatch.setattr(ja_tokenizer, "_create_sudachi_tokenizer", _missing_sudachi)

    with pytest.raises(RuntimeError, match="KEYWORD_TOKENIZER=sudachi"):
        ja_tokenizer.active_tokenizer_name()


def test_unknown_mode_is_rejected(monkeypatch):
    monkeypatch.setattr(config, "KEYWORD_TOKENIZER", "mecab")

    with pytest.raises(ValueError, match="Unknown KEYWORD_TOKENIZER"):
        ja_tokenizer.active_tokenizer_name()


def test_sudachi_tokens_keep_content_lemmas_and_drop_function_words(monkeypatch):
    monkeypatch.setattr(config, "KEYWORD_TOKENIZER", "sudachi")
    monkeypatch.setattr(config, "KEYWORD_NGRAM_ENABLED", True)
    _use_fake_sudachi(
        monkeypatch,
        [
            _FakeMorpheme("会社", "名詞", "会社"),
            _FakeMorpheme("を", "助詞", "を"),
            _FakeMorpheme("辞め", "動詞", "辞める"),
            _FakeMorpheme("たら", "助動詞", "た"),
        ],
    )

    tokens = ja_tokenizer.sudachi_tokens("会社を辞めたら")

    assert "会社" in tokens
    assert "辞める" in tokens
    assert "を" not in tokens
    assert "た" not in tokens
    assert "2g:会社" in tokens


def test_code_tokens_add_separator_free_variants():
    assert ja_tokenizer.code_tokens("zz-10 の確認") == ["zz-10", "zz10"]


def test_bigram_tokens_skip_hiragana_only_runs():
    assert ja_tokenizer.bigram_tokens("ください") == []
    assert ja_tokenizer.bigram_tokens("健康保険") == ["2g:健康", "2g:康保", "2g:保険"]


def test_long_text_is_split_below_sudachi_limit():
    text = "あ" * 9000

    pieces = ja_tokenizer._split_for_sudachi(text)

    assert "".join(pieces) == text
    assert all(len(piece) <= ja_tokenizer._MAX_SUDACHI_CHARS for piece in pieces)


def test_keyword_index_rebuilds_when_tokenizer_changes(monkeypatch, tmp_path):
    corpus = tmp_path / "chunks.jsonl"
    corpus.write_text('{"id": "c1", "text": "会社を辞める手続き"}\n', encoding="utf-8")
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(corpus))
    retrieval._INDEX_CACHE.update({"path": None, "mtime": None, "tokenizer": None, "index": None})
    monkeypatch.setattr(config, "KEYWORD_TOKENIZER", "heuristic")

    assert retrieval._load_keyword_index().tokenizer == "heuristic"

    monkeypatch.setattr(config, "KEYWORD_TOKENIZER", "sudachi")
    _use_fake_sudachi(monkeypatch, [_FakeMorpheme("辞める", "動詞", "辞める")])

    index = retrieval._load_keyword_index()

    assert index.tokenizer == "sudachi"
    assert "辞める" in index.vocabulary


def test_real_sudachi_lemmatizes_inflected_verb(monkeypatch):
    # Runs only where requirements-ja.txt is installed.
    pytest.importorskip("sudachipy")
    monkeypatch.setattr(config, "KEYWORD_TOKENIZER", "sudachi")
    try:
        ja_tokenizer.active_tokenizer_name()
    except RuntimeError:
        pytest.skip("Sudachi dictionary is not installed")

    assert "辞める" in ja_tokenizer.sudachi_tokens("会社を辞めたら")
