# Current Chatbot Commercialization Assessment

作成日: 2026-06-12
対象: `/home/rai/chatbot`(ブランチ `eval/real-vector-evidence`、HEAD `86e4adf` = `prompt018-multiformat-ingestion-foundation`)
方法: ローカル検査のみ。コード変更・コミット・タグ・push・Prompt017/019 の実行は行っていない。`.env`・シークレットは読んでいない。

---

## 1. Executive Summary

現在のリポジトリは「**日本語企業内文書 AI 回答ボット**」の技術基盤としてほぼ完成形に近い: PDF/Word/Excel/CSV/PowerPoint を共通のチャンク契約に変換し(prompt018)、引用必須で回答し、承認済み Q&A は決定的に返し、証拠が弱ければ正直に回答を拒否し、テナント分離+API キー認可で守られ、Docker で配備できる。633 テスト・107+ ラベル付き評価ケース・実コーパスベースラインという証拠も揃っている。

商用化の最大の欠落は 2 系統: ①**ガード未校正**(実測: 回答可能 41 件中 18 件を誤って拒否、回答不能 10 件中 2 件に誤回答)— これが Prompt017。②**実顧客データを安全に取り込む運用経路の不在**(マニフェスト・重複検知・ドライラン・バックアップなし)— これが Prompt019 以降。

**次の一手は Prompt017(実ベクトル・ガード校正)**。評価コーパスはそのために整備済みであり、精度の主張ができない限り有償 PoC のデモも成立しないため。実顧客データの取り込みは Prompt019 完了+バックアップ整備まで行うべきではない。

## 2. Evidence Checked

| 項目 | 結果 |
|---|---|
| ブランチ / main 差分 | `eval/real-vector-evidence`、main より **76 コミット**先行(未マージ) |
| タグ | prompt001〜016 + **prompt018** の 17 本 + 分析タグ 2 本。**prompt017 タグなし(未実行)** |
| git status | 追跡ファイルはクリーン。未追跡は作業ファイルのみ(data/、pdfs/、backlog/ 等) |
| runs/eval | prompt016 ベースライン+prompt016/018 smoke 結果あり。**prompt017/019 成果物なし(未実行)** |
| converters | `rag_core/document_converters/` に csv/xlsx/docx/pptx/pdf の 5 形式+`scripts/convert_document_to_canonical_jsonl.py` 実在 |
| マーカー grep | `API_AUTH_TENANT_MAP`・`ApiAuthContext`・`enforce_tenant_authorization`(webapi+tests)、`CROSS_ENCODER_RERANK_ENABLED`・`hybrid_rerank_ce`(rag_core/eval/tests)、`convert_file_to_canonical_chunks`(converters/CLI/tests)、`RAG_MAX_DISTANCE`(config.py のみ=未校正) — すべて実在 |
| テスト | `pytest --collect-only` → **633 件収集成功** |
| 既知の欠落 | 設定済み `CHUNKS_JSONL_PATH`(index/chunks.canonical.bytype.dedup.jsonl)の**実体ファイルが不在**(vectorstore のみに存在) |
| 既存レポート | 競合分析・方向性/自動化計画の 2 本を確認し本書に反映 |

## 3. Current Chatbot Capability

今この瞬間にできること:

- **入力形式**: PDF(実証済み・OCR オプション付き)、Word/docx・Excel/xlsx・CSV・PowerPoint/pptx(prompt018、標準ライブラリのみで変換、FAQ 表は自動で Q+A ペアチャンク化)。変換は JSONL 出力のみで vectorstore へは手動 ingest。
- **回答フロー**: ①正規化質問が承認済み Q&A に完全一致 → LLM を呼ばず承認回答をそのまま返す(22/22 実証)②不一致 → ハイブリッド検索(BM25+ベクトル)→ 日本語ヒューリスティック・リランク(+任意のクロスエンコーダ、既定オフ)→ 親チャンク展開 → 引用付き生成 ③証拠が弱い → ガード理由付きで「回答できません」。
- **検索/ガードの実測**: 実コーパス 41 ケース全件で正解チャンクが 1 位。ただしガードは未校正で、回答可能 41 件中 18 件を誤拒否、回答不能 10 件中 2 件に誤回答(runs/eval/prompt016_real_corpus_baseline.json)。
- **テナント/セキュリティ**: チャンク・キャッシュ・承認 QA すべてテナント分離。API キー認証+`API_AUTH_TENANT_MAP` によるキー→テナント認可(未マップキーは 403 fail-closed)。admin 認証・debug 遮断・CORS 許可リスト。**レートリミットなし**。
- **デプロイ**: Dockerfile(非 root・データ非同梱)+compose(volume/healthcheck)+CI。構文検証済みだが**実ビルド+実データでの e2e 未実施**。TLS・バックアップなし。
- **評価**: 決定的 smoke(21/21)・qa_pair(7/7)・実コーパス 51 ケース+計 107+ ラベル付きケース、`/metrics` カウンタ(プロセス内)。

