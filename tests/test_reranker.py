from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

import pytest

from rag_core.reranker import rerank_chunks
from rag_core.retrieval import RetrievedChunk


@dataclass(frozen=True)
class EvalCase:
    category: str
    query: str
    chunks: Tuple[Tuple[str, str], ...]
    expected_top: str
    why: str


def _mk_chunks(specs: Sequence[Tuple[str, str]]) -> List[RetrievedChunk]:
    out: List[RetrievedChunk] = []
    for idx, (label, text) in enumerate(specs, start=1):
        out.append(
            RetrievedChunk(
                text=text,
                metadata={"id": label, "retrieval_source": "hybrid"},
                score=min(0.25 + (idx - 1) * 0.02, 0.95),
            )
        )
    return out


PR4_EVAL_CASES: Tuple[EvalCase, ...] = (
    EvalCase(
        category="quoted",
        query='「請求書ID」 の確認方法',
        chunks=(
            ("A", "請求書番号の一般説明です。"),
            ("B", "請求書ID は帳票ヘッダーで確認できます。"),
            ("C", "ID一覧の説明です。"),
        ),
        expected_top="B",
        why="Quoted exact term should outrank nearby partial matches.",
    ),
    EvalCase(
        category="id_code",
        query="PR2 の対処方法",
        chunks=(
            ("A", "PR1 の対処手順です。"),
            ("B", "PR2 エラーの対処手順です。"),
            ("C", "一般的なトラブル対応です。"),
        ),
        expected_top="B",
        why="Identifier/code hits should be prioritized.",
    ),
    EvalCase(
        category="alnum_technical",
        query="AnswerResult の必須項目",
        chunks=(
            ("A", "回答フォーマットの概要です。"),
            ("B", "AnswerResult には answer_text と citations を含めます。"),
            ("C", "レスポンス例です。"),
        ),
        expected_top="B",
        why="Technical alnum term should improve lexical precision.",
    ),
    EvalCase(
        category="katakana",
        query="カタカナ語 の意味",
        chunks=(
            ("A", "用語の一般説明です。"),
            ("B", "カタカナ語 は外来語の表記です。"),
            ("C", "カタカナ表記の説明です。"),
        ),
        expected_top="B",
        why="Exact katakana term should outrank broader katakana mentions.",
    ),
    EvalCase(
        category="kanji_compound",
        query="漢字複合語 の定義",
        chunks=(
            ("A", "漢字の基本説明です。"),
            ("B", "漢字複合語 は複数の漢字からなる語です。"),
            ("C", "複合語の一般論です。"),
        ),
        expected_top="B",
        why="Kanji compound exact match should move up conservatively.",
    ),
    EvalCase(
        category="quoted_id",
        query='「ABC123」 の仕様',
        chunks=(
            ("A", "仕様の概要です。"),
            ("B", "ABC123 は伝票コードです。"),
            ("C", "ABC の一般仕様です。"),
        ),
        expected_top="B",
        why="Quoted code should receive strongest lexical bump.",
    ),
    EvalCase(
        category="procedure_broad",
        query="設定を変更する方法を教えて",
        chunks=(
            ("A", "管理画面の全体説明です。"),
            ("B", "変更前に確認する項目を示します。"),
            ("C", "設定値の一覧です。"),
        ),
        expected_top="A",
        why="Broad procedural intent should preserve base order under weak evidence.",
    ),
    EvalCase(
        category="change_reset_style",
        query="初期化 手順",
        chunks=(
            ("A", "初期化を実施する前提条件です。"),
            ("B", "初期化手順の詳細です。"),
            ("C", "初期化後の確認方法です。"),
        ),
        expected_top="A",
        why="Single weak lexical signal should not trigger reckless reordering.",
    ),
    EvalCase(
        category="broad_semantic",
        query="これは何ですか",
        chunks=(
            ("A", "機能概要です。"),
            ("B", "詳細な補足です。"),
            ("C", "関連する注意事項です。"),
        ),
        expected_top="A",
        why="No precise lexical anchors should keep original ranking.",
    ),
    EvalCase(
        category="ambiguous_code_combo",
        query="S12345 または S54321 の差分",
        chunks=(
            ("A", "S12345 の説明です。"),
            ("B", "S12345 と S54321 の差分説明です。"),
            ("C", "一般的なコード仕様です。"),
        ),
        expected_top="B",
        why="Chunk matching multiple IDs should be promoted.",
    ),
    EvalCase(
        category="mixed_script_ambiguous",
        query="承認フラグ の条件",
        chunks=(
            ("A", "承認の説明です。"),
            ("B", "承認フラグ の判定条件です。"),
            ("C", "フラグ管理の一般説明です。"),
        ),
        expected_top="A",
        why="When salient extraction is weak for mixed-script terms, keep base ranking.",
    ),
    EvalCase(
        category="ambiguous_weak",
        query="運用について",
        chunks=(
            ("A", "運用方針の概要です。"),
            ("B", "運用ルールの詳細です。"),
            ("C", "補足です。"),
        ),
        expected_top="A",
        why="Ambiguous question should keep base ordering.",
    ),
)


