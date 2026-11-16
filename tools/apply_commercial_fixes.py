#!/usr/bin/env python3
"""商用化に向けた修正（F1〜F11）を、チャットボットのリポジトリに適用する。

目的:
  main（e5eb511, 2026-11-05）の分析で確認した欠陥のうち、コードで直せるものを
  1 ファイルで適用する。別の PC に持って行き、リポジトリの場所を指定して実行できる。
  各修正は「古い文字列 → 新しい文字列」の置換で、同じファイルに 2 回かけても
  二重適用にならない（適用済みなら skip と表示する）。
  main 以外の 10 ブランチは main の部分集合であり、取り込むものは無い。

使い方:
  python3 tools/apply_commercial_fixes.py --repo <リポジトリ> --dry-run   # 何を変えるかだけ表示
  python3 tools/apply_commercial_fixes.py --repo <リポジトリ>             # 適用（.bak をリポジトリ内に作る）
  python3 tools/apply_commercial_fixes.py --repo <リポジトリ> --check     # 適用後の動作確認（venv が要る）
  python3 tools/apply_commercial_fixes.py --repo <リポジトリ> --pytest    # 関連テストを実行
  python3 tools/apply_commercial_fixes.py --repo <リポジトリ> --revert    # .bak から戻す
  --repo を省略すると、このファイルの 1 つ上の階層をリポジトリとみなす。

入力:  リポジトリ内の下記ファイル。出力: 標準出力。変更はリポジトリ内のみ。

適用する修正:
  F1  production_guard: 本番起動の必須項目を 5 つ追加
        GROUNDING_VERIFY_ENABLED / 距離ガードが稼働中の埋め込みモデルで校正済み /
        RATE_LIMIT_ENABLED / 複数ワーカー時 RATE_LIMIT_BACKEND=redis / 管理者の個人識別（OIDC）
  F2  qa._finalize_generated_answer: LLM 生成の箇条書きにも年・金額・件数の型照合（不一致は抽出型へ）
  F3  grounding.check_claim: 主張文に否定があり根拠側に無いときは落とす
  F4  grounding.verify_answer: GROUNDING_LLM_REJECT=true で LLM 判定による棄却も行う
  F5  config: LLM タイムアウト既定 30 秒 → 10 秒
  F6  constraints: oauthlib 4.0.0 → 4.0.1（CVE-2026-49272）
  F7  retrieval.expand_parent_chunks: 引用ページを子で上書きしない（親の文を見せるなら親のページ）。
        子のページは primary_child_source_pages に保持
  F8  qa.guard_merged_top: 質問が年度・年を指定し、根拠にその年が無ければ year_mismatch で拒否
        （GUARD_YEAR_MISMATCH_ENABLED=false で無効化可）
  F9  rag_grounded._rules: 生成プロンプトに「年度・金額・件数は同じ値の根拠だけを使う」規則を追加
  F10 文単位の引用: CitationOut.supporting_sentences（引用チャンク内で、箇条書きと最も語が重なる文、最大 2）
  F11 /metrics: 段階別レイテンシ（retrieval / generation / total）の累積バケットと、
        ガード無しで引用 0 件の回数（chat_no_citation_total）。精度劣化の検知用
  F12 qa: クエリ前処理（口語除去・同義語・誤字候補）を既定 on（これまで既定 off で、本番で使われていなかった）
  F13 approved_qa + chat: 口語の言い回し（「営業時間おしえてー」「退職の手続き教えてほしいんだけど」）でも、
        承認済み回答を**そのまま**返す。両側から前置き・語尾を落として強い正規化キーで比較。否定語・内容語は落とさない。
        answer_mode=approved_colloquial_match で監査・指標に出る。APPROVED_QA_COLLOQUIAL_MATCH=false で無効化
  F14 configs/query_rules/colloquial_ja.json: 挨拶・前振り 3 種、語尾・話題マーカー 7 種を追加（JSON に追記、冪等）
  F15 configs/japanese_business_synonyms.json: 一般的な社内用語の同義語 14 群を追加（テナント固有語は別途）
  F16 qa._evidence_coverage_bypass: 2 文字の語 1 つだけの短い口語の問い（「有給ってどう？」）でも、
        その語が上位 1 件の見出し・FAQ 質問にあれば too_general で拒否しない
  F17 answer_cache: 口語の揺れ（教えて／教えてほしいんだけど）で同じキャッシュ項目を使う

含めていないもの（別作業）:
  - /chat/stream で検索完了時に citations を先行送出する（UI と同時に行う）
  - 実データ評価、ライセンス変更、GitHub の古いブランチ削除

注意:
  - 本番ガードに必須項目を足すので、既存の本番 .env はそのままでは起動しない（不足項目は起動時に列挙される）。
  - production_problems() の内容を固定しているテスト、引用 dict の鍵を固定しているテストは調整が要る（--pytest で判明）。
  - このスクリプトは個人情報・秘密情報を含まない。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class Fix:
    key: str
    path: str
    old: str
    new: str
    note: str


# ---------------------------------------------------------------------------
# F1 webapi/production_guard.py
# ---------------------------------------------------------------------------
_PG_OLD = """    from rag_core import llm

    llm_problem = llm.production_problem()
"""
_PG_NEW = """    # --- commercial fixes (tools/apply_commercial_fixes.py, F1) -------------
    # A generated answer may only reach the user after every bullet was checked
    # against the evidence it cites; the distance guard is meaningless with
    # thresholds calibrated for another embedding model.
    if not _enabled("GROUNDING_VERIFY_ENABLED"):
        problems.append("GROUNDING_VERIFY_ENABLED=true (every generated bullet is checked against its cited evidence)")
    try:
        from rag_core import qa as _qa

        _cal = _qa.distance_guard_calibration()
        if not _cal.get("calibrated"):
            problems.append(
                "RAG_DIST_CALIBRATED_MODEL must name the active embedding model "
                f"(active: {_cal.get('active_model') or 'unknown'}, calibrated for: {_cal.get('calibrated_for') or 'none'}); "
                "run scripts/calibrate_distance_guard.py and set RAG_SOFT_DIST_* / RAG_HARD_MAX_DIST from its output"
            )
    except Exception as exc:  # noqa: BLE001 - a guard that cannot be checked is a problem, not a pass
        problems.append(f"distance guard calibration could not be checked: {exc}")
    if not _enabled("RATE_LIMIT_ENABLED"):
        problems.append("RATE_LIMIT_ENABLED=true (a request budget per key)")
    if workers > 1 and (_env("RATE_LIMIT_BACKEND") or "local").lower() != "redis":
        problems.append("RATE_LIMIT_BACKEND=redis when WEB_CONCURRENCY > 1 (the local limiter counts per process)")
    if not _enabled("ENTERPRISE_OIDC_ENABLED") and not _enabled("ADMIN_SHARED_TOKEN_ACKNOWLEDGED"):
        problems.append(
            "ENTERPRISE_OIDC_ENABLED=true so administrators are identified individually "
            "(a pilot may set ADMIN_SHARED_TOKEN_ACKNOWLEDGED=true to accept the shared admin token; the audit log then "
            "cannot tell administrators apart)"
        )
    # --- end commercial fixes ---------------------------------------------
    from rag_core import llm

    llm_problem = llm.production_problem()