## 4. Completed Work By Responsibility

- **A. 検索・回答品質**: ハイブリッド検索、日本語リランク、親展開、実距離ガード(未校正)、クロスエンコーダ段(既定オフ・昇格未判定)。gold 41/41 1 位の実測。
- **B. 承認 Q&A ガバナンス**: 抽出(表形式 PDF)→ レビュー/承認 → 完全一致ルート(22/22)→ Q+A ペアチャンク化(prompt015、ランキング不具合の発見・修正込み)。
- **C. 多形式取り込み**: 5 形式 → 共通チャンク契約(prompt018)。FAQ 表の Q+A ペア検出、決定的 ID、テナント伝播、11 テスト+実 keyword 検索での互換実証。
- **D. テナント/セキュリティ**: 分離(検索・キャッシュ・承認 QA)+キー→テナント認可(prompt013、19 テスト)。
- **E. API/プロダクト面**: /chat、/chat/stream(SSE)、/search、/chat/product-preview、/chat/feedback、admin review、/metrics、製品プロファイル(production_safe 等)。
- **F. 評価/証拠**: 107+ ケース、実コーパスベースライン JSON、各プロンプトの smoke 履歴、readiness report 生成器。
- **G. デプロイ/運用**: Docker 一式+CI(prompt012)。運用系(バックアップ・TLS・監視集約)は未。
- **H. 自動化/プロンプト運用**: prompt001〜018 をタグ駆動で完遂。自動化計画レポートあり。マスターランナーは設計のみ・未実装。

## 5. Product Direction Decision

| 候補 | 判定 |
|---|---|
| 1. 汎用チャットボット SaaS | ✗ UI・課金・セルフサーブなし。激戦市場 |
| 2. 汎用 RAG プラットフォーム(Dify/Flowise 型) | ✗ ノーコード UI・コネクタ・コミュニティで勝負にならない |
| 3. カスタマーサポート SaaS クローン | ✗ チケット連携・SLA・製品成熟度の差が埋まらない |
| 4. 開発者向けフレームワーク | ✗ 縦特化の「意見の強い」実装であり汎用化と矛盾 |
| 5. **日本語プライベート企業内文書 AI 回答ボット** | ✅ **採用** |

**方向 5 が正しい。** 根拠: リポジトリの差別化要素(引用必須、承認回答の決定性、嘘をつかないガード、日本語/表形式文書の扱い、テナント認可、プライベート配備)がすべてこの方向の必須要件と一致し、かつ prompt018 で「顧客の現実の文書(Excel FAQ・Word マニュアル・PPT 資料)」を受け入れる入口が完成した。競合(クラウド RAG・SaaS ボット)が構造的に弱い「データ主権・閉域・日本語表文書」の交点を突ける。副ユースケースは引き続き「表形式 Q&A 文書(入札質問回答・FAQ 表)のボット化」。

## 6. Commercial Readiness Scores

