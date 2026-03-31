from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    source_doc: str
    source_pages: Tuple[int, ...]
    score: Optional[float] = None


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def extract_two_digit_codes(text: str) -> List[str]:
    norm = _nfkc(text)
    return re.findall(r"(?<!\d)(\d{2})(?!\d)", norm)


_REWRITE_RULES = [
    (r"\breset\b", "再設定 再発行"),
    (r"リセット", "再設定 再発行"),
    (r"インボイス制度", "適格請求書制度"),
    (r"パスワード", "暗証番号"),
]


def rewrite_query(text: str) -> str:
    out = text
    for pattern, repl in _REWRITE_RULES:
        if re.search(pattern, out, flags=re.IGNORECASE):
            out = re.sub(
                pattern,
                lambda m: f"{m.group(0)} {repl}",
                out,
                flags=re.IGNORECASE,
            )
    return out


def merge_by_page(chunks: Sequence[Chunk]) -> List[Chunk]:
    merged: Dict[Tuple[str, Tuple[int, ...]], Chunk] = {}
    for ch in chunks:
        key = (ch.source_doc, ch.source_pages)
        if key not in merged:
            merged[key] = ch
            continue
        prev = merged[key]
        text = prev.text + "\n" + ch.text
        score = min([s for s in [prev.score, ch.score] if s is not None], default=None)
        merged[key] = Chunk(
            id=prev.id,
            text=text,
            source_doc=prev.source_doc,
            source_pages=prev.source_pages,
            score=score,
        )
    return list(merged.values())


def build_evidence_blocks(chunks: Sequence[Chunk], max_chars: int = 1200) -> List[str]:
    blocks = []
    for idx, ch in enumerate(chunks, start=1):
        text = ch.text.strip().replace("\n", " ")
        if len(text) > max_chars:
            text = text[: max_chars - 1] + "…"
        blocks.append(f"[S{idx}] {text}")
    return blocks


def build_prompt(question: str, evidence_blocks: Sequence[str]) -> str:
    codes = extract_two_digit_codes(question)
    code_rule = ""
    if codes:
        code_rule = (
            "質問に2桁コードが含まれる場合、各箇条書きはそのコードで開始し「コード: 内容 [S#]」形式。"
        )
    allow_rule = ""
    if "許容値" in question:
        allow_rule = "許容値が問われる場合は許容範囲を示す行を必ず含める。"
    rules = [
        "推測しない。",
        "必ず箇条書きで答える。",
        "各行の末尾に必ず[ S# ]ではなく[S#]を付ける。",
        "本文に文書名やページ番号を書かない。",
        "表(パイプ)は使わない。",
        code_rule,
        allow_rule,
    ]
    rules = [r for r in rules if r]
    evidence = "\n".join(evidence_blocks)
    return (
        "あなたは根拠に基づき回答するアシスタントです。\n"
        "ルール:\n- "
        + "\n- ".join(rules)
        + "\n\n"
        + "根拠:\n"
        + evidence
        + "\n\n"
        + "質問:\n"
        + question
        + "\n\n"
        + "回答:\n"
    )


def validate_output(
    answer: str,
    chunks: Sequence[Chunk],
    intent: str,
    intent_terms: Optional[Iterable[str]] = None,
) -> bool:
    lines = [ln.strip() for ln in answer.splitlines() if ln.strip()]
    bullet_lines = [ln for ln in lines if ln.startswith("-")]
    if not bullet_lines:
        return False
    for ln in bullet_lines:
        if not re.search(r"\[S\d+\]\s*$", ln):
            return False
    if intent in {"change", "reset"} and intent_terms:
        for ln in bullet_lines:
            m = re.search(r"\[S(\d+)\]\s*$", ln)
            if not m:
                continue
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(chunks):
                text = chunks[idx].text
                if any(term in text for term in intent_terms):
                    return True
        return False
    return True


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"[。！？\n]+", text)
    return [p.strip() for p in parts if p.strip()]


def extractive_fallback(question: str, chunks: Sequence[Chunk]) -> str:
    tokens = set(re.findall(r"[A-Za-z0-9ぁ-んァ-ン一-龥]+", _nfkc(question)))
    scored = []
    for idx, ch in enumerate(chunks, start=1):
        for sent in _split_sentences(ch.text):
            overlap = sum(1 for t in tokens if t in sent)
            bonus = 1 if any(x in sent for x in ["手順", "方法", "操作", "流れ"]) else 0
            scored.append((overlap + bonus, idx, sent))
    scored.sort(key=lambda x: (-x[0], x[1]))
    top = scored[:6] if scored else []
    if not top:
        return "- 関連する記載が見つかりませんでした [S1]"
    lines = [f"- {sent} [S{idx}]" for _, idx, sent in top]
    return "\n".join(lines)


def strip_reference_block(text: str) -> str:
    text = re.sub(r"参考資料\s*:.*", "", text, flags=re.DOTALL)
    return text.replace("回答:\n回答:\n", "回答:\n")


def strip_source_tags(answer: str) -> str:
    text = re.sub(r"\[S\d+\]", "", answer)
    lines: List[str] = []
    for ln in text.splitlines():
        ln = re.sub(r"[ \t]+", " ", ln).strip()
        if ln:
            lines.append(ln)
    return "\n".join(lines).strip()


def to_footnotes(answer: str, chunks: Sequence[Chunk]) -> str:
    order = collect_source_order(answer)

    def _replace(match):
        sid = match.group(1)
        num = order.index(sid) + 1
        return f"[{num}]"

    body = re.sub(r"\[S(\d+)\]", _replace, answer)
    ref_lines = ["参考資料:"]
    for sid in order:
        idx = int(sid) - 1
        if 0 <= idx < len(chunks):
            ch = chunks[idx]
            pages = ",".join(str(p) for p in ch.source_pages) if ch.source_pages else ""
            page_txt = f" p{pages}" if pages else ""
            ref_lines.append(f"- [{order.index(sid)+1}] {ch.source_doc}{page_txt}")
        else:
            ref_lines.append(f"- [{order.index(sid)+1}] なし")
    return body.strip() + "\n" + "\n".join(ref_lines)


def collect_source_order(answer: str) -> List[str]:
    order: List[str] = []
    for match in re.finditer(r"\[S(\d+)\]", answer):
        sid = match.group(1)
        if sid not in order:
            order.append(sid)
    return order


def build_citation_payloads(answer: str, chunks: Sequence[Chunk]) -> List[Dict]:
    payloads: List[Dict] = []
    for num, sid in enumerate(collect_source_order(answer), start=1):
        idx = int(sid) - 1
        if 0 <= idx < len(chunks):
            ch = chunks[idx]
            payloads.append(
                {
                    "number": num,
                    "source_doc": ch.source_doc,
                    "source_pages": list(ch.source_pages),
                    "chunk_id": ch.id,
                }
            )
        else:
            payloads.append(
                {
                    "number": num,
                    "source_doc": "なし",
                    "source_pages": [],
                    "chunk_id": None,
                }
            )
    return payloads