"""

# ---------------------------------------------------------------------------
# F2 rag_core/qa.py
# ---------------------------------------------------------------------------
_QA_OLD_DEF = """def _finalize_generated_answer(
    raw: str, q: str, grounded: Sequence[Chunk], intent: str, *, tenant_id: str = "default", client=None
) -> Tuple[str, bool]:
    raw = strip_reference_block(raw)
    used_fallback = False
    if not validate_output(raw, grounded, intent, config.PREFER_SORT_TERMS_CHANGE + config.PREFER_SORT_TERMS_RESET):
        raw = extractive_fallback(q, grounded)
        used_fallback = True
        if not raw.strip():
            # No evidence sentence shares a term with the question.
            return _guard_fallback_raw("insufficient_evidence"), True
"""
_QA_NEW_DEF = """_GENERATED_BULLET_RE = re.compile(r"^-\\s*(.*?)\\s*((?:\\[S\\d+\\]\\s*)+)$")
_GENERATED_TAG_RE = re.compile(r"\\[S(\\d+)\\]")


def _generated_answer_type_supported(question: str, raw: str, grounded: Sequence[Chunk]) -> bool:
    \"\"\"Year / money / count agreement for LLM-generated bullets (commercial fix F2).

    The extractive path already refuses a sentence whose year, amount or count does
    not match the question (_has_answer_type_support); a generated bullet passed
    validate_output with a single shared term. Apply the same rule here.
    \"\"\"
    selected: List[Tuple[int, str]] = []
    for line in (raw or "").splitlines():
        match = _GENERATED_BULLET_RE.match(line.strip())
        if not match:
            continue
        tags = [int(n) for n in _GENERATED_TAG_RE.findall(match.group(2))]
        if not tags:
            continue
        selected.append((tags[0], match.group(1)))
    if not selected:
        return True
    return _has_answer_type_support(question, selected, grounded)


def _finalize_generated_answer(
    raw: str, q: str, grounded: Sequence[Chunk], intent: str, *, tenant_id: str = "default", client=None
) -> Tuple[str, bool]:
    raw = strip_reference_block(raw)
    used_fallback = False
    if not validate_output(raw, grounded, intent, config.PREFER_SORT_TERMS_CHANGE + config.PREFER_SORT_TERMS_RESET):
        raw = extractive_fallback(q, grounded)
        used_fallback = True
        if not raw.strip():
            # No evidence sentence shares a term with the question.
            return _guard_fallback_raw("insufficient_evidence"), True
    if not used_fallback and not _generated_answer_type_supported(q, raw, grounded):
        # Commercial fix F2: the generated bullets name a year, amount or count the
        # cited evidence does not support for this question.
        raw = extractive_fallback(q, grounded)
        used_fallback = True
        if not raw.strip():
            return _guard_fallback_raw("insufficient_evidence"), True
"""

# ---------------------------------------------------------------------------
# F3 / F4 rag_core/grounding.py
# ---------------------------------------------------------------------------
_GR_OLD_RE = """_KANJI = re.compile(r"[一-龥々〆]{3,}")
"""
_GR_NEW_RE = """_KANJI = re.compile(r"[一-龥々〆]{3,}")
# Commercial fix F3: a negated claim whose evidence carries no negation is not supported.
_NEGATION = re.compile(r"(できません|できない|ありません|対象外|不可|禁止|無効|不要|ではない|ではありません|認められない|認めない)")
"""
_GR_OLD_CHECK = """    if any(term not in evidence_n for term in _KANJI.findall(claim_n)):
        broken.append("new_term")
    terms = _terms(claim)
"""
_GR_NEW_CHECK = """    if any(term not in evidence_n for term in _KANJI.findall(claim_n)):
        broken.append("new_term")
    if _NEGATION.search(claim_n) and not _NEGATION.search(evidence_n):
        broken.append("negation")
    terms = _terms(claim)
"""
_GR_OLD_VERIFY = """        if not broken and coverage < threshold:
            if _llm_supports(claim, evidence, tenant_id=tenant_id, client=client):
                result.rescued += 1
                _bump("rescued")
            else:
                reasons.append("coverage")
"""
_GR_NEW_VERIFY = """        if not broken and coverage < threshold:
            if _llm_supports(claim, evidence, tenant_id=tenant_id, client=client):
                result.rescued += 1
                _bump("rescued")
            else:
                reasons.append("coverage")
        elif not broken and getattr(config, "GROUNDING_LLM_REJECT", False) and llm.check("verify", tenant_id).allowed:
            # Commercial fix F4: term coverage is necessary, not sufficient. When the
            # verify feature is allowed for this tenant, the LLM judge may also reject.
            if not _llm_supports(claim, evidence, tenant_id=tenant_id, client=client):
                reasons.append("llm_unsupported")