| カテゴリ | 点 | 根拠 / 欠落 / +10〜20 点の条件 |
|---|---|---|
| Commercial PoC readiness | **75** | 根拠: 全コア機能+5 形式入力+実証済み承認ルート。欠落: ガード未校正・デプロイ e2e 未実施。**+15**: Prompt017 完了+deploy smoke |
| Limited beta readiness | **50** | 根拠: 認証/分離/パッケージングは有る。欠落: バックアップ・TLS・レートリミット・オンボーディング経路。**+15**: P019+deploy/security ops 完了 |
| Production SaaS readiness | **20** | 根拠: プロセス内メトリクス・単一コンテナ・運用自動化なし。欠落: ほぼ全運用系。**+10**: 監視集約+復旧手順 |
| RAG trustworthiness | **60** | 根拠: gold 41/41 1 位、正直な abstain、引用必須。欠落: ガード未校正(誤拒否 44%)。**+20**: Prompt017 で false-abstain 半減を実証 |
| Multi-format ingestion readiness | **65** | 根拠: 5 形式・11 テスト・検索互換実証・依存追加ゼロ。欠落: 実文書での大規模検証・OCR・画像。**+15**: P019 のサンプル文書 eval+実文書投入実績 |
| Data onboarding readiness | **30** | 根拠: 変換/ingest スクリプトは個別に存在。欠落: マニフェスト・重複検知・ドライラン・一括手順(=P019)。**+20**: P019 完了 |
| API/security readiness | **60** | 根拠: キー認証+キー→テナント認可 fail-closed+admin/debug 保護。欠落: レートリミット・鍵ローテーション・シークレット管理。**+15**: security ops バッチ |
| Tenant readiness | **60** | 根拠: データ分離+認可+19 テスト。欠落: クォータ・オンボーディング/退去手順。**+15**: P019+退去 Runbook |
| Deployment readiness | **45** | 根拠: Docker 一式+CI+compose 構文検証。欠落: 実ビルド e2e・バックアップ・TLS。**+20**: deploy ops バッチ |
| Operations readiness | **40** | 根拠: /metrics・ガード理由カウンタ・stage latency。欠落: プロセス外集約・アラート・ログ保持。**+15**: メトリクスエクスポート+閾値文書 |
| Product UX/API readiness | **45** | 根拠: JSON+SSE の安定 API・製品プロファイル。欠落: API ドキュメント・エラー契約・UI(意図的に未着手)。**+10**: OpenAPI 整備+エラー契約 |
| Evaluation/evidence readiness | **70** | 根拠: 107+ ケース・ベースライン JSON・決定的 smoke。欠落: 実ベクトルでの計測・形式別 eval(P019)。**+15**: P017 の距離分布計測 |

## 7. Gaps Before Real Customer Data Import

**実顧客データを 1 件でも取り込む前に必須**(チェックリスト):

- [x] 全作業がコミット/タグ済みであること(確認済み: HEAD=prompt018 タグ、追跡ファイルクリーン)
- [ ] **ドライラン・オンボーディング**(P019): 変換→検証→「何が ingest されるか」の表示のみ、既定で書込みなし
- [ ] **インポート・マニフェスト**: source_doc ごとのチャンク数・ID ハッシュ・tenant 集合の記録
- [ ] **重複検知**: ID 重複・同一テキスト別 ID・ファイル間衝突の検出
- [ ] **テナント不一致検知**: 1 文書内の tenant_id 混在・指定テナントとの不一致で停止
- [ ] **非本番 collection への ingest のみ**: 明示的な collection 名指定を必須化(既定=本番禁止)
- [ ] **PII/セキュリティ予防**: 取り込み文書の PII 取り扱い方針、監査ログに本文を残さない確認(既存実装は ID ベース — 確認済み)、アクセスは API キー+テナント認可必須
- [ ] **バックアップ/リストア**: vectorstore・approved_qa・監査ログの取得と復元検証(現状なし — deploy ops バッチ)
- [ ] **canonical チャンク検証**: `check_chunks_canonical` 通過+embedding fingerprint 照合
- [ ] **サンプル文書 eval**: 同形式の合成文書で retrieval/abstain が green であること(P019)
- [ ] **.env 非露出**: コンテナ・ログ・レポートに環境値を出さない(現状の設計は準拠)
- [ ] **本番 vectorstore 無変更の保証**: 上記すべてが揃うまで実データは合成データで代替

## 8. Gaps Before Paid PoC

- ガード校正(Prompt017)— 誤拒否 44% のままでは精度説明ができない
- デプロイ e2e smoke(build→up→/chat 実打鍵)
- §7 のうちドライラン+マニフェスト+非本番 collection(顧客文書を預かるなら全部)
- 鍵ローテーション手順と PoC 向け契約上の免責(精度保証なし・ベストエフォート)

## 9. Gaps Before Limited Beta

- バックアップ/リストア実証、TLS/リバースプロキシ参照構成
- レートリミット+429 契約
- テナントオンボーディング/退去 Runbook
- メトリクス集約とアラート閾値(ガード発火率・エラー率)
- クロスエンコーダ昇格判定(精度向上の頭打ち解消)
- main へのマージ判断(76 コミット滞留はベータ前に解消すべき)

## 10. Gaps Before Production SaaS

