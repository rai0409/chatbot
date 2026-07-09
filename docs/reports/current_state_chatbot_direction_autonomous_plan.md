# Current State, Chatbot Direction, And Autonomous Execution Plan

作成日: 2026-06-12
対象: `/home/rai/chatbot`(ブランチ `eval/real-vector-evidence`)
方法: ローカル検査のみ(git・grep・テスト収集・既存 eval 成果物)。実装・コミット・タグ・push は行っていない。Prompt017 は未実行。

---

## 1. Executive Summary

このリポジトリは、Phase 0〜5-C(prompt001〜016)が**すべてコミット・タグ・テスト付きで完了**した日本語向け citation-first RAG チャットボットである。検索品質の証拠は強い(実コーパス 41/41 ケースで正解チャンクが 1 位)。一方、ガードはキーワードのみモードで過剰拒否しており(回答可能 41 件中 18 件がフォールバック)、その校正(Prompt017)が次の作業として未実行のまま待機している。

**製品方向の結論**: 「日本企業向けプライベート RAG チャットボット(引用必須・承認回答ガバナンス付き)」を主方向、「表形式 Q&A 文書(入札質問回答・FAQ 表)の Q&A ボット化ワークフロー」を副ユースケースとする。汎用 SaaS・サポート SaaS・開発フレームワーク路線は棄却。

**自動実行の結論**: 残作業は小さなプロンプト 20 本ではなく、**6 個の大きなバッチプロンプト**(B1=既存 Prompt017 そのまま、B2=クロスエンコーダ昇格判定、B3=テナントオンボーディング/データパイプライン、B4=デプロイ運用パック、B5=セキュリティ運用パック、B6=観測性+ベータ判定レポート)に統合し、キュー+マスターランナープロンプトで連続実行する。閾値・既定値を変える B1/B2 だけは分離必須。

---

## 2. Evidence Checked

| 確認項目 | 結果 |
|---|---|
| ブランチ / main との差 | `eval/real-vector-evidence`、main より **74 コミット**先行(未マージ) |
| タグ | `prompt001`〜`prompt016` の **16 本すべて存在**。HEAD = `49a7695`(prompt016 タグ) |
| git status | 追跡ファイルはクリーン。未追跡は作業ファイルのみ(data/、pdfs/、backlog/、本分析プロンプト等) |
| プロンプトファイル | prompt012〜017 すべて存在。**prompt017 は存在するが未実行**(校正成果物・閾値変更なし) |
| eval/cases | 9 ファイル: smoke(21)、retrieval(25)、qa_pair(7)、real_corpus(51、うち手書き 16)、approved_qa_sample(3) — **計 107 ラベル付きケース** |
| runs/eval | `prompt016_real_corpus_baseline.json` ほか各プロンプトの smoke 結果が揃う |
| コード上の機能マーカー(grep) | `API_AUTH_TENANT_MAP`/`ApiAuthContext`/`enforce_tenant_authorization`(webapi/api_auth.py, main.py, tests)、`CROSS_ENCODER_RERANK_ENABLED`/`hybrid_rerank_ce`(rag_core, eval/runner.py, config.py)、`approved_qa_pair`(scripts/tests 9 ファイル)、`RAG_MAX_DISTANCE`(config.py) — すべて実在 |
| テスト収集 | `pytest --collect-only` → **622 テスト収集成功** |
| 既存レポート | `docs/reports/commercial_repo_competitor_analysis.md` 存在(市場ポジショニング分析済み) |

シークレット・.env は読んでいない。

## 3. Confirmed Current State

**完了済み(タグ+テスト+成果物で確認)**:

- **Prompt012 デプロイパッケージング**: Dockerfile(非 root・データ非同梱)、docker-compose(volume+healthcheck)、.env.example、CI。
- **Prompt013 API キー→テナント認可**: `API_AUTH_TENANT_MAP` によるサーバ側強制。未マップキーは fail-closed 403。/chat・/chat/stream・/chat/product-preview・/chat/feedback に適用。19 テスト。
- **Prompt014 クロスエンコーダ・リランク**: 既定オフ・プロファイルゲート付き。eval モード `hybrid_rerank_ce` 追加。失敗時は順序不変+警告 1 回。10 テスト。**昇格判定は未実施**。
- **Prompt015 Q+A ペアチャンク**: `scripts/approved_qa_to_pair_chunks.py`。検索ランキング不具合(chunk_role=parent で常に劣後)を発見・修正。回答側にしかない語でもペアチャンクがヒットすることをテスト・eval で実証。
- **Prompt016 評価コーパス拡充**: 実 PDF 由来 51 ケース(生成 35+手書き 16)+既存で計 107 ケース。**ベースライン計測済み**: gold ヒット 41/41(全件 1 位)、ガード過剰拒否 18/41、誤回答 2/10。
- **未実行**: Prompt017(実ベクトル・ガード閾値校正)。`RAG_MAX_DISTANCE` 等は未校正のまま(config.py の既定値)。

**未完了の主要事項(証拠ベース)**: ガード校正、CE 昇格判定、レートリミット(なし、grep 確認済み)、バックアップ/リストア(なし)、TLS/リバースプロキシ構成(なし)、メトリクス集約(プロセス内のみ)、テナントオンボーディング手順(なし)、設定済み `CHUNKS_JSONL_PATH` の実体ファイル欠落(vectorstore のみに存在)。

## 4. What Kind Of Chatbot This Should Become

| 候補 | 現状適合 | 商業性 | 実装ギャップ | リスク |
|---|---|---|---|---|
| 1. 汎用チャットボット SaaS | 低(UI・課金・セルフサーブなし) | 高いが激戦 | 巨大(UI/課金/スケール) | Dify 等 OSS・大手に正面衝突 |
| 2. **日本企業向けプライベート RAG** | **高**(引用必須・承認回答・テナント分離・Docker が全部効く) | 中〜高(データ主権・閉域需要) | 中(運用整備のみ) | 案件型で売上が線形 |
| 3. **表形式 Q&A ボット化ワークフロー** | **高**(qanda_table 抽出→承認 QA→ペアチャンクは独自実装、22/22 実証済み) | 中(入札 Q&A・FAQ 表は反復需要) | 小〜中(管理ワークフロー磨き) | 単体では市場が狭い |
| 4. カスタマーサポート SaaS | 中(回答 API はある) | 高いが Intercom/Zendesk 支配 | 巨大(チケット連携・UI・SLA) | 成熟度で勝負にならない |
| 5. 社内コンプライアンス/マニュアルボット | 中〜高 | 中 | 中 | 実態は候補 2 の一垂直 |
| 6. 開発者向け RAG フレームワーク | 低(縦特化・抽象化なし) | 低(LangChain 等が無料で支配) | 巨大(汎用化・docs・コミュニティ) | 強みの「意見の強さ」が弱みになる |

## 5. Product Direction Decision

**主方向(1 つ): 候補 2 — 日本企業向けプライベート RAG チャットボット。**
「自社文書から、引用付きで、嘘をつかずに答える。承認済みの答えは一字一句そのまま返す。データは顧客環境から出ない。」 これはリポジトリが既に実証している強み(citation-first、approved exact-match 22/22、実距離ガード、テナント分離+キー認可、SSE API、Docker、107 ケースの評価基盤)の延長線上にあり、追加実装は運用整備が中心で済む。

**副ユースケース(1 つ): 候補 3 — 表形式 Q&A 文書のボット化ワークフロー。**
実コーパス(観光デジタルアンケート業務の入札質問回答 PDF)で PDF→抽出→承認→ペアチャンク→評価まで一気通貫で動いており、主方向の「最初の売れる型」として機能する。自治体・入札・FAQ 表という反復性の高い文書タイプに刺さる。

**棄却**: 1・4・6(成熟度・競合・方向性の不一致)。5 は主方向の一垂直として自然に取り込まれる。UI 構築は精度・セキュリティ・デプロイのゲートが安定するまで着手しない(本プロンプトの制約どおり)。

## 6. Remaining Gaps By Responsibility