"""

# ---------------------------------------------------------------------------
# F5 / F4 config.py
# ---------------------------------------------------------------------------
_CFG_OLD_TIMEOUT = """CHAT_COMPLETION_TIMEOUT_SECONDS = _get_float("CHAT_COMPLETION_TIMEOUT_SECONDS", default=30.0)
"""
_CFG_NEW_TIMEOUT = """# Commercial fix F5: 10 s keeps the LLM path inside the p95 target (8 s + retrieval); a slower call degrades to extractive.
CHAT_COMPLETION_TIMEOUT_SECONDS = _get_float("CHAT_COMPLETION_TIMEOUT_SECONDS", default=10.0)
"""
_CFG_OLD_GROUND = """GROUNDING_MIN_COVERAGE = _get_float("GROUNDING_MIN_COVERAGE", default=0.5)
"""
_CFG_NEW_GROUND = """GROUNDING_MIN_COVERAGE = _get_float("GROUNDING_MIN_COVERAGE", default=0.5)
# Commercial fix F4: let the LLM judge reject a bullet that passed term coverage (needs LLM_FEATURES to include verify).
GROUNDING_LLM_REJECT = _get_bool("GROUNDING_LLM_REJECT", default=False)
"""

# ---------------------------------------------------------------------------
# F7 rag_core/retrieval.py  (parent expansion keeps the parent's pages)
# ---------------------------------------------------------------------------
_RT_OLD_DEDUP = """                prev_meta["primary_child_chunk_id"] = child_id
                prev_meta["source_pages"] = meta.get("source_pages") or prev_meta.get("source_pages") or []
"""
_RT_NEW_DEDUP = """                prev_meta["primary_child_chunk_id"] = child_id
                # Commercial fix F7: the cited pages stay the parent's own pages (the
                # text shown is the parent's); the best child's pages are kept apart.
                prev_meta["primary_child_source_pages"] = meta.get("source_pages") or []
"""
_RT_OLD_NEW = """        merged_meta["source_pages"] = meta.get("source_pages") or merged_meta.get("source_pages") or []
        merged_meta["parent_source_pages"] = parent_meta.get("source_pages") or []
"""
_RT_NEW_NEW = """        # Commercial fix F7: a parent that spans pages is cited with its own pages,
        # not the retrieving child's single page; the child's pages stay available.
        merged_meta["source_pages"] = parent_meta.get("source_pages") or meta.get("source_pages") or []
        merged_meta["parent_source_pages"] = parent_meta.get("source_pages") or []
        merged_meta["primary_child_source_pages"] = meta.get("source_pages") or []
"""

# ---------------------------------------------------------------------------
# F8 rag_core/qa.py  (year mismatch guard)
# ---------------------------------------------------------------------------
_QA_OLD_MSG = """    "salient_mismatch": "質問にある名称や番号を含む記載が見つかりませんでした。",
"""
_QA_NEW_MSG = """    "salient_mismatch": "質問にある名称や番号を含む記載が見つかりませんでした。",
    "year_mismatch": "質問にある年度・年の記載が文書内に見つかりませんでした。",
"""
_QA_OLD_GUARD = """    salient = _salient_terms(question)
    if salient:
        # Both sides use the index normalization (case, width, dashes).
"""
_QA_NEW_GUARD = """    # Commercial fix F8: a question that names a year (2025年度, 令和7年) is not
    # answered by evidence that only carries other years, however well the
    # other terms overlap. GUARD_YEAR_MISMATCH_ENABLED=false switches it off.
    if _setting("GUARD_YEAR_MISMATCH_ENABLED", "true").lower() in {"1", "true", "yes", "on"}:
        requested_years = _extract_requested_years(question)
        if requested_years:
            evidence_compact = re.sub(r"\\s+", "", normalize_japanese_text(" ".join(ch.text for ch in chunks)))
            if not any(year in evidence_compact for year in requested_years):
                return "year_mismatch"
    salient = _salient_terms(question)
    if salient:
        # Both sides use the index normalization (case, width, dashes).
"""

# ---------------------------------------------------------------------------
# F9 rag_grounded.py  (prompt rule)
# ---------------------------------------------------------------------------
_RG_OLD_RULE = """        "推測しない。<根拠> と </根拠> の間に書かれた内容だけで答える。",
"""
_RG_NEW_RULE = """        "推測しない。<根拠> と </根拠> の間に書かれた内容だけで答える。",
        "質問が年度・年・金額・件数を指定しているときは、同じ年度・年・金額・件数が書かれた根拠だけを使う。無ければ「不足」に書く。",
"""

# ---------------------------------------------------------------------------
# F10 sentence-level citations  (schemas.py + rag_grounded.py)
# ---------------------------------------------------------------------------
_SC_OLD = """    # Ids of every retrieved chunk merged into the cited evidence block.
    member_chunk_ids: List[str] = field(default_factory=list)
"""
_SC_NEW = """    # Ids of every retrieved chunk merged into the cited evidence block.
    member_chunk_ids: List[str] = field(default_factory=list)
    # Commercial fix F10: the evidence sentences that support the bullets citing this
    # block (up to 2), so a reader can verify the answer without opening the page.
    supporting_sentences: List[str] = field(default_factory=list)
"""
_RG_OLD_CIT_DEF = """def build_citation_payloads(answer: str, chunks: Sequence[Chunk]) -> List[Dict]:
"""
_RG_NEW_CIT_DEF = """def _supporting_sentences(answer: str, chunk: Chunk, sid: str, limit: int = 2) -> List[str]:
    \"\"\"Commercial fix F10: the sentences of the cited chunk that share the most terms with the bullets citing [S<sid>].\"\"\"
    claims: List[str] = []
    for ln in answer.splitlines():
        ln = ln.strip()
        if not ln.startswith("-") or sid not in _SOURCE_TAG_RE.findall(ln):
            continue
        claims.append(_SOURCE_TAG_RE.sub("", ln).lstrip("- ").strip())
    if not claims:
        return []
    claim_terms = {_compact(t) for c in claims for t in _terms(c)}
    scored = []
    for order, sent in enumerate(_split_sentences(chunk.text)):
        compact = _compact(sent)
        hits = sum(1 for t in claim_terms if t and t in compact)
        if hits:
            scored.append((-hits, order, sent))
    scored.sort()
    return [sent for _, _, sent in scored[:limit]]


def build_citation_payloads(answer: str, chunks: Sequence[Chunk]) -> List[Dict]:
"""
_RG_OLD_CIT_ITEM = """                "member_chunk_ids": list(ch.member_ids or (ch.id,)),
            }
"""
_RG_NEW_CIT_ITEM = """                "member_chunk_ids": list(ch.member_ids or (ch.id,)),
                "supporting_sentences": _supporting_sentences(answer, ch, sid),
            }
