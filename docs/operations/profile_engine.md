# プロファイルの検索ブースト規則（profile_engine）

顧客プロファイルの `retrieval_boost_rules.json` を、検索結果の並べ替えに使う。
`PROFILE_BOOST_ENABLED=true` のときだけ動く（既定 `false`。プロファイルを評価してから有効にする）。

## 動き

1. 規則が適用されるのは、`applies_to_question_type` が質問タイプと同じで、`when_question_contains_any` が
   空でないときは質問にその語のどれかが含まれるとき。`enabled: false` の規則は無視する。
2. 規則の語 = `positive_terms` / `negative_terms` ＋ `positive_categories` / `negative_categories` に属する
   有効な `domain_terms` の `term` と `aliases`。
3. チャンク本文（NFKC 正規化・大文字小文字無視）にプラスの語が 1 つでもあれば `+|positive_weight|`、
   マイナスの語が 1 つでもあれば `-|negative_weight|` の点数。規則ごと・向きごとに 1 回だけ数え、全規則の点数を足す。
4. スコア（距離。小さいほど良い）を `score -= PROFILE_BOOST_MAX_DELTA × clamp(点数 / 100, -1, 1)` で動かし、
   安定ソートで並べ直す。1 つの設定で全規則の影響の上限が決まる。
5. 位置は `rerank_chunks` とキーワードブーストの後、クロスエンコーダの前。プロファイルの読み込みに警告が
   ある場合は、回答検証と同じく適用しない。

トレースは `staged_retrieval.profile_boost`（適用した規則・動かした件数）、チャンクの
`score_details.profile_boost`（規則・点数・変化量）。回答キャッシュのキーに設定が含まれる。

## 注意

- 重みは 0〜100 の目盛りとして扱う（デモの +35 / −25 / +40 など）。`PROFILE_BOOST_MAX_DELTA` の初期値 0.05 は
  既存のキーワードブーストと同じで、実データの評価（2-4 以降）で調整する。
- `domain_terms` の `weight` / `negative_weight` は使わない（規則側の重みだけ）。
- 検証規則（`validation_rules.json`）は従来から回答生成後に実行されている。今回、`enabled: false` の規則が
  適用されていた不具合を直した。

### 回答検証と公開

検証を省略した場合、`profile_validation` は `passed: false`、`skipped: true` を返します。警告、Guard、適用規則なしの理由は `reason` で区別します。規則未配置でも通常の回答は継続します。

`fallback_if_failed: true` の規則に不合格の場合、抽出回答を同じ規則で再検証し、合格した場合のみ公開します。抽出結果が空または不合格の場合は引用なしの拒否回答を返します。`fallback_if_failed: false` は記録のみで、不合格でも回答を変更しません。Guard による拒否はプロファイルで解除されません。

強制検証のストリームは公開前に最大100万文字までバッファし、検証後の回答のみを送ります。上限超過や生成途中の失敗では未検証本文を公開しません。検証済みの回答のみキャッシュし、旧形式のキャッシュは再利用しません。

## 照合の正規化（2026-11）

規則の語とチャンク本文の照合は、キーワード索引と同じ `ja_text.normalize_for_index` で正規化する
（NFKC・小文字。`NORMALIZATION_PROFILE=index` ではダッシュ・長音・波ダッシュの表記ゆれも揃う）。

## 文書の優先度（doc_boost_rules / doc_priority）

社内規程と古いメモが食い違うときに、規程を上に出すための仕組み。`profile_engine.apply_doc_priority(chunks, profile)`
が、並べ終えた一覧（クロスエンコーダの後）で、優先する文書を最大 `max_rank_shift`（既定 2）位まで上げる。
**呼び出しの組み込みは `rag_core/qa.py` で行う（未配線）。**

- チャンクの任意メタデータ（`canonical_metadata.normalize_source_metadata` が整える。無くてもよい）
  - `doc_priority`: 整数（既定 0、±1000 に丸める）。大きいほど優先、負は後ろへ。
  - `authority`: 発行元（例 `人事部`、`規程`）。
  - `effective_from`: 施行日（`YYYY-MM-DD`。不正な値は捨てる）。
- プロファイルの `doc_boost_rules`:
  ```json
  [{"rule_id": "regulations_first", "points": 40, "match": {"source_doc": "規程_*.pdf"}},
   {"rule_id": "hr", "points": 20, "match": {"authority": "人事部"}},
   {"rule_id": "old_memo", "points": -30, "match": {"source_doc": "*旧*"}}]
  ```
  `match` のキーは `source_doc`・`doc_id`（glob）と `authority`（正規化後の一致）。全キーが一致した規則の点数と
  `doc_priority` を足し、`移動量 = max_rank_shift × clamp(点数/100, -1, 1)` 位だけ動かす。同順位は優先度の高い方、
  次に元の順。スコアは新しい順に昇順で振り直す（後段のスコア順ソートでも順序が保たれる）。不明なキーを含む規則は一致しない。
  チャンクの `score_details.doc_priority` に点数・規則・移動前後の順位が残る。

## /chat のテナント回答ポリシー（2026-11）

