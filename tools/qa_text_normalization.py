#!/usr/bin/env python3
import re
import unicodedata
from typing import Any, List


JP = r"ぁ-んァ-ヴー一-龥々〆〤"


def to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def normalize_unicode(text: Any) -> str:
    """
    全角英数字・記号ゆれをある程度そろえる。
    例:
    ２１０ → 210
    ＰＩＮ → PIN
    ％ → %
    """
    s = to_text(text)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = unicodedata.normalize("NFKC", s)
    return s


def normalize_display_text(text: Any) -> str:
    """
    画面表示・Excel確認用。
    日本語中のPDF折り返し由来スペースを消す。
    英語の自然な語間スペースは基本的に残す。
    """
    s = normalize_unicode(text)
    s = s.replace("\u3000", " ")
    s = re.sub(r"\s*\n\s*", " ", s)
    s = re.sub(r"[ \t]+", " ", s)

    # 日本語どうしの間の不要スペースを削除
    # ボタ ン -> ボタン
    # 新カード に -> 新カードに
    # 教えて下さ い -> 教えて下さい
    s = re.sub(rf"([{JP}])\s+([{JP}])", r"\1\2", s)

    # 日本語と句読点・括弧周りの不要スペースを削除
    s = re.sub(rf"([{JP}])\s+([。、，．！？!?）」』])", r"\1\2", s)
    s = re.sub(rf"([「『（(])\s+([{JP}])", r"\1\2", s)

    # 連続スペースは1つに
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def normalize_search_text(text: Any) -> str:
    """
    ベクトル検索・BM25検索用。
    表示用より少し強めに正規化するが、英語の語間は残す。
    """
    s = normalize_display_text(text)
    s = s.lower()

    # 検索では記号揺れを少し吸収
    s = s.replace("／", "/")
    s = s.replace("－", "-")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def normalize_eval_text(text: Any) -> str:
    """
    QA評価用。
    空白差を完全に無視する。
    IC カード / ICカード、Internet Explorer / InternetExplorer も同一視。
    """
    s = normalize_search_text(text)
    s = re.sub(r"\s+", "", s)
    return s.strip()


def contains_eval(haystack: Any, needle: Any) -> bool:
    h = normalize_eval_text(haystack)
    n = normalize_eval_text(needle)
    return bool(n) and n in h


def count_expected_hits(answer: Any, expected_terms: List[str]) -> int:
    ans = normalize_eval_text(answer)
    hits = 0
    for term in expected_terms or []:
        t = normalize_eval_text(term)
        if t and t in ans:
            hits += 1
    return hits


def char_overlap_score(answer: Any, expected: Any) -> float:
    """
    完全一致ではなく、回答がどれくらい期待回答を含んでいるかを見る簡易スコア。
    商用品質では LLM Judge ではなく、まずこのような deterministic 評価を持つ。
    """
    a = normalize_eval_text(answer)
    e = normalize_eval_text(expected)

    if not a or not e:
        return 0.0
    if e in a:
        return 1.0
    if a in e:
        return min(0.95, len(a) / max(1, len(e)))

    e_chars = set(e)
    a_chars = set(a)
    if not e_chars:
        return 0.0
    return len(e_chars & a_chars) / len(e_chars)


def assert_no_japanese_internal_spacing(text: Any) -> bool:
    s = normalize_display_text(text)
    return re.search(rf"[{JP}]\s+[{JP}]", s) is None