"""

# ---------------------------------------------------------------------------
# F11 webapi/routers/chat.py  (stage latency + no-citation metrics)
# ---------------------------------------------------------------------------
_CH_OLD_FN = """def _record_provider_error_metric(generation_error) -> None:
"""
_CH_NEW_FN = """_STAGE_LATENCY_BUCKETS_MS = (250, 500, 1000, 2000, 4000, 8000)


def _record_stage_latency_metrics(trace, *, citations_count=None, guard_reason=None) -> None:
    \"\"\"Commercial fix F11: per-stage latency as cumulative buckets and the no-citation count.

    chat_stage_latency_bucket{label="<stage>:le<ms>"} / "<stage>:le+Inf" and
    chat_stage_latency_count{label="<stage>"} give p50/p95 per stage on /metrics;
    chat_no_citation_total counts answers that were not guarded yet cite nothing
    (an accuracy-regression signal).
    \"\"\"
    if not isinstance(trace, dict):
        return
    stages = dict(trace.get("stage_latency_ms") or {})
    if trace.get("latency_ms") is not None:
        stages["total_ms"] = trace.get("latency_ms")
    for stage, value in stages.items():
        try:
            ms = float(value)
        except (TypeError, ValueError):
            continue
        name = str(stage)
        if name.endswith("_ms"):
            name = name[:-3]
        metrics_registry.increment("chat_stage_latency_count", name)
        for le in _STAGE_LATENCY_BUCKETS_MS:
            if ms <= le:
                metrics_registry.increment("chat_stage_latency_bucket", f"{name}:le{le}")
        metrics_registry.increment("chat_stage_latency_bucket", f"{name}:le+Inf")
    if citations_count is not None and not guard_reason and int(citations_count) == 0:
        metrics_registry.increment("chat_no_citation_total")


def _record_provider_error_metric(generation_error) -> None:
"""
_CH_OLD_CALL1 = """        append_audit_event("chat", chat_event)
"""
_CH_NEW_CALL1 = """        append_audit_event("chat", chat_event)
        _record_stage_latency_metrics(trace, citations_count=len(ans.citations or []), guard_reason=ans.guard_reason)
"""
_CH_OLD_CALL2 = """                append_audit_event("chat", stream_event)
"""
_CH_NEW_CALL2 = """                append_audit_event("chat", stream_event)
                _record_stage_latency_metrics(
                    trace,
                    citations_count=len(final_payload.get("citations") or []),
                    guard_reason=final_payload.get("guard_reason"),
                )
"""

# ---------------------------------------------------------------------------
# F12 rag_core/qa.py  (query preprocessing on by default)
# ---------------------------------------------------------------------------
_QA_OLD_PRE = """    if _setting("QUERY_PREPROCESS_ENABLED", "false") in _ENABLED_VALUES:
"""
_QA_NEW_PRE = """    # Commercial fix F12: colloquial stripping, synonyms and typo candidates are part
    # of the product path (QUERY_PREPROCESS_ENABLED=false restores the raw query).
    if _setting("QUERY_PREPROCESS_ENABLED", "true") in _ENABLED_VALUES:
"""

# ---------------------------------------------------------------------------
# F13 rag_core/approved_qa.py + webapi/routers/chat.py  (colloquial → approved answer, unchanged)
# ---------------------------------------------------------------------------
_AQ_OLD_LOOKUP = """def lookup_approved_answer(
    index: ApprovedQAIndex,
    question: str,
    tenant_id: str = "default",
) -> ApprovedAnswer | None:
    normalized = normalize_question_for_exact_match(question)
    canonical = index.by_tenant_question.get((tenant_id, normalized))
    if canonical is not None:
        return canonical
    alias_match = index.by_tenant_alias.get((tenant_id, normalized))
    if alias_match is None:
        return None
    answer, matched_alias = alias_match
    return replace(answer, match_type="alias", matched_alias=matched_alias)
"""
_AQ_NEW_LOOKUP = """_STRONG_KEY_CACHE: Dict[int, Tuple[Any, Dict[Tuple[str, str], Tuple[ApprovedAnswer, str, str]]]] = {}


def _strong_key_map(index: ApprovedQAIndex) -> Dict[Tuple[str, str], Tuple[ApprovedAnswer, str, str]]:
    \"\"\"(tenant, strong exact key) -> (answer, "canonical" | "alias", matched text); built once per index.\"\"\"
    cached = _STRONG_KEY_CACHE.get(id(index))
    if cached is not None and cached[0] is index:
        return cached[1]
    from rag_core.ja_text import normalize_for_index
    from rag_core.query_preprocess import strip_colloquial
    from rag_core.question_normalization import exact_match_key

    def key_of(tenant: str, text: str) -> Tuple[str, str]:
        # The stored question loses its own fillers too ("営業時間を教えてください" ->
        # "営業時間"), so both sides are compared in the same reduced form.
        stripped, _applied = strip_colloquial(normalize_for_index(text), tenant_id=tenant)
        return (tenant, exact_match_key(stripped or text))

    out: Dict[Tuple[str, str], Tuple[ApprovedAnswer, str, str]] = {}
    for (tenant, norm), answer in index.by_tenant_question.items():
        out.setdefault(key_of(tenant, norm), (answer, "canonical", norm))
    for (tenant, norm), (answer, alias) in index.by_tenant_alias.items():
        out.setdefault(key_of(tenant, norm), (answer, "alias", alias))
    if len(_STRONG_KEY_CACHE) >= 8:
        _STRONG_KEY_CACHE.clear()
    _STRONG_KEY_CACHE[id(index)] = (index, out)
    return out


def _lookup_colloquial(index: ApprovedQAIndex, question: str, tenant_id: str) -> ApprovedAnswer | None:
    \"\"\"Commercial fix F13: a colloquial phrasing of an approved question returns the same approved answer.

    "営業時間って何時？" / "退職の手続き教えてほしいんだけど" lose their fillers
    (configs/query_rules/colloquial_ja.json) and are compared on the strong exact key
    (punctuation, spaces, case removed). Negations and content words are never
    stripped, so "不要ですか" cannot match "必要ですか". The answer text is untouched.
    APPROVED_QA_COLLOQUIAL_MATCH=false switches the fallback off.
    \"\"\"
    if str(os.getenv("APPROVED_QA_COLLOQUIAL_MATCH", "true")).strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    from rag_core.ja_text import normalize_for_index
    from rag_core.query_preprocess import strip_colloquial
    from rag_core.question_normalization import exact_match_key

    stripped, _applied = strip_colloquial(normalize_for_index(question or ""), tenant_id=tenant_id)
    if not stripped:
        return None
    hit = _strong_key_map(index).get((tenant_id, exact_match_key(stripped)))
    if hit is None:
        return None
    answer, kind, matched = hit
    if kind == "alias":
        return replace(answer, match_type="alias", matched_alias=matched)
    return replace(answer, match_type="colloquial", matched_alias=None)