### A. Accuracy and Evidence — **ベータ前**
- 現状: 検索は gold 41/41 全件 1 位。ガードは未校正で過剰拒否 18/41・誤回答 2/10。CE リランクは実装済み・未昇格。
- 残作業: ①実ベクトル距離分布計測と閾値校正(Prompt017)②hybrid_rerank vs hybrid_rerank_ce 昇格判定 ③校正後ベースライン更新。
- 受入基準: 校正後、実ベクトルモードで false-abstain が現行 18/41 から有意に減り、false-answer が 2/10 を超えない。CE は promotion gate を通過した場合のみ既定化。
- 触るファイル: `config.py`(閾値既定)、`eval/guard_distance_calibration.py`(新規)、`eval/rerank_promotion_gate.py`(利用)、runs/eval/。
- 検証: 対象テスト、smoke 21/21、qa_pair 7/7、before/after ベースライン比較 JSON。

### B. Security and Tenant Operations — **レートリミットと鍵手順はベータ前、他はベータ直後**
- 現状: キー認証+キー→テナント認可(fail-closed)、admin 認証、debug 遮断、CORS 許可リストまで完了・テスト済み。
- 残作業: インバウンド・レートリミット(キー単位、標準ライブラリで)、鍵ローテーション手順、シークレット取扱い文書、テナント退去時のデータ削除手順。
- 受入基準: 超過時 429 が deterministc に返る(テスト)、ローテーション・削除のドキュメント化された手順が smoke で再現可能。
- 触るファイル: `webapi/api_auth.py` または新 `webapi/rate_limit.py`、`docs/`、tests。
- 検証: 新規テスト+`tests/test_api_key_tenant_authorization.py` 回帰+readiness smoke 117。

### C. Deployment and Operations — **デプロイ smoke とバックアップはベータ前、TLS 参照構成はベータ前(文書)、エクスポータはベータ後可**
- 現状: イメージ/compose/CI は構文検証まで。実ビルド+実データマウントの e2e 未実施。バックアップなし。TLS なし。メトリクスはプロセス内のみ。
- 残作業: `scripts/deploy_smoke.sh`(build→up→/health・/chat・/metrics curl→down)、`scripts/backup.sh`/`restore.sh`+復元検証、nginx/caddy 参照構成、ログローテーション/保持方針、メトリクス JSONL エクスポート。
- 受入基準: クリーン環境で deploy smoke exit 0、復元後 smoke 全通過、参照構成が文書化。
- 触るファイル: scripts/、docs/、(必要なら)docker-compose.yml 追記。
- 検証: deploy smoke 実行ログ、restore 後の readiness smoke。

### D. Product/API Surface — **最小限はベータ前(エラー契約)、他はベータ後**
- 現状: /chat・/chat/stream・/search・product-preview・feedback・admin review・/metrics。FastAPI 自動 OpenAPI はあるが整備されていない(not verified)。
- 残作業: エラー応答契約の文書化、OpenAPI メタデータ整備、API クイックスタート。
- 受入基準: 全公開エンドポイントのエラー形が文書と一致(テスト)。
- 触るファイル: webapi/main.py(メタデータのみ)、docs/。

### E. Data Ingestion and Admin Workflow — **ベータ前**
- 現状: pdf→canonical→ingest、qanda 表→承認 QA→レビュー→ペアチャンクの各スクリプトは揃うが、手順が分散。設定済み `CHUNKS_JSONL_PATH` の実体が欠落(vectorstore のみ)。
- 残作業: テナント 1 件を「PDF 投入→検証→ingest→approved QA→smoke」まで通す一括スクリプト+Runbook、canonical chunks ファイルの再生成と整合性検証、ingest 時のテナント誤り検出。
- 受入基準: 新規テナントの文書一式を 1 コマンドで投入し、検証レポートが green になる。
- 触るファイル: scripts/(オーケストレーションのみ、新規)、docs/runbooks/。
- 検証: サンプル PDF での一括実行ログ+approved QA runner 22/22 再現。

### F. Documentation and Commercial Packaging — **ベータ開始後(価格・契約は人間の作業)**
- 現状: README・readiness checklist・競合分析レポートは充実。運用者向け・顧客向け文書なし。
- 残作業: 運用者マニュアル(起動・監視・バックアップ・障害時)、顧客向け 1 枚説明、評価結果の対外要約。価格・契約条件は**人間が決める**(Claude は下書きまで)。
- 受入基準: 運用者が README なしで deploy smoke〜restore を再現できる。