- 水平スケール(キャッシュ/メトリクスの外部ストア化)、マルチワーカー集約
- 監視・オンコール体制、SLA、課金、セルフサーブ UI
- セキュリティ認証(SOC2 等)・脆弱性管理プロセス
- ※当面は「プライベート配備テンプレート」として売り、SaaS 化は需要実証後で良い

## 11. Recommended Next Step

**Prompt017(実ベクトル・ガード校正)を最優先で実行する。**

比較judgment:

| 候補 | 判定 |
|---|---|
| **Prompt017 ガード校正** | ✅ **今すぐ**。唯一の実測済み品質欠陥(誤拒否 18/41)を直す。評価コーパスは P016 でこのために整備済み。PoC デモの説得力に直結 |
| Prompt019 オンボーディング | 2 番目。実顧客データの前提だが、合成文書デモには不要。P017 と独立なので直後に実行 |
| deploy smoke / backup | 3 番目。PoC 前に必要だが精度の証拠が先 |
| rate limiting / 鍵ローテーション | 4 番目。外部公開・ベータ前で十分 |
| UI/アップロード | ❌ まだ早い。精度・セキュリティ・デプロイのゲートが安定するまで着手しない(方針どおり) |

理由: 商談で最初に聞かれるのは「どのくらい正しく答えるか/嘘をつかないか」であり、その数字が今は「誤拒否 44%」のまま。Prompt017 は前提(コーパス・ベースライン・実 PDF・ローカル埋め込み)がすべて揃った状態で待機しており、費用対効果が最も高い。

## 12. Commercial Rollout Plan

| Stage | 必要能力 | 受入基準 | 通すべきテスト/eval | 成果物 | リスク |
|---|---|---|---|---|---|
| **0. ローカル技術実証**(現在地〜P017) | 校正済みガード+既存全機能 | false-abstain が大幅減・false-answer ≤ 現状、smoke/qa_pair green | 633+ テスト、smoke 21/21、qa_pair 7/7、校正前後比較 | 距離分布レポート、校正済み config | 分布が分離不能(→閾値据え置きで PARTIAL) |
| **1. 社内デモ(合成文書)** | P019 オンボーディング+multiformat eval | 合成 Excel/Word/PPT 一式を 1 コマンドでドライラン→専用 collection に ingest→/chat 実演 | multiformat eval green、deploy smoke exit 0 | デモ手順書、マニフェスト例 | デモ文書が現実の汚さを反映しない |
| **2. 単一顧客有償 PoC** | バックアップ/TLS/鍵手順+§7 全項目 | 顧客文書で gold-hit/abstain を計測し合意した目標を達成、データ削除手順実演 | 顧客コーパス eval+restore 実証 | PoC 報告書、精度実測値 | 顧客 PDF が画像系(OCR 未対応)→事前サンプル検収で回避 |
| **3. 限定ベータ(2〜5 社)** | レートリミット・監視集約・オンボーディング Runbook・main マージ | 2 社以上で 30 日無人運用、重大インシデント 0、ガード発火率の監視 | 全 smoke+security 回帰+月次 eval | 運用マニュアル、アラート定義 | 運用負荷の線形増加(Runbook の質が鍵) |
| **4. 本番 SaaS / 配備テンプレート** | スケール・SLA・課金 or テンプレート整備 | §10 解消 | 負荷試験、DR 訓練 | 配備テンプレート v1 | SaaS 化判断を需要実証前に急がないこと |

## 13. Recommended Prompt Batches