def lookup_approved_answer(
    index: ApprovedQAIndex,
    question: str,
    tenant_id: str = "default",
) -> ApprovedAnswer | None:
    normalized = normalize_question_for_exact_match(question)
    canonical = index.by_tenant_question.get((tenant_id, normalized))
    if canonical is not None:
        return canonical
    alias_match = index.by_tenant_alias.get((tenant_id, normalized))
    if alias_match is not None:
        answer, matched_alias = alias_match
        return replace(answer, match_type="alias", matched_alias=matched_alias)
    return _lookup_colloquial(index, question, tenant_id)
"""
_CH_OLD_MODE = """def _approved_answer_mode(answer: ApprovedAnswer) -> str:
    return "approved_alias_match" if answer.match_type == "alias" else "approved_exact_match"
"""
_CH_NEW_MODE = """def _approved_answer_mode(answer: ApprovedAnswer) -> str:
    if answer.match_type == "alias":
        return "approved_alias_match"
    if answer.match_type == "colloquial":
        # Commercial fix F13: visible in audit and metrics, so the colloquial fallback can be reviewed.
        return "approved_colloquial_match"
    return "approved_exact_match"
"""

# ---------------------------------------------------------------------------
# F16 rag_core/qa.py  (short colloquial lookups whose term is a heading)
# ---------------------------------------------------------------------------
_QA_OLD_BYPASS = """    content_len = sum(len(run) for run in _TOO_GENERAL_WORD_RUN_RE.findall(question_norm))
    if content_len >= _TOO_GENERAL_MIN_FULL_QUESTION_CONTENT_LEN:
        return True
    return len(terms) == 1 and len(terms[0]) >= _TOO_GENERAL_MIN_SINGLE_TERM_LEN
"""
_QA_NEW_BYPASS = """    content_len = sum(len(run) for run in _TOO_GENERAL_WORD_RUN_RE.findall(question_norm))
    if content_len >= _TOO_GENERAL_MIN_FULL_QUESTION_CONTENT_LEN:
        return True
    if len(terms) == 1 and len(terms[0]) >= _TOO_GENERAL_MIN_SINGLE_TERM_LEN:
        return True
    # Commercial fix F16: a short colloquial lookup ("有給ってどう？") names one 2-char
    # term. It is a legitimate lookup when that term is in the top-1 chunk's heading
    # fields (title / section path / FAQ question), not merely somewhere in its body.
    if len(terms) == 1 and len(terms[0]) >= 2:
        section_path = top_meta.get("section_path") or []
        if not isinstance(section_path, (list, tuple)):
            section_path = [str(section_path)]
        heading = normalize_japanese_text(" ".join(
            [str(top_meta.get("title") or ""), str(top_meta.get("faq_question") or ""), " ".join(str(p) for p in section_path)]
        ))
        return terms[0] in heading
    return False