### G. Automation Runner For Claude Execution — **今すぐ(これが本レポートの主目的)**
- 現状: 存在しない。これまで 1 プロンプト=1 手動実行。
- 残作業: §8 の設計に基づくキューファイル+マスターランナープロンプト(実装はファイル 2〜3 個の markdown/シェルのみ。アプリコードに触れない)。
- 受入基準: 1 回の起動で複数バッチが PASS→commit→tag→次へ進み、FAIL/PARTIAL で安全停止する。

## 7. Recommended Larger Prompt Batches

方針: **評価ゲート(既定値を変える)は分離、追加型(コード追加+文書+テスト)は統合**。Prompt017 は**そのまま維持**(閾値変更を含む唯一のリスク作業であり、単独実行・単独検証が正しい)。

| # | バッチ | 統合される作業 | 分離理由/統合理由 |
|---|---|---|---|
| B1 | **Prompt017(既存のまま)** 実ベクトル・ガード校正 | — | 既定閾値を変えうる。分離必須 |
| B2 | CE 昇格判定(旧 prompt018 相当) | 計測+判定+(通過時のみ)プロファイル既定変更 | ランキング既定を変えうる。分離必須 |
| B3 | テナント・データパイプライン | E 全部+B のテナント削除手順 | すべて追加型・相互依存が強い |
| B4 | デプロイ運用パック | C のうち deploy smoke+バックアップ/リストア+TLS 参照+ログ保持 | 追加型。アプリコード変更なし |
| B5 | セキュリティ運用パック | レートリミット+鍵ローテーション+シークレット文書 | リクエストパスに触れるため B4 と分離。ただし内部では一体 |
| B6 | 観測性+ベータ判定 | メトリクスエクスポート+アラート閾値+readiness report 再生成+go/no-go レポート | 追加型+最終ゲート文書 |

各バッチの定義:

### B1: Prompt017 — Real-Vector Guard Calibration(既存ファイルをそのまま実行)
- Goal/含む作業/非目標/検証: `prompts/claude/prompt017_phase5d_real_vector_guard_calibration.md` に記載済みのとおり。
- Stop conditions: 埋め込みモデル未キャッシュ、stamped collection 不在で ingest 不可、分布が分離不能(→PARTIAL 報告で停止)。
- Artifacts: 距離分布レポート(JSON+MD)、before/after ベースライン、(根拠が明確な場合のみ)config.py 閾値変更。
- Tag: `prompt017-phase5d-guard-calibration`

### B2: Cross-Encoder Promotion Eval
- Goal: 校正済み実ベクトル環境で hybrid_rerank vs hybrid_rerank_ce を `eval/rerank_promotion_gate.py` で比較し、昇格可否を判定。
- 含む: 比較実行、判定レポート、通過時のみ `pilot_high_accuracy` 等プロファイルへの組込み(production_safe は不変)。
- 非目標: 既定(グローバル)有効化、モデルダウンロードの必須化。
- Stop: CE モデルがローカルに無い(PARTIAL)、ゲート不通過(レポートのみで終了=正常)。
- 検証: smoke 21/21、qa_pair 7/7、CE テスト 10、ゲート判定 JSON。
- Tag: `prompt018-phase5e-ce-promotion-eval`

### B3: Tenant Onboarding And Data Pipeline
- Goal: 新規テナントの文書投入を 1 コマンド+Runbook 化。
- 含む: `scripts/onboard_tenant.sh`(pdf→canonical→pair→検証→ingest dry-run)、canonical chunks 実体の再生成と `CHUNKS_JSONL_PATH` 整合、テナント削除(オフボーディング)手順+スクリプト、誤テナント ingest 検出。
- 非目標: 管理 UI、DB 化。
- Stop: 既存 vectorstore を破壊しうる操作が必要になった場合(専用 collection 以外への書込みは禁止)。
- 検証: サンプル PDF 一括実行 green、approved QA 22/22 再現、tenant isolation テスト回帰。
- Tag: `prompt019-phase6a-tenant-pipeline`