| # | バッチ | Goal / Why now | 触る場所 | 成果物 | 検証 | Tag | リスク | Runner 可 | 人間レビュー |
|---|---|---|---|---|---|---|---|---|---|
| B1 | **Prompt017 ガード校正**(既存をそのまま) | 唯一の実測品質欠陥を修正。前提完備 | eval/(新計測スクリプト)、config.py(根拠が明確な場合のみ) | 距離分布レポート、before/after | smoke、qa_pair、校正比較 | `prompt017-phase5d-guard-calibration` | **高**(既定値変更) | 可(停止条件厳守) | **後で必須**(閾値判断) |
| B2 | **Prompt019 オンボーディング**(既存をそのまま) | 実データ受け入れの前提。B1 と独立 | scripts/、eval/cases/sample_docs/ | マニフェスト、ドライラン、multiformat eval | 3 本の eval+対象テスト | `prompt019-multiformat-onboarding` | 中 | 可 | 後で推奨 |
| B3 | デプロイ運用パック | PoC 配備の前提 | scripts/、docs/ | deploy_smoke.sh、backup/restore、TLS 参照 | deploy smoke exit 0、restore 後 smoke | `prompt020-deploy-ops` | 低 | 可 | 不要 |
| B4 | セキュリティ運用パック | ベータ公開の前提 | webapi/(rate limit)、docs/ | レートリミット+429 テスト、鍵 Runbook | auth 回帰+新テスト | `prompt021-security-ops` | 中(リクエスト経路) | 可 | 後で推奨 |
| B5 | CE 昇格判定 | 精度上積みのゲート判定 | eval/、configs/(通過時のみ) | 比較レポート、判定 | promotion gate JSON | `prompt022-ce-promotion-eval` | 中(プロファイル変更) | 可 | **後で必須**(昇格判断) |
| B6 | 観測性+ベータ判定 | 無人運用の前提+go/no-go | webapi/metrics 周辺、eval/ | エクスポート、アラート閾値、ベータ判定書 | エクスポートテスト+全 smoke | `prompt023-beta-gate` | 低 | 可 | **後で必須**(go/no-go) |
| B7 | (任意)API 契約/ドキュメント | PoC 顧客の開発者体験 | webapi/(メタデータのみ)、docs/ | OpenAPI 整備、エラー契約 | 契約テスト | `prompt024-api-contract` | 低 | 可 | 不要 |

順序: **B1 → B2 → B3 → B4 → B5 → B6 →(B7)**。B2〜B4 は相互独立で、失敗時スキップ可。

## 14. Automation Strategy

前回レポートの設計を維持: `prompts/claude/auto/queue.md`(状態)+`master_runner.md`(ループ: 前提検査→次バッチ実行→検証→PASS でコミット/タグ→FAIL/PARTIAL で安全停止)+`runs/auto/` ログ。安全レール: .env 読取り禁止(settings の deny 推奨)、push 禁止、本番 collection 書込み禁止、セッション上限 3 バッチ、queue による冪等再開。**B1 と B5 の「判断」だけは自動コミット前に人間レビューを挟む**(runner は計測まで実行し、閾値/昇格の適用は레포ート提示で停止する設定が安全)。

## 15. Risks And Stop Conditions

- **ガード分布が分離不能**(B1): 閾値据え置きで PARTIAL 停止 → コーパス追加が次善策。
- **顧客 PDF が画像スキャン**(Stage 2): OCR は既存 `--ocr` 経路のみ。PoC 契約前にサンプル文書検収を必須化。
- **実データの早期投入誘惑**: §7 チェックリスト完了前の実データ取り込みは禁止(本レポートの最重要警告)。
- **main 未マージ 76 コミット**: ベータ前に PR/マージ判断(人間)。
- **自動実行の汚染**: 検証 green 時のみコミット、失敗時は working tree 温存で停止。
- **XLSX 日付セル**: シリアル値のまま出る既知制限(README 記載済み)。日付重要文書では事前変換が必要。

## 16. Appendix: Commands Run

| コマンド | 結果 |
|---|---|
| `pwd` / `git branch --show-current` | `/home/rai/chatbot` / `eval/real-vector-evidence` |
| `git status --short` | 追跡クリーン、未追跡は作業ファイルのみ |
| `git log --oneline --decorate -30` | HEAD=`86e4adf`(prompt018 タグ) |
| `git tag --list "prompt*" \| sort -V` | prompt001〜016+018 の 17 本(017 なし=未実行) |
| `git rev-list --count main..HEAD` | 76 |
| `ls prompts/claude{,/product,/analysis}` | prompt017・019 ファイル存在(ともに未実行) |
| `ls eval/cases` / `ls runs/eval` | 107+ ケース群/prompt016 ベースライン+016/018 smoke。017/019 成果物なし |
| `ls rag_core/document_converters` / `ls scripts` | 5 形式 converter+CLI(scripts 17 本) |
| マーカー grep(18 種) | すべて該当ファイルに実在(§2) |
| `pytest --collect-only -q` | 633 テスト収集 |
| `ls index/chunks.canonical...jsonl` | **欠落確認**(CHUNKS_JSONL_PATH の実体なし) |
| 閲覧 | 既存レポート 2 本、README、readiness checklist、prompt016 ベースライン JSON(gold 41/41 1 位、誤拒否 18/41、誤回答 2/10) |

実装・コミット・タグ・push・Prompt017/019 実行・マスターランナー実行は行っていない。
