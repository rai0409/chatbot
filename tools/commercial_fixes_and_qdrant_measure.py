#!/usr/bin/env python3
"""商用基準に向けた確定済みの修正（G1〜G13、G11 は欠番）の適用と、Qdrant サーバーでの計測（①〜④）を 1 ファイルにまとめたもの。

目的:
  2026-11-17 の分析（実測・再現・コード確認）で確定した欠陥のうち、小さく独立に直せるものを適用する。
  あわせて、ベクトル DB を Qdrant サーバーにしたときの速度・精度を、Chroma のときと同じ条件で計測する。
  各修正は「古い文字列 → 新しい文字列」の置換で、2 回かけても二重適用にならない（適用済みは skip と表示）。

使い方（--repo を省略すると、このファイルの 1 つ上の階層をリポジトリとみなす）:
  python3 tools/commercial_fixes_and_qdrant_measure.py fixes                 # 何を変えるかの表示だけ（既定）
  python3 tools/commercial_fixes_and_qdrant_measure.py fixes --apply         # 安全なtransactional対応が整うまで拒否
  python3 tools/commercial_fixes_and_qdrant_measure.py fixes --apply --only G1,G3
  python3 tools/commercial_fixes_and_qdrant_measure.py measure               # ①〜④のコマンドを表示するだけ（既定）
  python3 tools/commercial_fixes_and_qdrant_measure.py measure --run --step 2,3,4 --qdrant-url http://127.0.0.1:6333
  python3 tools/commercial_fixes_and_qdrant_measure.py measure --run --step 1 --qdrant-image qdrant/qdrant:<版>

入力:  リポジトリ内のファイル。計測は data/external/ 以下（git 管理外）の、既存の計測用コーパスを再利用する。
出力:  標準出力。修正はリポジトリ内のファイルのみ。計測結果は data/external/<作業フォルダ>/reports/*.json。
後始末: data/external/ はコミットしない。不要になったら 即削除/ へ移す。このスクリプトは何も削除しない。

適用する修正:
  G1  qa._finalize_generated_answer: LLM が「不足: …」だけを返したら棄権（insufficient_evidence）として扱う。
        これまでは「形式不正」とみなされ、質問と語が 1 つ重なるだけの抜粋に、引用付きで差し替わっていた。
  G2  rag_grounded.validate_output: 箇条書きでも「不足／不明」でもない行（引用の無い地の文）があれば不合格にする。
  G3  question_normalization.exact_match_key: 数字の間の小数点とハイフン、語中の長音を残す。
        「3.5日」と「35日」、「ビール」と「ビル」が同じキーになり、別の質問の承認済み回答を返していた。
        語末の長音（サーバー／サーバ）は、これまでどおり同じキーになる。
  G4  qa.guard_merged_top: 質問の内容語が、上位 3 件の根拠に現れる割合で棄権する（low_term_coverage）。
        既定は無効（GUARD_TERM_COVERAGE_MIN=0）。実際の質問で誤棄権率を測ってから 0.7〜0.8 を設定する。
        公開 FAQ 3,000 件での試算: 0.7 で誤回答 37 件中 16 件を止め、誤棄権 147 件中 1 件。0.8 で 21 件、誤棄権 7 件。
        （回答可能な質問は文書の文面から作ったもので、誤棄権は実際より少なく出る。）
  G5  production_guard: 本番で VECTOR_BACKEND=qdrant のとき QDRANT_PATH（組み込みモード）を拒否する。
  G6  routers/sources.sources_create: アップロード配下のフォルダは、そのテナントの取り込み元にしか登録できない。
  G7  admin_auth.revoke_admin_session: 署名を検証できた有効なセッションだけを失効させる（未認証の書き込みを止める）。
  G8  LLM クライアント: SDK 側の再試行を切る（max_retries=0）。アプリ側の再試行と重なっていた。
  G9  tools/verify_all.sh: pyflakes をインストールする（新しい環境で静的チェックが必ず FAIL になっていた）。
  G10 scripts/lock_dependencies.sh: ロック対象に requirements-anthropic.txt を加える。
  G12 バックアップ: compose に backups ボリュームを加え、backup.sh の対象に config/rag_profiles を加える。
  G13 webapi.main: 本番起動時に埋め込みモデルを読み込む（最初の質問が 20 秒以上待たされていた）。
        WARMUP_ON_START=false で無効化できる。失敗しても起動は止めず、警告を出す。

含めていないもの（設計か実測が先に要る）:
  距離ガードの閾値の再校正（scripts/calibrate_distance_guard.py を実データで実行）、取り込みジョブの再開、
  管理者トークンの失敗回数制限と個人識別、ハッシュロックの生成、.env.example と compose の Qdrant 既定化。
  回帰ゲートの「回答不能」5 件の誤ラベル（eval/cases/retrieval_v2_cases.jsonl の 21〜25 番）: このファイルは
  config/evaluation/real_vector_quality_baseline_v2.contract.json にハッシュで固定されており、直すには契約の
  ハッシュ更新と、実モデルでの基準レポートの作り直しが要る（文字列置換だけでは済まない）。

注意:
  - G2 で、前置きの文を付ける LLM の回答は抽出型に切り替わる（厳しくなる）。
  - G3 で完全一致キーが変わる。旧索引は読み取り時に質問を再検証し、新索引は別ファイルで作成する。
  - このスクリプトは個人情報・秘密情報を含まない。
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, Tuple


# --- 修正の定義 --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Edit:
    path: str
    old: str
    new: str
    marker: str  # 適用後にだけ現れる文字列（適用済みの判定に使う）


@dataclass(frozen=True)
class Fix:
    fix_id: str
    title: str
    edits: Tuple[Edit, ...]


FIXES: Tuple[Fix, ...] = (
    Fix("G1", "LLM の「不足」だけの回答を棄権として扱う", (
        Edit(
            "rag_core/qa.py",
            "def _finalize_generated_answer(\n",
            "_SHORTAGE_ONLY_LINE_RE = re.compile(r\"^\\s*(不足|不明)\\s*[:：]\")\n"
            "\n"
            "\n"
            "def _is_shortage_only_answer(raw: str) -> bool:\n"
            "    \"\"\"Fix G1: the model followed the prompt and wrote only 不足/不明 lines (no bullet).\"\"\"\n"
            "    lines = [ln.strip() for ln in (raw or \"\").splitlines() if ln.strip()]\n"
            "    return bool(lines) and all(_SHORTAGE_ONLY_LINE_RE.match(ln) for ln in lines)\n"
            "\n"
            "\n"
            "def _finalize_generated_answer(\n",
            "def _is_shortage_only_answer(",
        ),
        Edit(
            "rag_core/qa.py",
            "    raw = strip_reference_block(raw)\n"
            "    used_fallback = False\n"
            "    if not validate_output(raw, grounded, intent, config.PREFER_SORT_TERMS_CHANGE + config.PREFER_SORT_TERMS_RESET):\n",
            "    raw = strip_reference_block(raw)\n"
            "    used_fallback = False\n"
            "    # Fix G1: an answer of only \"不足: ...\" is an abstention, not a malformed answer.\n"
            "    # It used to fail validate_output and was replaced by quoted sentences with citations.\n"
            "    if _is_shortage_only_answer(raw):\n"
            "        return _guard_fallback_raw(\"insufficient_evidence\"), True\n"
            "    if not validate_output(raw, grounded, intent, config.PREFER_SORT_TERMS_CHANGE + config.PREFER_SORT_TERMS_RESET):\n",
            "    if _is_shortage_only_answer(raw):",
        ),
    )),
    Fix("G2", "引用の無い地の文を含む回答を不合格にする", (
        Edit(
            "rag_grounded.py",
            "    bullet_lines = [ln for ln in lines if ln.startswith(\"-\")]\n"
            "    if not bullet_lines:\n"
            "        return False\n"
            "    for ln in bullet_lines:\n"
            "        if not re.search(r\"(\\[S\\d+\\]\\s*)+$\", ln):\n",
            "    bullet_lines = [ln for ln in lines if ln.startswith(\"-\")]\n"
            "    if not bullet_lines:\n"
            "        return False\n"
            "    # Fix G2: a line that is neither a bullet nor a 不足/不明 line has no citation and\n"
            "    # was never checked, so an unsupported statement could reach the user.\n"
            "    if any(not ln.startswith(\"-\") and not _SHORTAGE_LINE_RE.match(_SOURCE_TAG_RE.sub(\"\", ln)) for ln in lines):\n"
            "        return False\n"
            "    for ln in bullet_lines:\n"
            "        if not re.search(r\"(\\[S\\d+\\]\\s*)+$\", ln):\n",
            "# Fix G2: a line that is neither a bullet",
        ),
    )),
    Fix("G3", "完全一致キーで小数点・数字間ハイフン・語中の長音を残す", (
        Edit(
            "rag_core/question_normalization.py",
            "    value = unicodedata.normalize(\"NFKC\", str(text or \"\")).replace(\"\\u3000\", \" \").lower()\n"
            "    return _EXACT_KEY_DROP_RE.sub(\"\", value)\n",
            "    value = unicodedata.normalize(\"NFKC\", str(text or \"\")).replace(\"\\u3000\", \" \").lower()\n"
            "    # Fix G3: \"3.5日\" and \"35日\", \"1-2\" and \"12\", \"ビール\" and \"ビル\" must not share a key (a\n"
            "    # collision returns another question's approved answer). Protect the decimal point and the\n"
            "    # hyphen between digits and a long vowel mark inside a word; a word-final one (サーバー) is\n"
            "    # still dropped, so サーバー / サーバ keep one key.\n"
            "    value = _EXACT_KEY_DECIMAL_RE.sub(\"\\x00\", value)\n"
            "    value = _EXACT_KEY_DIGIT_DASH_RE.sub(\"\\x01\", value)\n"
            "    value = _EXACT_KEY_INNER_LONG_VOWEL_RE.sub(\"\\x02\", value)\n"
            "    value = _EXACT_KEY_DROP_RE.sub(\"\", value)\n"
            "    return value.replace(\"\\x00\", \".\").replace(\"\\x01\", \"-\").replace(\"\\x02\", \"ー\")\n",
            "_EXACT_KEY_DECIMAL_RE.sub(",
        ),
        Edit(
            "rag_core/question_normalization.py",
            "def exact_match_key(text: str) -> str:\n",
            "_EXACT_KEY_DECIMAL_RE = re.compile(r\"(?<=\\d)\\.(?=\\d)\")\n"
            "_EXACT_KEY_DIGIT_DASH_RE = re.compile(r\"(?<=\\d)[‐-‒–—―\\-](?=\\d)\")\n"
            "_EXACT_KEY_INNER_LONG_VOWEL_RE = re.compile(r\"ー(?=[ァ-ヴ])\")\n"
            "\n"
            "\n"
            "def exact_match_key(text: str) -> str:\n",
            "_EXACT_KEY_DECIMAL_RE = re.compile(",
        ),
        Edit(
            "tests/test_stage3_ingestion.py",
            "@pytest.mark.parametrize(\"text\", [\"「営業時間」は？\", \"パスワード・再設定！\", \"Ｑ＆Ａ ー テスト\", \"A-1 (b) [c]\"])\n"
            "def test_exact_match_key_is_unchanged(text):\n"
            "    assert exact_match_key(text) == _old_exact_key(text)\n",
            "@pytest.mark.parametrize(\"text\", [\"「営業時間」は？\", \"手順・再設定！\", \"Ｑ＆Ａ ー テスト\", \"A-1 (b) [c]\"])\n"
            "def test_exact_match_key_is_unchanged(text):\n"
            "    assert exact_match_key(text) == _old_exact_key(text)\n"
            "\n"
            "\n"
            "def test_exact_match_key_keeps_decimals_and_inner_long_vowels():\n"
            "    # Fix G3: these pairs shared one key, so one question returned the other's approved answer.\n"
            "    assert exact_match_key(\"有給は3.5日ですか\") != exact_match_key(\"有給は35日ですか\")\n"
            "    assert exact_match_key(\"第1-2項\") != exact_match_key(\"第12項\")\n"
            "    assert exact_match_key(\"ビールの経費\") != exact_match_key(\"ビルの経費\")\n"
            "    # Unchanged: a word-final long vowel, thousands separators and dashes next to letters.\n"
            "    assert exact_match_key(\"サーバーの設定\") == exact_match_key(\"サーバの設定\")\n"
            "    assert exact_match_key(\"1,000円\") == exact_match_key(\"1000円\")\n"
            "    assert exact_match_key(\"A-1の手順\") == exact_match_key(\"A1の手順\")\n",
            "def test_exact_match_key_keeps_decimals_and_inner_long_vowels",
        ),
    )),
    Fix("G4", "質問語の被覆率ガード（既定は無効）", (
        Edit(
            "rag_core/qa.py",
            "def guard_merged_top(question: str, intent: str, chunks: Sequence[Chunk], retrieved: Sequence[RetrievedChunk]) -> Optional[str]:\n",
            "def _term_coverage_min() -> float:\n"
            "    \"\"\"Fix G4: GUARD_TERM_COVERAGE_MIN (0 = off). Measure false abstention on real questions before enabling.\"\"\"\n"
            "    try:\n"
            "        return max(0.0, min(1.0, float(_setting(\"GUARD_TERM_COVERAGE_MIN\", \"0\") or 0.0)))\n"
            "    except ValueError:\n"
            "        return 0.0\n"
            "\n"
            "\n"
            "def _question_term_coverage(question: str, chunks: Sequence[Chunk], top_n: int = 3) -> Optional[float]:\n"
            "    \"\"\"Share of the question's content words found in the top evidence; None when it cannot be judged.\"\"\"\n"
            "    try:\n"
            "        from rag_core import ja_tokenizer\n"
            "\n"
            "        tokens = ja_tokenizer.morpheme_tokens(normalize_japanese_text(question))\n"
            "        # Both sides go through the same tokenizer: it returns normalized forms (くん製 -> 薫製),\n"
            "        # which a substring search in the evidence text would miss.\n"
            "        evidence = set(ja_tokenizer.morpheme_tokens(normalize_japanese_text(\" \".join(ch.text for ch in list(chunks)[:top_n]))))\n"
            "    except Exception:  # noqa: BLE001 - no morphological tokenizer: this guard is skipped\n"
            "        return None\n"
            "    terms = list(dict.fromkeys(tokens))\n"
            "    if len(terms) < 2:\n"
            "        return None\n"
            "    return sum(1 for t in terms if t in evidence) / len(terms)\n"
            "\n"
            "\n"
            "def guard_merged_top(question: str, intent: str, chunks: Sequence[Chunk], retrieved: Sequence[RetrievedChunk]) -> Optional[str]:\n",
            "def _question_term_coverage(",
        ),
        Edit(
            "rag_core/qa.py",
            "            return \"salient_mismatch\"\n"
            "    if intent == \"other\":\n",
            "            return \"salient_mismatch\"\n"
            "    # Fix G4: evidence on the same topic that does not cover the question's content words\n"
            "    # (asked: frozen; found: smoked) was quoted as an answer. Off unless the threshold is set.\n"
            "    coverage_min = _term_coverage_min()\n"
            "    if coverage_min > 0.0:\n"
            "        coverage = _question_term_coverage(question, chunks)\n"
            "        if coverage is not None and coverage < coverage_min:\n"
            "            return \"low_term_coverage\"\n"
            "    if intent == \"other\":\n",
            "            return \"low_term_coverage\"\n",
        ),
        Edit(
            "rag_core/qa.py",
            "    \"salient_mismatch\": \"質問にある名称や番号を含む記載が見つかりませんでした。\",\n",
            "    \"salient_mismatch\": \"質問にある名称や番号を含む記載が見つかりませんでした。\",\n"
            "    \"low_term_coverage\": \"質問の内容に対応する記載が見つかりませんでした。\",\n",
            "    \"low_term_coverage\": \"",
        ),
    )),
    Fix("G5", "本番で Qdrant の組み込みモードを拒否する", (
        Edit(
            "webapi/production_guard.py",
            "        problems.append(\"VECTOR_BACKEND=qdrant when WEB_CONCURRENCY > 1 (Chroma is single-process)\")\n",
            "        problems.append(\"VECTOR_BACKEND=qdrant when WEB_CONCURRENCY > 1 (Chroma is single-process)\")\n"
            "    # Fix G5: QDRANT_PATH is the embedded development mode (no HNSW index, one process).\n"
            "    if (_env(\"VECTOR_BACKEND\") or \"chroma\").lower() == \"qdrant\" and _env(\"QDRANT_PATH\"):\n"
            "        problems.append(\"QDRANT_PATH must be empty (embedded Qdrant is for development; set QDRANT_URL to a server)\")\n",
            "QDRANT_PATH must be empty",
        ),
    )),
    Fix("G6", "取り込み元の登録でアップロードフォルダのテナントを照合する", (
        Edit(
            "webapi/routers/sources.py",
            "    scope = _request_scope(request)\n"
            "    require_scope(scope, req.tenant_id)\n"
            "    try:\n"
            "        record = source_registry.create(\n",
            "    scope = _request_scope(request)\n"
            "    require_scope(scope, req.tenant_id)\n"
            "    if req.kind == \"folder\" and req.path:\n"
            "        # Fix G6: a folder under uploads/<tenant>/ may only become a source of that tenant; an\n"
            "        # administrator of one tenant could otherwise pull another tenant's uploaded documents.\n"
            "        from webapi.routers import ingestion as _ingestion\n"
            "\n"
            "        _ingestion._check_upload_tenant([req.path], req.tenant_id)\n"
            "    try:\n"
            "        record = source_registry.create(\n",
            "_ingestion._check_upload_tenant([req.path], req.tenant_id)",
        ),
    )),
    Fix("G7", "ログアウトでは署名を検証できたセッションだけを失効させる", (
        Edit(
            "webapi/admin_auth.py",
            "    parsed = parse_admin_session(cookie)\n"
            "    if parsed is None:\n"
            "        return False\n"
            "    from webapi import state_backend\n"
            "\n"
            "    state_backend.revoke_session(parsed[0], float(parsed[2]))\n",
            "    parsed = parse_admin_session(cookie)\n"
            "    if parsed is None:\n"
            "        return False\n"
            "    # Fix G7: /admin/logout needs no authentication, and the cookie was parsed without\n"
            "    # checking its signature, so anyone could write revocation rows with any id and expiry.\n"
            "    expected = _expected_token()\n"
            "    if not expected or not _valid_admin_session(cookie, expected):\n"
            "        return False\n"
            "    from webapi import state_backend\n"
            "\n"
            "    state_backend.revoke_session(parsed[0], float(parsed[2]))\n",
            "# Fix G7: /admin/logout needs no authentication",
        ),
    )),
    Fix("G8", "LLM クライアントの SDK 再試行を切る", (
        Edit(
            "rag_core/utils.py",
            "def ensure_openai_client(base_url: Optional[str] = None):\n",
            "def ensure_openai_client(base_url: Optional[str] = None, max_retries: Optional[int] = None):\n"
            "    # Fix G8: max_retries=0 for chat clients (the gateway retries; the SDK default of 2 multiplied the wait).\n"
            "    extra = {} if max_retries is None else {\"max_retries\": int(max_retries)}\n",
            "max_retries: Optional[int] = None):",
        ),
        Edit(
            "rag_core/utils.py",
            "    if url:\n"
            "        return OpenAI(base_url=url, api_key=key)\n"
            "    return OpenAI(api_key=key)\n",
            "    if url:\n"
            "        return OpenAI(base_url=url, api_key=key, **extra)\n"
            "    return OpenAI(api_key=key, **extra)\n",
            "return OpenAI(api_key=key, **extra)",
        ),
        Edit(
            "rag_core/llm_profiles.py",
            "        return ensure_openai_client(base_url=profile.base_url or None)\n",
            "        return ensure_openai_client(base_url=profile.base_url or None, max_retries=0)  # Fix G8\n",
            "max_retries=0)  # Fix G8",
        ),
        Edit(
            "rag_core/llm_profiles.py",
            "    return OpenAI(base_url=profile.base_url, api_key=key) if profile.base_url else OpenAI(api_key=key)\n",
            "    # Fix G8: the gateway retries; the SDK's own retries are off (as for the Anthropic client).\n"
            "    if profile.base_url:\n"
            "        return OpenAI(base_url=profile.base_url, api_key=key, max_retries=0)\n"
            "    return OpenAI(api_key=key, max_retries=0)\n",
            "return OpenAI(api_key=key, max_retries=0)",
        ),
    )),
    Fix("G9", "verify_all.sh で pyflakes をインストールする", (
        Edit(
            "tools/verify_all.sh",
            "-r requirements-anthropic.txt pytest -c constraints/linux-py312.txt",
            "-r requirements-anthropic.txt pytest pyflakes -c constraints/linux-py312.txt",
            "pytest pyflakes -c constraints",
        ),
    )),
    Fix("G10", "ロック対象に Anthropic SDK を加える", (
        Edit(
            "scripts/lock_dependencies.sh",
            "BASE=(requirements.txt requirements-ja.txt requirements-pdf.txt requirements-state.txt requirements-qdrant.txt)",
            "BASE=(requirements.txt requirements-ja.txt requirements-pdf.txt requirements-state.txt requirements-qdrant.txt requirements-anthropic.txt)",
            "requirements-qdrant.txt requirements-anthropic.txt)",
        ),
    )),
    Fix("G12", "バックアップの保存先と対象を補う", (
        Edit(
            "docker-compose.yml",
            "      - ./models_cache:/app/models_cache\n",
            "      - ./models_cache:/app/models_cache\n"
            "      # Fix G12: the console backup writes to /app/backups; the root file system is read-only.\n"
            "      - ./backups:/app/backups\n",
            "      - ./backups:/app/backups\n",
        ),
        Edit(
            "scripts/backup.sh",
            "                 runs/conversations configs \\\n",
            "                 runs/conversations configs config/rag_profiles \\\n",
            "runs/conversations configs config/rag_profiles",
        ),
    )),
    Fix("G13", "本番起動時に埋め込みモデルを読み込む", (
        Edit(
            "webapi/main.py",
            "if production_guard.is_production():\n"
            "    _production_index_checks()\n",
            "def _production_warmup() -> None:\n"
            "    # Fix G13: the first question paid for loading the embedding model (20 s or more).\n"
            "    # WARMUP_ON_START=false switches it off; a failure is logged and does not stop the start.\n"
            "    if str(os.getenv(\"WARMUP_ON_START\", \"true\")).strip().lower() in {\"0\", \"false\", \"no\", \"off\"}:\n"
            "        return\n"
            "    try:\n"
            "        from rag_core import embedding_provider\n"
            "\n"
            "        if embedding_provider.is_local_provider():\n"
            "            embedding_provider.embed_queries([\"warmup\"])\n"
            "    except Exception:  # noqa: BLE001 - reported, never fatal\n"
            "        logging.warning(\"embedding warm-up failed; the first query will load the model\", exc_info=True)\n"
            "\n"
            "\n"
            "if production_guard.is_production():\n"
            "    _production_index_checks()\n"
            "    _production_warmup()\n",
            "def _production_warmup() -> None:",
        ),
    )),
)


# --- 修正の適用 --------------------------------------------------------------------------------------

_INTEGRATED_CHECKS = {
    "G1": ["tests/test_g_fixes_g3_g4.py::test_g1_shortage_only_abstains_without_extractive_fallback", "tests/test_chat_stream.py::test_g1_stream_shortage_is_insufficient_evidence"],
    "G2": ["tests/test_g_fixes_g3_g4.py::test_g2_rejects_unquoted_assertion_next_to_cited_bullet", "tests/test_chat_stream.py::test_g2_stream_never_emits_unverified_assertion", "tests/test_chat_stream.py::test_g2_stream_does_not_replay_bullets_removed_by_grounding"],
    "G3": ["tests/test_g_fixes_g3_g4.py::test_g3_meaningful_characters_do_not_collide", "tests/test_g_fixes_g3_g4.py::test_g3_legacy_index_revalidates_item_question", "tests/test_g_fixes_g3_g4.py::test_g3_ambiguous_current_key_fails_closed", "tests/test_g_fixes_g3_g4.py::test_g3_conflicting_answers_with_same_identifier_fail_closed"],
    "G4": ["tests/test_g_fixes_g3_g4.py::test_g4_guard_disabled_by_default_and_enabled_threshold", "tests/test_g_fixes_g3_g4.py::test_g4_changed_guard_never_reuses_answer"],
    "G5": ["tests/test_g_fixes_g3_g4.py::test_g5_rejects_embedded_qdrant_in_production"],
    "G6": ["tests/test_sources_admin_api.py::test_g6_upload_source_is_tenant_scoped"],
    "G7": ["tests/test_g_fixes_g3_g4.py::test_g7_invalid_session_does_not_write_revocation", "tests/test_admin_auth.py"],
    "G8": ["tests/test_g_fixes_g3_g4.py::test_g8_sdk_retry_disabled_only_on_chat_clone", "tests/test_g_fixes_g3_g4.py::test_g8_stream_sdk_retry_disabled_on_chat_clone"],
    "G9": ["tests/test_g_fixes_g3_g4.py::test_g9_verification_script_installs_static_checker"],
    "G10": ["tests/test_g_fixes_g3_g4.py::test_g10_anthropic_pinned_in_both_hash_locked_sets"],
    "G12": ["tests/test_deploy_ops.py::test_g12_backup_includes_rag_profiles_and_compose_backup_mount"],
    "G13": ["tests/test_g_fixes_g3_g4.py::test_g13_warmup_success_disabled_and_failure"],
}


def run_fixes(repo: Path, apply: bool, only: Sequence[str]) -> int:
    """Verify integrated behavior; automatic writes are disabled until transactional application exists."""
    import tempfile

    wanted = {x.strip().upper() for x in only if x.strip()} or set(_INTEGRATED_CHECKS)
    unknown = wanted - set(_INTEGRATED_CHECKS)
    if unknown:
        print(f"不明な修正 ID: {sorted(unknown)}", file=sys.stderr)
        return 2
    for fix_id in wanted:
        for node in _INTEGRATED_CHECKS[fix_id]:
            if not (repo / node.split("::", 1)[0]).is_file():
                print(f"[{fix_id}] 検証テストがありません", file=sys.stderr)
                return 2
    env = {**os.environ, "APP_ENV": "development", "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    failures = 0
    with tempfile.TemporaryDirectory(prefix="chatbot-g-fix-check-") as tmp:
        for fix_id in _INTEGRATED_CHECKS:
            if fix_id not in wanted:
                continue
            command = [_py(repo), "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--basetemp={tmp}/{fix_id}", *_INTEGRATED_CHECKS[fix_id]]
            try:
                result = subprocess.run(command, cwd=repo, env=env, text=True, capture_output=True, timeout=90)
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f"[{fix_id}] BLOCKED: {type(exc).__name__}", file=sys.stderr)
                failures += 1
                continue
            if result.returncode:
                print(f"[{fix_id}] BLOCKED: behavioral validation failed", file=sys.stderr)
                failures += 1
            else:
                print(f"[{fix_id}] APPLIED: behavioral checks passed")
    if apply:
        print("自動書込みは無効。全件が検証済みなら変更不要、未適用なら手動統合が必要。")
    return 1 if failures else 0


# --- 計測（①〜④） -----------------------------------------------------------------------------------

def _py(repo: Path) -> str:
    venv = repo / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def _show(command: Sequence[str], env: dict) -> None:
    prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    print(("  " + prefix + " \\\n  " if prefix else "  ") + " ".join(shlex.quote(c) for c in command))


def _call(command: Sequence[str], env: dict, repo: Path, run: bool) -> int:
    _show(command, env)
    if not run:
        return 0
    return subprocess.run(list(command), cwd=str(repo), env={**os.environ, **env}).returncode


def _wait_health(url: str, seconds: int) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/health", timeout=3):
                return True
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(2)
    return False


def run_measure(repo: Path, steps: Sequence[int], run: bool, qdrant_url: str, qdrant_image: str,
                workers: Sequence[int], port: int) -> int:
    if run:
        # Only the embedded Qdrant backend can be proven isolated here.
        # Never connect to a supplied server or start/stop an existing service.
        import tempfile

        selected = set(steps)
        if not selected or not selected <= {2, 3}:
            print("--run supports isolated local steps 2 and 3 only; server/API steps require separate isolation", file=sys.stderr)
            return 2
        if qdrant_url != "http://127.0.0.1:6333" or qdrant_image:
            print("--run refuses external Qdrant URLs and Docker images", file=sys.stderr)
            return 2
        work = Path(tempfile.mkdtemp(prefix="chatbot-qdrant-measure-", dir="/tmp"))
        scale = work / "scale"
        product = work / "product"
        common = {"APP_ENV": "development", "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HOME": str(work / "hf_cache"), "QDRANT_URL": "", "QDRANT_PATH": str(work / "qdrant")}
        py = _py(repo)
        print(f"isolated embedded Qdrant work: {work}; not comparable to a server benchmark")
        if 2 in selected:
            base = [py, "scripts/scale_benchmark.py"]
            commands = [
                base + ["generate", "--work", str(scale), "--docs", "12500", "--chunks-per-doc", "4"],
                base + ["ingest", "--work", str(scale), "--embedding", "dummy", "--backend", "qdrant-local"],
                base + ["search", "--work", str(scale), "--embedding", "dummy", "--backend", "qdrant-local", "--concurrency", "1,4,8", "--requests", "200"],
            ]
            for command in commands:
                result = _call(command, common, repo, True)
                if result:
                    return result
        if 3 in selected:
            return _call([py, "tools/eval_product_pipeline_jagov.py", "--backend", "qdrant", "--work", str(product)], common, repo, True)
        return 0
    py = _py(repo)
    ext = repo / "data" / "external"
    common = {"TMPDIR": str(ext / "tmp"), "PYTHONDONTWRITEBYTECODE": "1"}
    scale = ext / "scale50k_qsrv"
    product = ext / "product_eval_qsrv"
    status = 0

    if 1 in steps:
        print("\n① Qdrant サーバーを起動する（保存先はリポジトリ内 data/external/qdrant_storage）")
        if not qdrant_image:
            print("  --qdrant-image qdrant/qdrant:<本番で使う版> を指定する（版は固定する）。既にサーバーがあれば①は不要。")
        else:
            status |= _call(["docker", "run", "-d", "--name", "qdrant-bench", "-p", "127.0.0.1:6333:6333",
                             "-v", f"{ext / 'qdrant_storage'}:/qdrant/storage", qdrant_image], {}, repo, run)

    if 2 in steps:
        print("\n② 5 万チャンクの取り込みと検索（Chroma と同じコーパス・同じ質問。基準: p50 180ms、8 並列 4.3 件/秒）")
        source = ext / "scale50k"
        if run:
            (ext / "tmp").mkdir(parents=True, exist_ok=True)
            scale.mkdir(parents=True, exist_ok=True)
            if not (source / "corpus.jsonl").exists():
                status |= _call([py, "scripts/scale_benchmark.py", "generate", "--work", str(scale),
                                 "--docs", "12500", "--chunks-per-doc", "4"], common, repo, run)
            else:
                for name in ("corpus.jsonl", "queries.jsonl"):
                    (scale / name).write_bytes((source / name).read_bytes())
        else:
            print(f"  （{source} の corpus.jsonl / queries.jsonl を {scale} に複写。無ければ generate で作る）")
        base = [py, "scripts/scale_benchmark.py"]
        tail = ["--work", str(scale), "--embedding", "dummy", "--backend", "qdrant-url", "--qdrant-url", qdrant_url]
        status |= _call(base + ["ingest"] + tail, common, repo, run)
        status |= _call(base + ["search"] + tail + ["--concurrency", "1,4,8", "--requests", "200"], common, repo, run)
        print(f"  結果: {scale / 'reports' / 'search.json'}")

    qdrant_env = {**common, "HF_HOME": str(ext / "hf_cache"), "VECTOR_BACKEND": "qdrant", "QDRANT_URL": qdrant_url}

    if 3 in steps:
        print("\n③ 実モデルでの検索精度と回答パス（JaGovFaqs 3,000 文書。基準: R@10 100%、回答不能への回答 38%）")
        status |= _call([py, "tools/eval_product_pipeline_jagov.py", "--work", str(product)], qdrant_env, repo, run)
        print(f"  結果: {product / 'report.json'}")

    if 4 in steps:
        print("\n④ API 経由の /chat 負荷試験（③の後に行う。基準: 8 接続 p95 2,341ms、上限 約 6 件/秒）")
        server_env = {
            **qdrant_env, "APP_ENV": "development",
            "VECTORSTORE_DIR": str(product / "vectorstore"), "CHUNKS_JSONL_PATH": str(product / "keyword.jsonl"),
            "VECTORSTORE_COLLECTION_NAME": "product_eval_jagov", "INDEX_DIR": str(product / "index"),
            "RUNS_DIR": str(product / "runs"),
        }
        url = f"http://127.0.0.1:{port}"
        queries = ext / "product_eval" / "queries.jsonl"
        for count in workers:
            print(f"\n  -- uvicorn のワーカー数 {count}")
            server = [py, "-m", "uvicorn", "webapi.main:app", "--host", "127.0.0.1", "--port", str(port),
                      "--workers", str(count)]
            load = [py, "scripts/scale_benchmark.py", "http", "--work", str(product), "--url", url,
                    "--endpoint", "/chat", "--concurrency", "1,4,8", "--duration", "25"]
            if not run:
                _show(server, server_env)
                print(f"  （/health が返ってから。質問は {queries} を {product} に複写）")
                _show(load + ["--server-pid", "<uvicorn の PID>"], common)
                continue
            if not (product / "keyword.jsonl").exists():
                print("  ③を先に実行する（計測用の索引が無い）", file=sys.stderr)
                return 1
            if queries.exists():
                (product / "queries.jsonl").write_bytes(queries.read_bytes())
            _show(server, server_env)
            with open(product / f"server_workers{count}.log", "w", encoding="utf-8") as log:
                proc = subprocess.Popen(server, cwd=str(repo), env={**os.environ, **server_env}, stdout=log, stderr=log)
            try:
                if not _wait_health(url, 300):
                    print("  サーバーが起動しなかった（ログを確認）", file=sys.stderr)
                    status |= 1
                    continue
                status |= _call(load + ["--server-pid", str(proc.pid)], common, repo, True)
                report = product / "reports" / "http_chat.json"
                if report.exists():
                    report.replace(product / "reports" / f"http_chat_workers{count}.json")
                    print(f"  結果: {product / 'reports' / f'http_chat_workers{count}.json'}")
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    proc.kill()

    if not run:
        print("\n表示のみ。実行するには --run を付ける。")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    fixes = sub.add_parser("fixes", help="確定済みの修正 G1〜G13（G11 は欠番）")
    fixes.add_argument("--apply", action="store_true", help="書き込む（既定は表示のみ）")
    fixes.add_argument("--only", default="", help="例: G1,G3")
    measure = sub.add_parser("measure", help="Qdrant サーバーでの計測 ①〜④")
    measure.add_argument("--run", action="store_true", help="実行する（既定は表示のみ）")
    measure.add_argument("--step", default="1,2,3,4")
    measure.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    measure.add_argument("--qdrant-image", default="", help="①で使う。例: qdrant/qdrant:<版>")
    measure.add_argument("--workers", default="1,4", help="④で試す uvicorn のワーカー数")
    measure.add_argument("--port", type=int, default=8017)
    for command in (fixes, measure):
        command.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]), help="リポジトリの場所")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    if not (repo / "rag_core").is_dir():
        print(f"リポジトリではない: {repo}", file=sys.stderr)
        return 2
    if args.command == "fixes":
        return run_fixes(repo, args.apply, args.only.split(","))
    steps = [int(x) for x in args.step.split(",") if x.strip()]
    workers = [int(x) for x in args.workers.split(",") if x.strip()]
    return run_measure(repo, steps, args.run, args.qdrant_url, args.qdrant_image, workers, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