/chat・/chat/stream・/search は、フラグに関係なく次を適用する（`webapi/routers/chat.py` の `resolve_chat_answer_policy`）。

| 条件 | 動作 |
| --- | --- |
| テナント対応表で `status: disabled`、または `unknown_tenant_policy: reject` で未登録 | 403 |
| `answer_policy.allow_exact_answer=false`（製品プロファイル／テナントの `chat_policy`） | 承認済み回答を使わない |
| `answer_policy.human_review_on_low_confidence=true` | ガード・フォールバック・検証失敗の回答に `review_required: true`（応答と監査イベント）。キャッシュしない |
| テナントの `chat_policy.allow_llm_calls=false` | LLM を呼ばず抽出回答。監査イベントに `llm_allowed: false` |
| `CHAT_USE_TENANT_PROFILE=true` のとき製品プロファイルの `cost_policy.allow_llm_calls=false` | 同上 |

- **判断**: 製品プロファイルの `allow_llm_calls` は同梱のすべてで `false`（類似候補プレビュー用に作られた値）。
  フラグなしで適用すると LLM 運用の全テナントで生成が止まるため、プロファイル側の値は従来どおり
  `CHAT_USE_TENANT_PROFILE=true` のときだけ使う。LLM を絶対に使わせないテナントは、テナント対応表
  （`configs/product_tenants/default.json`）の項目に `"chat_policy": {"allow_llm_calls": false}` を書く。
  こちらはフラグに関係なく適用する。`chat_policy` は絞る方向にだけ効く（許可を広げることはできない）。
- 既定のテナント（`default`、`production_safe`）は従来どおり答える（承認済み回答あり、LLM はフラグに従う）。
  ただし `human_review_on_low_confidence=true` のため、低信頼の回答には `review_required` が付く。
- 抽出回答の強制は `rag_core/llm.py` の要求単位の切り替え（`extractive_only()`）を使う。それが無い版では、
  LLM を使う設定のときに要求を 503（`error_type: llm_not_allowed_for_tenant`）で断る（禁止された LLM を呼ばない）。
- 対応表が読めないときは止めずに従来の既定で答え、`chat_answer_policy_error_total` に数える。

## 回答キャッシュ（2026-11）

- キーに、プロンプトテンプレート（`prompts/*.jinja`）、`grounded_domain_rules.json`（または
  `GROUNDED_DOMAIN_RULES_PATH`）、製品テナント／プロファイルの JSON、`CHAT_USE_TENANT_PROFILE`、
  `QUERY_PREPROCESS_ENABLED`（config の値。無ければ true）を含める。
- `rag_profile_id` が default 以外でもキャッシュする（プロファイルのファイルはキーに含まれる）。
- `build_key(..., principal_digest="")`: 利用者ごとに見える文書が違う検索（ACL）を入れるときは、
  **呼び出し側が利用者の権限のダイジェストを必ず渡す**。渡さないと、ある利用者だけが読める文書から作った
  回答が同じテナントの別の利用者に返る。
- 2026-11-27: `/chat` と `/chat/stream` は `answer_cache.access_scope_digest(api_auth)` を `principal_digest` に渡す。
  今の検索はテナント単位（メールは全員から除外）なので値は空で、テナント内で 1 件を共有する。
  `MAIL_SEARCH_ENABLED` または `RETRIEVAL_ACL_SCOPED=1` のときは、認証情報の `acl_principals` / `principals` /
  `groups` のダイジェストがキーに入り、権限の違う利用者は別のエントリになる。権限が分からない利用者の回答は
  キャッシュを読まず書かない（`chat_cache_skip_total{access_scope_unknown}`）。
- テナントの回答方針の `allow_llm_calls` もキーに入る。「LLM を使わない」に切り替えたテナントに、
  それ以前に LLM が書いた回答は返らない。
- キャッシュの各エントリは内部の `_origin`（作った要求の `request_id`、索引世代、キーの要約）を持ち、応答には出さない。
  キャッシュから返した応答には新しい `request_id` を付け、根拠記録（`docs/operations/answer_evidence.md`）の
  `origin_request_id` が元の要求を指す。
- Redis のキーは `ac:<tenant>:<sha256>`。`answer_cache.clear_tenant(tenant_id)` でそのテナントだけ消す
  （`clear()` は全体）。承認済み QA の変更で呼ばれる。
- `ANSWER_CACHE_STATE_MEMO_MS`: キーの元になるファイル状態・設定の要約を保持する時間（既定 `APP_ENV=production`
  で 1000、それ以外 0）。変更の反映が最大その時間遅れる。
- キーワード索引を裏で作り直している間（古い索引で答えている間）の回答はキャッシュしない。
- 既知の制限: 配信中のキーワードコーパスは全テナントで 1 ファイルで、テナント別の識別子がないため、
  どのテナントの取り込みでも全テナントのキーが変わる。承認済み QA のファイルも同様。
- 口語の言い換え（教えて／教えてほしいんだけど）は別のキーのまま（同じ検索クエリ・意図・質問タイプで
  あることを確かめる 2 段のキーは未実装）。