@pytest.mark.parametrize("case", PR4_EVAL_CASES, ids=[f"{c.category}" for c in PR4_EVAL_CASES])
def test_pr4_eval_cases_expected_top(case: EvalCase):
    before = _mk_chunks(case.chunks)
    after = rerank_chunks(case.query, before, intent="other")
    assert after[0].metadata["id"] == case.expected_top, case.why


def test_rerank_chunks_preserves_order_without_precise_lexical_signals():
    chunks = _mk_chunks(
        (
            ("A", "概要です。"),
            ("B", "補足です。"),
            ("C", "詳細です。"),
        )
    )
    after = rerank_chunks("これはどうですか", chunks, intent="other")
    assert [c.metadata["id"] for c in after] == ["A", "B", "C"]


def test_rerank_chunks_preserves_metadata_and_score_fields():
    chunks = _mk_chunks(
        (
            ("A", "一般説明です。"),
            ("B", "PR2 の説明です。"),
        )
    )
    after = rerank_chunks("PR2", chunks)
    assert all("retrieval_source" in c.metadata for c in after)
    assert all(isinstance(c.score, float) for c in after)


def test_rerank_prevents_pr2_vs_pr20_false_positive():
    chunks = _mk_chunks(
        (
            ("A", "PR20 エラーの説明です。"),
            ("B", "PR2 エラーの説明です。"),
            ("C", "一般的な障害対応です。"),
        )
    )
    after = rerank_chunks("PR2 の対処方法", chunks, intent="other")
    assert [c.metadata["id"] for c in after[:2]] == ["B", "A"]


def test_rerank_keeps_exact_identifier_hit_in_noisy_long_chunk():
    noisy = (
        "更新履歴や補足説明が続きます。関連しない設定値の列挙があります。"
        " それでも対象コード ABC123 の手順がこの段落に含まれます。"
        " 末尾にも追加の注意事項があります。"
    )
    chunks = _mk_chunks(
        (
            ("A", "ABC124 の一般説明です。"),
            ("B", noisy),
            ("C", "その他の参考情報です。"),
        )
    )
    after = rerank_chunks("ABC123 の仕様", chunks, intent="other")
    assert after[0].metadata["id"] == "B"


def test_rerank_prevents_alnum_identifier_superset_token_false_positive():
    chunks = _mk_chunks(
        (
            ("A", "XABC123Y の一般説明です。"),
            ("B", "ABC123 の仕様です。"),
            ("C", "その他の参考情報です。"),
        )
    )
    after = rerank_chunks("ABC123 の仕様", chunks, intent="other")
    assert [c.metadata["id"] for c in after[:2]] == ["B", "A"]


def test_rerank_quoted_code_like_uses_strict_matching():
    chunks = _mk_chunks(
        (
            ("A", "PR20 の手順です。"),
            ("B", "PR2 の手順です。"),
            ("C", "一般説明です。"),
        )
    )
    after = rerank_chunks('"PR2" の手順', chunks, intent="other")
    assert [c.metadata["id"] for c in after[:2]] == ["B", "A"]


def test_rerank_quoted_non_code_like_keeps_conservative_text_matching():
    chunks = _mk_chunks(
        (
            ("A", "請求書関連の一般説明です。"),
            ("B", "この画面で請求書IDの確認ができます。"),
            ("C", "ID入力の注意事項です。"),
        )
    )
    after = rerank_chunks('「請求書ID」 の確認', chunks, intent="other")
    assert after[0].metadata["id"] == "B"


def test_rerank_metadata_title_and_section_hits_can_lift_candidate():
    chunks = [
        RetrievedChunk(
            text="一般説明です。",
            metadata={
                "id": "A",
                "retrieval_source": "hybrid",
                "title": "請求管理ガイド",
                "section_path": ["概要"],
            },
            score=0.25,
        ),
        RetrievedChunk(
            text="本文では略称のみ記載します。",
            metadata={
                "id": "B",
                "retrieval_source": "hybrid",
                "title": "請求書ID 運用手順",
                "section_path": ["請求書ID の確認方法"],
            },
            score=0.27,
        ),
    ]
    after = rerank_chunks("請求書ID の確認方法", chunks, intent="other")
    assert after[0].metadata["id"] == "B"


def test_rerank_metadata_faq_question_and_alias_hits():
    chunks = [
        RetrievedChunk(
            text="一般的な問い合わせ先の説明です。",
            metadata={
                "id": "A",
                "retrieval_source": "hybrid",
                "doc_type": "faq_glossary",
                "faq_question": "問い合わせ先はどこですか",
                "aliases": ["連絡窓口"],
            },
            score=0.25,
        ),
        RetrievedChunk(
            text="一般論です。",
            metadata={
                "id": "B",
                "retrieval_source": "hybrid",
                "doc_type": "policy_spec",
                "faq_question": "",
                "aliases": ["汎用説明"],
            },
            score=0.27,
        ),
    ]
    after = rerank_chunks("連絡窓口 とは", chunks, intent="other")
    assert after[0].metadata["id"] == "A"
