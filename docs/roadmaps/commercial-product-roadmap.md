# 商用プロダクト到達目標

## 対象product

蔵伝 / KuraDen は、日本語の社内文書と approved Q&A を対象に、citation-first の回答と証拠不足時の abstain を提供する、限定用途の複数tenant向けナレッジAIである。対象外は、全世界向け汎用企業検索、無制限の自律操作、根拠のない生成回答である。

現在点は暫定47点前後、目標は90～92点である。評価軸は検索・回答品質、citation/abstain、QA governance、取込、UI、tenant/ACL、認証、publish/rollback、同期、性能、運用、法務、実利用実証である。90点は実装完了ではなく、限定顧客での安全な運用実証、復旧実証、監査可能性を満たした時点とする。

## 現在までにできていること

### 実装済み（現在branchでcodeを確認）

- approved Q&A exact lookup、alias/類似候補、citation metadata、hybrid retrieval、reranker、parent expansion、guard/fallback、retrieval trace。
- tenant normalization、API/admin auth、rate limit、OIDC関連実装、RBAC、audit/review action、conversation store、branding、review queue、ingestion job、collection promotion。
- PDF/XLSX等のdocument converter、canonical metadata、product route policy、product contract。
- approved Q&A governed source/review/apply/export workflow と固定fixture分離のテスト。

### test確認済み

- review workflow対象3テストは27 passed（直近確認）。
- 全pytestは現在のPython asyncio cross-thread wakeup障害により未完走である。

### real-vector品質baseline（契約分離）

- deterministic regression baseline と real-vector quality baseline は別契約であり、前者はlive dense-search品質の証拠ではない。
- fixed local assetによるreal dense pathは確認済み。現fixtureではdense gold hit@5は20/20、MRR@5は0.9375、nDCG@5は0.9530803155822426、hybrid/hybrid-rerankはともに1.0。
- ただしBM25 missをdense/hybridが固有回復するcaseはなく、semantic incremental contributionは未実証。lexical-mismatch/paraphraseを含むsemantic challenge setで無回帰の利益を示すまで、production promotionは主張しない。

### 実装はあるが運用未実証

- auth、tenant、audit、review queue、ingestion/promotion、chat UI、OIDC関連実装。

### report記載のみで現在branch未確認 / 未実装として扱うもの

- SAML/実運用SSO、document/source ACL、connector差分同期、malware scan、append-only retention、production publish/rollback drill、backup/restore、負荷/障害実証、SBOM/NOTICE、正式な96件QA承認、Evidence Inspector、workspace V2。

## 現在の阻害要因

1. **release再現性/runtime/CI**: Python標準 asyncio の cross-thread wakeup 最小再現が停止し、TestClientを使用する全pytestが完走しない。lock/constraints、CI clean-run、モデル・dataset・baseline fingerprintも未確立。
2. **QA内容品質**: 040219e-biscfaq の96件は legacy review-required で、正式な人間承認が未完了。
3. **security/ACL**: 役割分離、secure session、document/source ACL、upload security、audit retentionの運用証拠がない。
4. **publish/rollback/operations**: versioned snapshot、publish gate、rollback、backup/restore、incident drillが未実証。
5. **UI/connector/performance/legal**: 非エンジニア運用画面、同期、性能SLO、デザインprovenance、OSS/商標/意匠確認が未完了。

# 実装ロードマップ

## 第0段階：baselineとrelease再現性（47→53）

- **目的**: clean環境で同じ品質判断を再実行できる状態にする。
- **開始条件**: 現在のQA/fixture境界を凍結する。
- **実装項目**: asyncio runtime比較、全pytestのCI化、constraints/lock、Python/Node/model revision固定、retrieval/dataset fingerprint、正しいnDCG、baseline artifact、SBOM/OSS inventory/release artifact。
- **変更しない対象**: production corpus、ranking挙動、QA内容。
- **test/評価**: clean全pytest、metric regression、hash一致、CI artifact。
- **security/legal gate**: dependency provenance と license inventory を記録。
- **release/rollback**: immutable release artifact と前version復元手順。
- **完了条件**: clean環境で全テスト・評価・artifact hashが再現可能。未達時は第1段階へ進まない。

## 第1段階：非エンジニア向けworkspace V2（53→62）

- **目的**: 既存UIを残したまま `/workspace-v2` を独自設計で追加する。
- **実装項目**: React/TypeScript/Vite shell、streaming adapter、conversation sidebar、answer card、citation chip、Evidence Inspector、feedback/human review request、responsive/a11y/keyboard、dark mode、error boundary、Playwright E2E。
- **変更しない対象**: backend contract、ranking、SSO、production切替。
- **gate**: API contract regression、accessibility/E2E、design provenance。
- **完了条件**: 非エンジニアがcitationを確認しreview依頼できる。未達時は旧UIを維持。

## 第2段階：非エンジニア向け管理workflow（62→70）