### B4: Deployment Operations Pack
- Goal: 「デプロイできる」を「運用できる」にする。
- 含む: `scripts/deploy_smoke.sh`(build→compose up→e2e curl→down)、`scripts/backup.sh`/`restore.sh`+復元検証、nginx/caddy 参照構成(docs)、ログローテーション/保持方針(docs)。
- 非目標: k8s、CD、クラウド固有構成。
- Stop: Docker ビルドがローカルで不可能(PARTIAL)。
- 検証: deploy smoke exit 0、restore 後 readiness smoke 117、collect-only。
- Tag: `prompt020-phase6b-deploy-ops`

### B5: Security Operations Pack
- Goal: 公開運用の最低限の防御と手順。
- 含む: キー単位レートリミット(標準ライブラリ、既定オフ)、429 契約+テスト、鍵ローテーション Runbook、シークレット取扱い文書。
- 非目標: WAF、OAuth/JWT、課金。
- Stop: 既存認可テストが壊れて 1 回の修正で直らない場合。
- 検証: 新テスト+test_api_auth/test_api_key_tenant_authorization 回帰+readiness smoke。
- Tag: `prompt021-phase6c-security-ops`

### B6: Observability Export And Beta Gate Report
- Goal: 監視可能性とベータ可否判定。
- 含む: メトリクス JSONL エクスポート(プロセス内→ファイル集約)、アラート閾値文書(ガード発火率・フォールバック率・エラー率)、`eval/production_readiness_report.py` 再生成、ベータ go/no-go レポート(全バッチ結果の集約)。
- 非目標: Prometheus サーバ構築、外部 SaaS 監視。
- Stop: なし(追加型+文書)。
- 検証: エクスポート単体テスト、readiness report 生成、全 smoke green。
- Tag: `prompt022-phase6d-beta-gate`

順序: **B1 → B2 → B3 → B4 → B5 → B6**(A の証拠ゲート 2 つを先に通すことで、以降の運用整備が「校正済みの製品」を対象にできる)。B3/B4/B5 は相互独立なので、FAIL 時は飛ばして続行可能(キュー設計で表現)。

## 8. Proposed Autonomous Claude Execution Strategy

設計のみ(本ランでは実装しない)。

**構成ファイル(3 つ)**:
1. `prompts/claude/auto/queue.md` — 実行キュー。1 行 1 バッチ: `状態(pending/running/pass/partial/fail) | プロンプトパス | タグ名 | 検証コマンド要約`。
2. `prompts/claude/auto/master_runner.md` — マスタープロンプト(下記ループ仕様)。
3. `runs/auto/` — 各バッチの実行ログ・判定・失敗レポートの出力先。

**マスターランナーのループ仕様**:
```
1. 前提検査: pwd / branch が eval/real-vector-evidence(または指定作業ブランチ)/
   git status クリーン(想定内の未追跡のみ)/ 直近タグがキューと整合。不整合なら即停止。
2. queue.md から先頭の pending を 1 件取得。なければ「全完了」を報告して終了。
3. 該当プロンプトファイルを読み、そのまま実行(各バッチプロンプトが自身の
   scope/stop conditions/verification を持つ)。
4. バッチ内検証を実行(対象テスト→collect-only→smoke 21/21→qa_pair 7/7→
   readiness smoke→セキュリティ回帰)。1 つでも fail なら 1 回だけ bounded fix、
   再検証。
5. PASS: git add(関連ファイルのみ)→ commit(定型メッセージ)→ tag(バッチ定義の
   タグ名)→ queue.md を pass に更新 → runs/auto/<tag>.md に結果記録 → 2 へ戻る。
6. PARTIAL/FAIL: コミットしない。runs/auto/failure_<tag>.md に原因・診断・人間への
   質問を書き、queue.md を partial/fail に更新して**停止**。
   (B3/B4/B5 など独立バッチは「skip して次へ」を queue 行のフラグで許可可能)
7. セッション上限: 1 回の起動で最大 3 バッチまで(コンテキスト劣化防止)。
   上限到達時は正常停止し「次の起動で継続」と報告。
```