"""

# ---------------------------------------------------------------------------
# F17 rag_core/answer_cache.py  (colloquial variants share one cache entry)
# ---------------------------------------------------------------------------
_AC_OLD_KEY = """        normalize_question_for_exact_match(question or ""),
"""
_AC_NEW_KEY = """        _cache_question_key(question or ""),
"""
_AC_OLD_DEF = """def build_key(
"""
_AC_NEW_DEF = """def _cache_question_key(question: str) -> str:
    \"\"\"Commercial fix F17: colloquial variants of one question share a cache entry
    (fillers do not change the answer) while query preprocessing is on.\"\"\"
    base = normalize_question_for_exact_match(question)
    if str(os.getenv("QUERY_PREPROCESS_ENABLED", "true")).strip().lower() not in {"1", "true", "yes", "on"}:
        return base
    try:
        from rag_core.ja_text import normalize_for_index
        from rag_core.query_preprocess import strip_colloquial

        stripped, applied = strip_colloquial(normalize_for_index(question))
        return stripped if applied and stripped else base
    except Exception:  # noqa: BLE001 - the cache key must never fail a request
        return base


def build_key(
"""

# ---------------------------------------------------------------------------
# F14 / F15 data files (JSON merge, idempotent)
# ---------------------------------------------------------------------------
_COLLOQUIAL_PREFIX_RULES = [
    r"^(?:お疲れ様です|お疲れさまです|おつかれさまです|お世話になります|お世話になっております|こんにちは|こんばんは|おはようございます)[、,。\s]*",
    r"^(?:ちょっと)?(?:聞きたい|ききたい|お聞きしたい|伺いたい|確認したい|教えてほしい)(?:んですけど|んだけど|のですが|ですが|んですが|ことがあって|ことがあるんですけど)[、,。\s]*",
    r"^(?:1つ|ひとつ|一つ)?(?:質問|確認|相談)(?:なんですけど|なんだけど|なのですが|ですが|です|があります|があるんですけど)[、,。\s]*",
]
_COLLOQUIAL_SUFFIX_RULES = [
    r"[、,\s]*(?:って|を|が|は)?(?:おしえて|教えて)(?:ー|〜|ほしい|欲しい|ちょうだい|頂戴|くれ|くれる|もらえる|よ|ね)?(?:んだけど|んですけど|かな)?[?？。!！\s]*$",
    r"(?:ってやつ|って件|の件|のやつ|のこと|ってこと)[?？。!！\s]*$",
    r"(?:でしたっけ|だったっけ|なんでしたっけ|なんだっけ|なんですっけ|だったかな|でしたかね)[?？。!！\s]*$",
    r"[、,\s]*(?:って|は)?(?:どうなってる|どうなってます|どうなってますか|どうなってるの|どうなの|どんな感じ|どんなの|どんなもの)[?？。!！\s]*$",
    r"(?<=[一-龥ァ-ヴーa-z0-9])(?<![送持待言行作取使払貼知買売立入切乗残守直戻])って(?=(?:何|なに|なん|いつ|どこ|どう|誰|だれ|いくら|何時|どの|どれ|どっち))",
    r"(?:なんでした|なんだった|でした|だった)(?:か)?[?？。!！\s]*$",
]
# F14: patterns of an earlier revision of this script that the ones above replace.
_COLLOQUIAL_OBSOLETE_RULES = [
    r"(?<=[一-龥ァ-ヴー][一-龥ァ-ヴー])って(?=(?:何|なに|なん|いつ|どこ|どう|誰|だれ|いくら|何時|どの|どれ|どっち))",
]
_SYNONYM_GROUPS = [
    {"canonical": "有給休暇", "terms": ["有給", "年休", "年次有給休暇", "有休"]},
    {"canonical": "給与", "terms": ["給料", "賃金", "お給料", "給与明細"]},
    {"canonical": "経費精算", "terms": ["経費", "立替", "立替精算", "精算"]},
    {"canonical": "勤怠", "terms": ["出退勤", "打刻", "勤怠管理"]},
    {"canonical": "テレワーク", "terms": ["在宅勤務", "リモートワーク", "在宅", "リモート"]},
    {"canonical": "社員証", "terms": ["IDカード", "入館証", "社員カード"]},
    {"canonical": "残業", "terms": ["時間外労働", "時間外", "超過勤務"]},
    {"canonical": "出張", "terms": ["出張申請", "出張旅費", "旅費精算"]},
    {"canonical": "通勤手当", "terms": ["交通費", "定期代", "通勤費"]},
    {"canonical": "年末調整", "terms": ["年調"]},
    {"canonical": "健康診断", "terms": ["健診", "定期健診", "健康診断書"]},
    {"canonical": "退職金", "terms": ["退職手当", "退職一時金"]},
    {"canonical": "パスワード", "terms": ["PW", "パスワード再設定", "パスワードリセット"]},
    {"canonical": "問い合わせ窓口", "terms": ["窓口", "問い合わせ先", "連絡先", "ヘルプデスク"]},
]


@dataclass
class JsonMerge:
    key: str
    path: str
    note: str
    field: str
    items: list
    identity: str | None = None  # dict items: the field that identifies an entry
    remove: list | None = None  # entries of an earlier revision to drop


def _merge_json(repo: Path, fix: JsonMerge, dry_run: bool) -> str:
    import json

    target = repo / fix.path
    if not target.is_file():
        return "FAIL -- file not found"
    data = json.loads(target.read_text(encoding="utf-8"))
    current = data.get(fix.field)
    if not isinstance(current, list):
        return f"FAIL -- field {fix.field!r} is not a list"
    obsolete = [item for item in current if item in (fix.remove or [])]
    current = [item for item in current if item not in obsolete]
    if fix.identity:
        present = {str(item.get(fix.identity)) for item in current if isinstance(item, dict)}
        missing = [item for item in fix.items if str(item.get(fix.identity)) not in present]
    else:
        present = set(current)
        missing = [item for item in fix.items if item not in present]
    if not missing and not obsolete:
        return "skip -- already applied"
    if dry_run:
        return f"would add {len(missing)} / remove {len(obsolete)}"
    backup = target.with_suffix(target.suffix + ".bak")
    if not backup.exists():
        shutil.copy2(target, backup)
    data[fix.field] = current + missing
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"ok -- added {len(missing)}"


JSON_MERGES: List[JsonMerge] = [
    JsonMerge("F14", "configs/query_rules/colloquial_ja.json", "口語の前置き（挨拶・前振り）を除去", "strip_prefix", _COLLOQUIAL_PREFIX_RULES),
    JsonMerge("F14", "configs/query_rules/colloquial_ja.json", "口語の語尾・話題マーカーを除去", "strip_suffix", _COLLOQUIAL_SUFFIX_RULES,
              remove=_COLLOQUIAL_OBSOLETE_RULES),
    JsonMerge("F15", "configs/japanese_business_synonyms.json", "一般的な社内用語の同義語", "synonym_groups", _SYNONYM_GROUPS, "canonical"),
]

FIXES: List[Fix] = [
    Fix("F1", "webapi/production_guard.py", _PG_OLD, _PG_NEW, "本番起動の必須項目を追加"),
    Fix("F2", "rag_core/qa.py", _QA_OLD_DEF, _QA_NEW_DEF, "LLM 回答に年・金額・件数の型照合"),
    Fix("F3", "rag_core/grounding.py", _GR_OLD_RE, _GR_NEW_RE, "否定の照合（正規表現の定義）"),
    Fix("F3", "rag_core/grounding.py", _GR_OLD_CHECK, _GR_NEW_CHECK, "否定の照合（check_claim）"),
    Fix("F4", "rag_core/grounding.py", _GR_OLD_VERIFY, _GR_NEW_VERIFY, "LLM 判定による棄却"),
    Fix("F4", "config.py", _CFG_OLD_GROUND, _CFG_NEW_GROUND, "GROUNDING_LLM_REJECT の設定"),
    Fix("F5", "config.py", _CFG_OLD_TIMEOUT, _CFG_NEW_TIMEOUT, "LLM タイムアウト 30 → 10 秒"),
    Fix("F6", "constraints/linux-py312.txt", "oauthlib==4.0.0\n", "oauthlib==4.0.1\n", "CVE-2026-49272"),
    Fix("F6", "constraints/linux-py312-embedding.txt", "oauthlib==4.0.0\n", "oauthlib==4.0.1\n", "CVE-2026-49272"),
    Fix("F7", "rag_core/retrieval.py", _RT_OLD_DEDUP, _RT_NEW_DEDUP, "親展開: 統合時にページを上書きしない"),
    Fix("F7", "rag_core/retrieval.py", _RT_OLD_NEW, _RT_NEW_NEW, "親展開: 引用ページは親のページ"),
    Fix("F8", "rag_core/qa.py", _QA_OLD_MSG, _QA_NEW_MSG, "year_mismatch の利用者向け文言"),
    Fix("F8", "rag_core/qa.py", _QA_OLD_GUARD, _QA_NEW_GUARD, "年度・年の不一致で拒否"),
    Fix("F9", "rag_grounded.py", _RG_OLD_RULE, _RG_NEW_RULE, "生成プロンプトの規則を追加"),
    Fix("F10", "schemas.py", _SC_OLD, _SC_NEW, "CitationOut.supporting_sentences"),
    Fix("F10", "rag_grounded.py", _RG_OLD_CIT_DEF, _RG_NEW_CIT_DEF, "文単位の引用（抽出関数）"),
    Fix("F10", "rag_grounded.py", _RG_OLD_CIT_ITEM, _RG_NEW_CIT_ITEM, "文単位の引用（payload）"),
    Fix("F11", "webapi/routers/chat.py", _CH_OLD_FN, _CH_NEW_FN, "段階別レイテンシ・引用 0 件の指標"),
    Fix("F11", "webapi/routers/chat.py", _CH_OLD_CALL1, _CH_NEW_CALL1, "/chat で記録"),
    Fix("F11", "webapi/routers/chat.py", _CH_OLD_CALL2, _CH_NEW_CALL2, "/chat/stream で記録"),
    Fix("F12", "rag_core/qa.py", _QA_OLD_PRE, _QA_NEW_PRE, "クエリ前処理（口語除去・同義語・誤字）を既定 on"),
    Fix("F13", "rag_core/approved_qa.py", _AQ_OLD_LOOKUP, _AQ_NEW_LOOKUP, "口語の質問でも承認済み回答をそのまま返す"),
    Fix("F13", "webapi/routers/chat.py", _CH_OLD_MODE, _CH_NEW_MODE, "answer_mode=approved_colloquial_match"),
    Fix("F16", "rag_core/qa.py", _QA_OLD_BYPASS, _QA_NEW_BYPASS, "見出し語に当たる短い口語の問いを too_general にしない"),
    Fix("F17", "rag_core/answer_cache.py", _AC_OLD_DEF, _AC_NEW_DEF, "口語の揺れで同じキャッシュ（関数）"),
    Fix("F17", "rag_core/answer_cache.py", _AC_OLD_KEY, _AC_NEW_KEY, "口語の揺れで同じキャッシュ（キー）"),
]


def apply(repo: Path, dry_run: bool) -> int:
    failures = 0
    backed_up = set()
    for fix in FIXES:
        target = repo / fix.path
        label = f"[{fix.key}] {fix.path}: {fix.note}"
        if not target.is_file():
            print(f"FAIL  {label} -- file not found")
            failures += 1
            continue
        text = target.read_text(encoding="utf-8")
        if fix.new in text:
            print(f"skip  {label} -- already applied")
            continue
        count = text.count(fix.old)
        if count != 1:
            print(f"FAIL  {label} -- anchor found {count} times (expected 1); the file differs from the analysed revision")
            failures += 1
            continue
        if dry_run:
            print(f"would {label}")
            continue
        backup = target.with_suffix(target.suffix + ".bak")
        if fix.path not in backed_up and not backup.exists():
            shutil.copy2(target, backup)
        backed_up.add(fix.path)
        target.write_text(text.replace(fix.old, fix.new, 1), encoding="utf-8")
        print(f"ok    {label}")
    for merge in JSON_MERGES:
        label = f"[{merge.key}] {merge.path}: {merge.note}"
        outcome = _merge_json(repo, merge, dry_run)
        if outcome.startswith("FAIL"):
            failures += 1
        elif outcome.startswith("ok"):
            backed_up.add(merge.path)
        print(f"{outcome.split(' ')[0]:<5} {label} -- {outcome.split('--', 1)[-1].strip()}")
    if backed_up and not dry_run:
        print("\nbackups (.bak, inside the repository; delete after review):")
        for p in sorted(backed_up):
            print(f"  {repo / (p + '.bak')}")
    return failures


def revert(repo: Path) -> int:
    restored = 0
    for path in sorted({f.path for f in FIXES} | {m.path for m in JSON_MERGES}):
        backup = repo / (path + ".bak")
        if backup.is_file():
            shutil.copy2(backup, repo / path)
            print(f"restored {path}")
            restored += 1
    print(f"{restored} file(s) restored from .bak (the .bak files are kept)")
    return 0


# 適用後の確認。リポジトリを import して、新しい挙動を小さな入力で検査する。
_CHECK_SCRIPT = r'''
import os
os.environ.setdefault("APP_ENV", "development")
from rag_core import grounding
broken, _ = grounding.check_claim("申請は対象外です", "申請は対象です", min_coverage=0.5)
assert "negation" in broken, ("F3 negation check missing", broken)
broken, _ = grounding.check_claim("申請は対象です", "申請は対象です", min_coverage=0.5)
assert "negation" not in broken, ("F3 false negation", broken)
print("F3 ok")

import config
assert hasattr(config, "GROUNDING_LLM_REJECT"), "F4 config missing"
print("F4/F5 ok")

from rag_core import qa
from rag_grounded import Chunk, build_citation_payloads
ch = [Chunk(id="c1", text="2024年度の年会費は10,000円です。更新は4月です。", source_doc="d", source_pages=[1])]
good = "- 2024年度の年会費は10,000円です。 [S1]"
bad = "- 2025年度の年会費は10,000円です。 [S1]"
assert qa._generated_answer_type_supported("2024年度の年会費はいくらですか", good, ch) is True, "F2 good rejected"
assert qa._generated_answer_type_supported("2025年度の年会費はいくらですか", bad, ch) is False, "F2 wrong year accepted"
print("F2 ok")

from rag_core.retrieval import RetrievedChunk
retrieved = [RetrievedChunk(text=ch[0].text, metadata={"vector_distance": 0.10, "source_doc": "d", "source_pages": [1]}, score=0.1)]
reason = qa.guard_merged_top("2025年度の年会費はいくらですか", "other", ch, retrieved)
assert reason == "year_mismatch", ("F8 expected year_mismatch", reason)
reason = qa.guard_merged_top("2024年度の年会費はいくらですか", "other", ch, retrieved)
assert reason != "year_mismatch", ("F8 false mismatch", reason)
print("F8 ok")

payloads = build_citation_payloads(good, ch)
assert payloads and payloads[0]["supporting_sentences"], ("F10 no supporting sentence", payloads)
assert "10,000円" in payloads[0]["supporting_sentences"][0], payloads
from schemas import CitationOut
CitationOut(**payloads[0])
print("F10 ok")

from rag_grounded import _rules
assert any("年度" in r for r in _rules("q")), "F9 rule missing"
print("F9 ok")

from webapi import metrics_registry
from webapi.routers import chat as chat_router
metrics_registry.reset()
chat_router._record_stage_latency_metrics({"stage_latency_ms": {"retrieval_ms": 300, "generation_ms": 2500}, "latency_ms": 2900},
                                          citations_count=0, guard_reason=None)
snap = metrics_registry.snapshot()
assert "chat_stage_latency_bucket" in snap and "chat_no_citation_total" in snap, snap.keys()
assert snap["chat_stage_latency_bucket"].get("retrieval:le500") == 1, snap["chat_stage_latency_bucket"]
print("F11 ok")

from webapi import production_guard
for k in ("GROUNDING_VERIFY_ENABLED", "RATE_LIMIT_ENABLED", "ENTERPRISE_OIDC_ENABLED", "ADMIN_SHARED_TOKEN_ACKNOWLEDGED"):
    os.environ.pop(k, None)
joined = "\n".join(production_guard.production_problems())
for needle in ("GROUNDING_VERIFY_ENABLED", "RATE_LIMIT_ENABLED", "ENTERPRISE_OIDC_ENABLED"):
    assert needle in joined, ("F1 missing", needle)
print("F1 ok")
print("F7: covered by --pytest (tests/test_retrieval_ja_integration.py)")

from rag_core.query_preprocess import strip_colloquial
from rag_core.ja_text import normalize_for_index
for raw, expected in (
    ("お疲れ様です。営業時間って何時ですか？", "営業時間何時"),
    ("退職の手続き教えてほしいんだけど", "退職の手続き"),
    ("有給ってどうなってますか", "有給"),
    ("社員証なんでしたっけ", "社員証"),
    ("ちょっと聞きたいんですけど、経費精算の締め日っていつ？", "経費精算の締め日いつ"),
):
    got, _applied = strip_colloquial(normalize_for_index(raw))
    assert got == expected, ("F14", raw, got)
got, _applied = strip_colloquial(normalize_for_index("退職の手続きは不要ですか"))
assert "不要" in got, ("F14 negation must survive", got)
print("F14 ok")

from rag_core import approved_qa
from rag_core.question_normalization import normalize_question_for_exact_match
import dataclasses
# A hand-built one-record index (the real loader needs a file). Fields without a
# default get a plausible value by name; unknown required fields abort the check.
kwargs = {}
for f in dataclasses.fields(approved_qa.ApprovedAnswer):
    if f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:
        continue
    kwargs[f.name] = {
        "qa_id": "qa-1", "tenant_id": "default",
        "question_text": "営業時間を教えてください", "question": "営業時間を教えてください",
        "normalized_question": normalize_question_for_exact_match("営業時間を教えてください"),
        "answer_text": "9時から18時です。", "answer": "9時から18時です。",
    }.get(f.name, [] if "citation" in f.name or "source" in f.name else "")
try:
    answer = approved_qa.ApprovedAnswer(**kwargs)
except TypeError as exc:
    print("F13 skipped (ApprovedAnswer signature differs):", exc)
else:
    index = approved_qa.ApprovedQAIndex(records=(answer,), by_tenant_question={("default", answer.normalized_question): answer})
    hit = approved_qa.lookup_approved_answer(index, "お疲れ様です。営業時間おしえてー", "default")
    assert hit is not None and hit.match_type == "colloquial", ("F13 colloquial miss", hit)
    miss = approved_qa.lookup_approved_answer(index, "営業時間は不要ですか", "default")
    assert miss is None, ("F13 false positive", miss)
    print("F13 ok")

from rag_core import answer_cache
k1 = answer_cache._cache_question_key("退職の手続き教えてほしいんだけど")
k2 = answer_cache._cache_question_key("退職の手続き")
assert k1 == k2, ("F17", k1, k2)
print("F17 ok")
print("CHECK PASSED")
'''


def check(repo: Path) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo)
    proc = subprocess.run([sys.executable, "-c", _CHECK_SCRIPT], cwd=str(repo), env=env, text=True,
                          capture_output=True)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stdout.write(proc.stderr[-4000:])
        print("CHECK FAILED")
    return proc.returncode


def run_pytest(repo: Path) -> int:
    candidates = [
        "tests/test_grounding.py",
        "tests/test_confidence_guard.py",
        "tests/test_stage5_accuracy.py",
        "tests/test_too_general_guard_redesign.py",
        "tests/test_retrieval_ja_integration.py",
        "tests/test_production_guard_embed_strict.py",
        "tests/test_production_guard_tenant_strict.py",
        "tests/test_llm_policy.py",
        "tests/test_feedback_token_signing.py",
        "tests/test_llm_call_hardening.py",
        "tests/test_chat_stream.py",
        "tests/test_metrics_observability.py",
        "tests/test_answer_cache.py",
    ]
    existing = [c for c in candidates if (repo / c).is_file()]
    if not existing:
        print("no candidate test files found")
        return 0
    cmd = [sys.executable, "-m", "pytest", "-q", *existing]
    print("running:", " ".join(cmd))
    return subprocess.run(cmd, cwd=str(repo)).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=None, help="リポジトリの場所（省略時: このファイルの 1 つ上）")
    parser.add_argument("--dry-run", action="store_true", help="変更せずに、何を変えるかだけ表示")
    parser.add_argument("--check", action="store_true", help="適用後の動作確認（import して検査）")
    parser.add_argument("--pytest", action="store_true", help="関連テストを実行")
    parser.add_argument("--revert", action="store_true", help=".bak から元に戻す")
    args = parser.parse_args()

    repo = Path(args.repo).resolve() if args.repo else Path(__file__).resolve().parent.parent
    if not (repo / "config.py").is_file() or not (repo / "webapi" / "production_guard.py").is_file():
        print(f"not the chatbot repository: {repo}")
        return 2
    print(f"repository: {repo}\n")

    if args.revert:
        return revert(repo)
    if args.check or args.pytest:
        rc = 0
        if args.check:
            rc |= check(repo)
        if args.pytest:
            rc |= run_pytest(repo)
        return rc

    failures = apply(repo, args.dry_run)
    if failures:
        print(f"\n{failures} fix(es) could not be applied; nothing else was changed for those entries.")
        return 1
    if not args.dry_run:
        print("\nnext: python3 tools/apply_commercial_fixes.py --repo <repo> --check && ... --pytest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