- **実装項目**: upload wizard、extraction preview、metadata/duplicate/tenant conflict表示、staging validation/evaluation/publish request、QA review queue/PDF preview、version/rollback/job/audit UI。
- **security/release**: production直接投入禁止、reviewer/approver/publisher職務分離、staging→publish gate、snapshot rollback。
- **完了条件**: 管理者以外でも安全に申請・レビューでき、publishは承認済みsnapshotだけに限定される。

## 第3段階：evidence-ranking-core（70→77）

新規repo `evidence-ranking-core` を作り、chatbotを最初のconsumerとする（本段階では作成しない）。共通責務は Candidate、SourceHit、RankingContext、ObjectiveScore、PolicyDecision、RankedCandidate、source fusion、content fingerprint、deterministic score/tie-break、policy trace、shadow comparison、offline metrics。chatbotは keyword/vector/approved-QA retrieval、feature生成、parent expansion、citation-first回答、guard/abstain/fallback、tenant/ACL/APIを保持する。

- 固定weightから開始し、Qwen/外部APIを必須にしない。approved exact-matchは不変。
- current/candidateをshadow比較し、query単位artifact、tuning/holdout/regression set、hit@k/MRR/正しいnDCG/citation-answer support/contradiction/abstain/identifier-number-date/latency/determinismを評価する。
- title/sectionは初期にはfeature、query expansion/Qwenは後続。
- **完了条件**: 本文末尾の本番切替条件をすべて満たすまでcandidateは回答経路へ入れない。

## 第4段階：enterprise security（77→84）

- OIDCまたはSAML、HttpOnly secure session、CSRF、user/reviewer/approver/publisher/admin、backend-authoritative tenant authorization、document/source ACL、API authorization、upload validation/malware scan、secret manager、account disable、append-only audit/retention、security tests。
- **完了条件**: UI表示だけで権限を与えず、ACL/監査/失効をbackendで実証。

## 第5段階：文書同期と鮮度（84→88）

- 優先順位: manual upload、共有folder、SharePoint/OneDrive、Google Drive、Web/portal、Slack/Teams、GitHub。
- incremental/deletion/ACL sync、version/stale/failed-sync alert、source ownership、last-success、reindex、duplicate source detection。
- **完了条件**: 顧客需要順のconnectorで削除・ACL・鮮度を含む同期が実証済み。

## 第6段階：商用運用実証（88→90～92）

- 10～20人非エンジニアpilot、task completion、初回質問時間、citation確認、誤回答/abstain理解、upload成功、QA review時間、p95/p99、concurrent load、memory上限、backup/restore・rollback・process/vector/model/connector障害試験、incident/support runbook。
- **完了条件**: pilot指標、運用ドリル、security/legal gatesを満たす。未達ならpilot改善を継続し、90点を宣言しない。

# タスク分割

各Taskは1責任・1 commit単位とし、QAレビューとcode、SSOとACL、connectorとranking、dependency更新とrefactor、publishとschemaを混在させない。

| Task | 目的 / 主な変更対象 | 依存 | 並列 | risk | 推奨model | 完了条件 |
| --- | --- | --- | --- | --- | --- | --- |
| T0-01 | branch・production data・fixture境界固定 | なし | 不可 | 中 | terra | hash/status記録 |
| T0-02 | clean Python runtime/全pytest経路 | T0-01 | 不可 | 高 | sol | cross-thread runtime解決と全pytest |
| T0-03 | GitHub Actions全pytest | T0-02 | 不可 | 中 | terra | clean CI evidence |
| T0-04 | constraints/lockとruntime固定 | T0-02 | 可 | 高 | sol | reproducible install |
| T0-05 | metric正当性・evaluation regression | T0-02 | 可 | 高 | sol | nDCG等の証拠 |
| T0-06 | baseline/dataset/model artifact fingerprint | T0-03,T0-04 | 可 | 中 | terra | immutable artifact |
| T1-01 | workspace-v2 backend/frontend contract | T0-03 | 可 | 中 | sol | contract review |
| T1-02 | React/TypeScript/Vite shell/design tokens | T1-01 | 可 | 中 | terra | E2E shell |
| T1-03 | streaming chat adapter | T1-02 | 不可 | 中 | sol | contract/E2E |
| T1-04 | Evidence Inspector | T1-03,T0-05 | 不可 | 高 | sol | citation trace UI |

## 本番切替条件

candidate ranker は、gold hit@5非悪化、MRRまたはnDCG改善、citation support/abstain precision-recall/approved consistency非悪化、contradiction非増加、identifier/number/date preservation非悪化、許容p95、holdout改善、regression成功、rollback実証を全て満たすまで置換しない。

## UI法務gate

会話sidebar、composer、citation/evidence表示等の一般的操作思想は採用できるが、OpenAI/ChatGPT/GPT/Anthropic/Claudeの名称、logo、CSS、HTML、JavaScript、画像、画面文言はコピーしない。蔵伝 / KuraDen は維持候補とし、独自design token、icon/font/component license、UI design provenance、trademark、GUI意匠、誤認表示、第三者assetを記録する。公開前に知財専門家が確認するまで法的安全を断定しない。
