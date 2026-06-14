#!/usr/bin/env python3
"""Build the synthetic manufacturing pilot validation corpus + eval cases (Prompt039).

CLEARLY FICTIONAL content only (架空精機 / 装置X). No real customer data, no
secrets, no network, no .env, no Docker. Writes canonical chunk JSONL + eval
cases JSONL under eval/cases/manufacturing_pilot/. Eval runs operate on the
provided --chunks-jsonl; this never ingests into the production/default
vectorstore.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[1] / "eval" / "cases" / "manufacturing_pilot"
COMPANY = "架空精機"  # fictional company

# --- synthetic documents (canonical chunks) --------------------------------

DOCS = [
    # (id, source_doc, type, text)
    ("proc_start", "souki_procedure.pdf", "pdf",
     "架空精機 作業手順書。装置Xの起動手順は次のとおりです。①主電源スイッチをONにする。②制御盤の緑ランプ点灯を確認する。③スタートボタンを長押しする。"),
    ("proc_setup", "souki_procedure.pdf", "pdf",
     "装置Xの段取り替え手順。①非常停止ボタンを押す。②金型を交換する。③原点復帰を実行してから運転を再開する。"),
    ("qual_kizu", "souki_quality.pdf", "pdf",
     "外観検査の合否基準。キズの長さが0.5ミリメートル以下なら合格、0.5ミリメートルを超える場合は不合格とする。"),
    ("safe_ppe", "souki_safety.pdf", "pdf",
     "架空精機 安全規程。装置Xの稼働エリアでは保護メガネと安全靴の着用を義務付ける。手袋は回転部では禁止する。"),
    ("safe_lockout", "souki_safety.pdf", "pdf",
     "ロックアウト手順。保守作業の前に電源を遮断し、施錠タグを取り付けてから作業を開始すること。"),
    ("trouble_e12", "souki_troubleshooting.pdf", "pdf",
     "アラームE12が発生した場合、原因は潤滑油不足である。対処として潤滑油を補充し、リセットボタンを押す。"),
    ("it_vpn", "souki_it_faq.csv", "csv",
     "VPN接続の申請手順。情報システム部のポータルから申請フォームを提出し、承認後に接続情報が発行される。"),
]


def _chunk(idx, cid, source_doc, dtype, text):
    return {
        "id": cid, "text": text, "source_doc": source_doc, "source_pages": [idx],
        "doc_id": source_doc, "chunk_index": idx, "searchable": 1, "type": dtype,
        "quality": "high", "searchable_text": text, "display_text": text,
        "language": "ja", "tenant_id": "default", "chunk_role": "child", "parent_chunk_id": "",
    }


def _qa_pair(cid, qid, source_doc, page, title, question, answer):
    text = f"Q: {question}\nA: {answer}"
    return {
        "id": cid, "text": text, "source_doc": source_doc, "source_pages": [page],
        "doc_id": source_doc, "chunk_index": page, "searchable": 1, "type": "approved_qa",
        "quality": "approved", "doc_type": "approved_qa_pair", "chunk_type": "qa_pair",
        "title": title, "section_path": [title], "chunk_role": "child", "parent_chunk_id": "",
        "searchable_text": text, "display_text": text, "language": "ja", "tenant_id": "default",
        "extraction_method": "build_manufacturing_pilot_pack", "qa_id": qid, "approved_qa_id": qid,
        "question_text": question, "answer_text": answer, "normalized_question": question.replace("？", "?"),
        "approved_answer": answer,
        "approved_citations": [{"source_doc": source_doc, "source_pages": [page], "title": title}],
    }


def build_chunks():
    chunks = [_chunk(i + 1, cid, sd, dt, tx) for i, (cid, sd, dt, tx) in enumerate(DOCS)]
    chunks.append(_qa_pair(
        "approved_qa_pair:qa_inspect_pass", "qa_inspect_pass", "souki_qa_table.pdf", 1,
        "外観検査Q&A", "外観検査の合否はどのように判定しますか", "キズの長さが0.5ミリメートル以下であれば合格と判定します。"))
    return chunks


# --- eval cases -------------------------------------------------------------
# Default eval = keyword/BM25 retrieval (vectors stubbed). Distinctive terms
# localize to one chunk. expected_used_fallback/expected_guard_reason capture
# the abstain-first behavior.

CASES = [
    # answerable + citation (grounded)
    {"case_id": "mfg_proc_start", "category": "procedure / 起動手順", "query": "装置Xの起動手順を教えて",
     "expected_top_chunk_id": "proc_start", "expected_used_fallback": False,
     "notes": "Answerable grounded procedure lookup; top-1 = proc_start (souki_procedure.pdf)."},
    {"case_id": "mfg_proc_setup", "category": "procedure / 段取り替え", "query": "装置Xの段取り替え手順",
     "expected_top_chunk_id": "proc_setup", "expected_used_fallback": False,
     "notes": "Answerable changeover procedure; top-1 = proc_setup."},
    {"case_id": "mfg_quality_kizu", "category": "quality / 外観検査", "query": "キズが0.5ミリメートルを超える場合は不合格ですか",
     "expected_top_chunk_id": "qual_kizu", "expected_used_fallback": False,
     "notes": "Quality fail-threshold lookup; '超える/不合格' localize to qual_kizu over the approved pair."},
    {"case_id": "mfg_safety_ppe", "category": "safety / 保護具", "query": "保護メガネと安全靴の着用は義務ですか",
     "expected_top_chunk_id": "safe_ppe", "expected_used_fallback": False,
     "notes": "Safety PPE rule; exact terms '保護メガネと安全靴' give localized evidence and bypass the guard."},
    {"case_id": "mfg_safety_lockout", "category": "safety / ロックアウト", "query": "保守作業前のロックアウト手順",
     "expected_top_chunk_id": "safe_lockout", "expected_used_fallback": False,
     "notes": "Lockout procedure; top-1 = safe_lockout."},
    {"case_id": "mfg_trouble_e12", "category": "troubleshooting / アラーム", "query": "アラームE12の原因と対処",
     "expected_top_chunk_id": "trouble_e12", "expected_used_fallback": False,
     "notes": "Troubleshooting alarm E12; top-1 = trouble_e12."},
    {"case_id": "mfg_it_vpn", "category": "helpdesk / IT申請", "query": "VPN接続の申請手順",
     "expected_top_chunk_id": "it_vpn", "expected_used_fallback": False,
     "notes": "IT helpdesk FAQ; top-1 = it_vpn."},
    # approved-QA exact-match
    {"case_id": "mfg_approved_inspect", "category": "approved-QA exact match",
     "query": "外観検査の合否はどのように判定しますか",
     "expected_top_chunk_id": "approved_qa_pair:qa_inspect_pass", "expected_used_fallback": False,
     "gold_chunk_ids": ["approved_qa_pair:qa_inspect_pass"],
     "notes": "Stored approved-Q&A question wording retrieves the qa_pair chunk (deterministic answer)."},
    # abstain (too_general) — must abstain
    {"case_id": "mfg_abstain_ambiguous", "category": "abstain / too_general", "query": "これは？",
     "expected_guard_reason": "too_general", "expected_used_fallback": True,
     "notes": "Deliberately vague query MUST abstain (too_general)."},
    {"case_id": "mfg_abstain_broad", "category": "abstain / too_general", "query": "運用は？",
     "expected_guard_reason": "too_general", "expected_used_fallback": True,
     "notes": "Broad short query MUST abstain (too_general)."},
    # out-of-corpus — must not answer (fallback)
    {"case_id": "mfg_out_of_corpus", "category": "out-of-corpus / no-answer", "query": "経理の月次締め日は？",
     "expected_used_fallback": True,
     "notes": "Topic absent from the manufacturing corpus MUST fall back (no fabricated answer)."},
]


def write_pack():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    chunks_path = OUT_DIR / "manufacturing_chunks.jsonl"
    cases_path = OUT_DIR / "manufacturing_cases.jsonl"
    chunks_path.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in build_chunks()) + "\n", encoding="utf-8")
    cases_path.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in CASES) + "\n", encoding="utf-8")
    return chunks_path, cases_path


if __name__ == "__main__":
    cp, kp = write_pack()
    print(f"wrote {cp} ({len(build_chunks())} chunks) and {kp} ({len(CASES)} cases)")