**安全レール(必須)**:
- `.env`・シークレットの読取り・出力・推測の禁止(プロンプト明記+Claude Code の deny 設定 `Read(.env*)` を推奨)。
- `git push` 禁止・リモート操作禁止・main 直接変更禁止。
- 本番 vectorstore collection への書込み禁止(専用 eval/tenant collection のみ)。
- 破壊的操作(削除・上書き)が必要になったら停止して人間に委ねる。
- 閾値・既定値の変更は B1/B2 のみで、かつ測定根拠が明確な場合のみ。

**起動方法(人間の操作は 1 コマンド)**:
`claude "Read and execute prompts/claude/auto/master_runner.md"` を実行するだけ。中断後の再開も同じコマンド(queue.md が状態を持つため冪等)。許可プロンプトを減らすには `.claude/settings.local.json` の allowlist に検証系コマンド(pytest、eval.runner、bash scripts/*_smoke.sh 等)を追加しておく。

## 9. What To Do Next Immediately

1. **(人間・1 分)** このレポートの方向決定(§5)とバッチ構成(§7)を承認するか決める。
2. **(Claude・次の 1 ラン)** `prompts/claude/auto/queue.md` と `master_runner.md` を作成する小プロンプトを実行(アプリコード変更なし・低リスク)。B1〜B6 のうち B2〜B6 のバッチプロンプトファイル生成もこのランに含めてよい(B1 は既存 prompt017 を参照するだけ)。
3. **(Claude・自動)** マスターランナー起動 → B1(Prompt017)から順に消化。
4. **(人間・随時)** PARTIAL/FAIL 停止時の判断と、F 群(価格・契約)の意思決定のみ。

## 10. Risks And Stop Conditions

- **ガード校正が分離不能な分布を示す**(B1): 閾値を変えず PARTIAL 報告で停止する設計になっている。コーパス拡充(追加 PDF)が次善策。
- **CE モデルがローカルに無い**(B2): ダウンロードはしない方針のため PARTIAL。人間が一度だけモデルをキャッシュするか、B2 をスキップして B3 以降を先行。
- **長時間自動実行によるコンテキスト劣化**: セッションあたり最大 3 バッチの上限で対処。queue.md が状態を持つので再起動で継続可能。
- **自動コミットの汚染**: バッチごとに「関連ファイルのみ add」「検証 green 時のみ commit」を強制。失敗時は working tree を残して停止(人間が diff を確認できる)。
- **main 未マージの累積(74 コミット)**: ベータ前に main への PR/マージ判断が必要(自動化対象外。人間の判断)。
- **データ面**: data/・pdfs/ は未追跡のまま(意図的とみられる)。自動ランナーはこれらを commit 対象に含めてはならない。

## 11. Appendix: Commands Run

| コマンド | 結果要約 |
|---|---|
| `pwd` / `git branch --show-current` | `/home/rai/chatbot` / `eval/real-vector-evidence` |
| `git status --short` | 追跡ファイルクリーン。未追跡は作業ファイルのみ |
| `git log --oneline --decorate -30` | HEAD=`49a7695`(prompt016 タグ)。prompt001〜016 連続 |
| `git tag --list "prompt*" \| sort -V` | 16 タグすべて存在 |
| `git rev-list --count main..HEAD` | 74 |
| `ls prompts/claude/prompt01[2-7]*` | prompt012〜017 すべて存在(017 未実行) |
| `ls eval/cases/` | 9 ファイル・計 107 ラベル付きケース |
| `ls -t runs/eval/` | prompt016 ベースライン+各 smoke 結果 |
| grep(API_AUTH_TENANT_MAP 等 8 マーカー) | すべて該当ファイルに実在(§2 の表) |
| `pytest --collect-only -q` | 622 テスト収集成功 |
| 既存レポート・README・checklist・baseline JSON | 内容を §3〜§6 に反映(ガード過剰拒否 18/41、誤回答 2/10、gold 41/41 1 位) |

実装・コミット・タグ・push・Prompt017 実行は行っていない。`.env`・シークレットは読んでいない。
