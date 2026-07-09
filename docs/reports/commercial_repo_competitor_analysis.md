# Commercial Repository Analysis

作成日: 2026-06-12
対象リポジトリ: `/home/rai/chatbot`(ブランチ: `eval/real-vector-evidence`)
作成方法: ローカル検査(git・grep・pytest collect・smoke スクリプト・docker compose config)に基づく。外部市場情報はこの実行中にインターネット検証していないため、「not freshly verified(未検証)」として扱う。

---

## 1. Executive Summary

**このリポジトリは何か。**
日本語ドキュメント向けの「引用第一(citation-first)」RAG チャットボット実装である。承認済み Q&A の完全一致回答(LLM 非経由・決定的)、ハイブリッド検索(キーワード+ベクトル)、日本語対応ヒューリスティック・リランク、証拠が弱い場合の正直な「回答不可」フォールバック、FastAPI による API 提供、SSE ストリーミング、回答キャッシュ、テナント分離、Docker/CI によるパッケージングまでを備えた、汎用デモではなく商用志向の単一プロダクト候補である。

**Prompt012 は完了しているか。**
完了している。コミット `b459ec9`(タグ `prompt012-phase4b-deployment-packaging`)に、Prompt012 が要求した成果物がすべて含まれている: `Dockerfile`、`.dockerignore`、`docker-compose.yml`、`.env.example`、`.github/workflows/ci.yml`、README の Docker 節追記、および次プロンプト `prompts/claude/prompt013_phase5a_cross_encoder_rerank.md` の作成。本検査での再検証(テスト収集 578 件成功、smoke 117 件パス、`docker compose config --quiet` 成功)もすべて通った。

**Claude が途中で停止した形跡はあるか。**
Prompt012 の実行自体に中断の形跡はない(成果物完備・コミット済み・未コミットのコード変更なし)。ただし、**本分析プロンプト自体の前回実行が途中で止まった形跡がある**: `docs/reports/` ディレクトリが 2026-06-12 00:18(分析プロンプトファイル `prompts/claude/analysis/` と同時刻)に作成されたまま空で残っていた。つまり前回の分析実行はレポート出力前に停止したと推定される。アプリケーションコードには影響なし。

**現在の商用準備度(1段落)。**
本リポジトリは「強い商用 PoC〜限定ベータ候補の入口」段階にある。承認済み Q&A 完全一致(実 PDF 1 件で 22/22 検証済み)、ガード付き RAG、API 認証、テナント分離、観測性、Docker パッケージングという骨格は揃っており、信頼できる単一テナント PoC を今すぐ実施できる水準である。一方で、API キーとテナントの紐付けが存在しない(クライアントが `tenant_id` を自己申告する)、評価コーパスが極小(検索評価 25 件)、リランクがヒューリスティックのみ、バックアップ・TLS・集約監視・シークレット管理が未整備という理由から、複数顧客への有償提供や本番 SaaS としての販売はまだできない。

---

## 2. Evidence Checked In This Repo

- **リポジトリパス**: `/home/rai/chatbot`(存在確認済み)
- **ブランチ**: `eval/real-vector-evidence`。`main` より 69 コミット先行。ハードニング系列(prompt001〜012)は **main に未マージ**。
- **最新コミット/タグ**(`git log --oneline --decorate -20`):
  - HEAD = `b459ec9` `prompt012-phase4b-deployment-packaging`
  - `prompt001-phase0a-retrieval-corpus-integrity` 〜 `prompt012-phase4b-deployment-packaging` の **12 タグすべて存在**(`git tag --list` で確認)。
- **git status の要約**: コミット済みコードに変更なし。未追跡のみ: `data/`、`pdfs/58887_95105_misc.pdf`、`prompts/claude/analysis/`、`prompts/claude/backlog/`、`prompts/claude/prompt001_...md`(プロンプトファイルのみ未追跡。実装コミットとタグは存在)、`roadmap.md`。
- **Prompt012 成果物(すべて確認済み)**:
  - `Dockerfile`(python:3.12-slim、非 root ユーザー、データ非同梱、uvicorn 起動)
  - `.dockerignore`(.env*、vectorstore/、data/、pdfs/、prompts/ 等を除外)
  - `docker-compose.yml`(vectorstore/index/data/runs のバインドマウント、env_file、/health ヘルスチェック)
  - `.env.example`(プレースホルダのみ。実値なし)
  - `.github/workflows/ci.yml`(smoke + ハードニングテスト + collect-only)
  - `prompts/claude/prompt013_phase5a_cross_encoder_rerank.md`(作成済み・**未実行**。`rag_core/` に cross_encoder モジュールが存在しないことを grep で確認)
- **検証コマンドの結果**:
  - `python -m pytest --collect-only -q` → **578 テスト収集成功**
  - `scripts/product_readiness_smoke.sh` → **117 テストパス + py_compile 成功**
  - `docker compose config --quiet` → **成功**
  - Docker イメージビルド → **スキップ**(プロンプト指示どおり。構文検証のみで十分であり、ビルドは時間がかかるため)
  - `.env` は読んでいない(存在のみ ls で確認)。シークレット・トークンは一切出力していない。

---

## 3. What Has Been Completed Through Prompt012

### Phase 0(prompt001〜003): 検索コーパス整合性・埋め込み一貫性・検索性能

- **変更内容**: コーパス整合性チェック、埋め込みプロバイダのフィンガープリント照合(ingest 時に collection に刻印し、クエリ時に検証)、クエリ埋め込みのバッチ化(`QueryEmbeddingBatch`)、キーワードインデックス状態の可視化(`keyword_index_status`、確認: `rag_core/retrieval.py:70`)。
- **商用上の意味**: 「異なる埋め込みモデルで ingest した vectorstore に対して黙って検索する」という RAG の典型的サイレント障害を防ぐ。検索レイテンシの予測可能性が上がる。
- **残る制約**: 性能検証は小規模ローカルコーパスのみ。大規模コーパスでの latency 実測は未実施(not verified)。

### Phase 1(prompt004〜005): 実証拠ベースのガード・正直な回答不可+引用

- **変更内容**: 生のベクトル距離(`vector_distance`)に基づくハード/ソフト確信度ガード(確認: `rag_core/qa.py:148-184`、`tests/test_confidence_guard.py`)。証拠不足時は推測せず、ガード理由と参照候補を付けて「回答できない」と返す。
- **商用上の意味**: ハルシネーションの商用リスク(誤案内・コンプライアンス)を直接抑える。「答えない」が説明可能であることはエンタープライズ導入の必須条件。
- **残る制約**: 閾値(`RAG_MAX_DISTANCE` 等)は実ベクトルコーパスでのキャリブレーションが未完。ブランチ名 `eval/real-vector-evidence` が示すとおり、実コーパス証拠での調整はまさに次の作業。

### Phase 2(prompt006〜008): API ハードニング・LLM 呼び出しハードニング・SSE ストリーミング

- **変更内容**: opt-in API キー認証(`X-Api-Key` / `Authorization: Bearer`、`webapi/api_auth.py:48-72`)、`/search/debug` の無効化/管理者保護、CORS 許可リスト。LLM 呼び出しのタイムアウト・リトライ・max_tokens 制御(`_create_chat_completion`、`rag_core/qa.py:323`)、プロバイダエラー分類(429 等)。`/chat/stream` SSE(`answer_query_stream`、`rag_core/qa.py:837`、認証必須をテストで担保)。
- **商用上の意味**: 公開エンドポイントとして最低限の防御線。LLM 障害時にハングせず分類されたエラーを返す。ストリーミングは商用チャット UI の体感品質に直結。
- **残る制約**: API キーはフラットな共有リストで、**キー→テナントの権限マッピングなし**。インバウンドのレートリミットなし(grep で確認。429 処理は上流プロバイダ側エラーの分類のみ)。

### Phase 3(prompt009〜010): 正規化回答キャッシュ・運用観測性

- **変更内容**: 正規化質問+tenant_id+パラメータをキーとする opt-in LRU 回答キャッシュ(`rag_core/answer_cache.py:49`)。`/metrics` エンドポイント(回答モード・ガード理由・フォールバック・キャッシュヒットのカウンタ、`webapi/metrics_registry`)、`stage_latency_ms`(retrieval_ms / generation_ms)のトレース。
- **商用上の意味**: 同一 FAQ 質問の LLM コスト削減。ガード発火率・フォールバック率は RAG 品質の運用 KPI としてそのまま使える。
- **残る制約**: カウンタは**プロセス内のみ**(複数 worker で分断、再起動で消失)。Prometheus 等への exporter なし。キャッシュもプロセス内のみで永続化なし。

### Phase 4(prompt011〜012): テナント分離・デプロイパッケージング

- **変更内容**: 検索(`rag_core/retrieval.py:83-108` の `normalize_tenant_id` / `_tenant_matches` / where 句)、回答キャッシュキー、承認済み Q&A インデックス、テナントプロファイル(`rag_core/tenant_profile.py`、パストラバーサル拒否テストあり)までテナント ID を貫通(tenant_id 参照 304 箇所、`tests/test_tenant_isolation.py` で分離を担保)。Docker/compose/.env.example/CI 一式(§2 参照)。
- **商用上の意味**: 複数顧客のナレッジを 1 デプロイで分離して扱う土台。Docker 化により顧客環境・VPS への再現可能な配備が可能になった。
- **残る制約**: **`tenant_id` はリクエストボディの自己申告値**(`webapi/main.py:115-169` で Optional フィールド、認証層との紐付けなし)。正しい API キーを持つ呼び出し側は任意のテナントを指定できる。これは複数テナント商用提供の最大のブロッカー。単一 Chroma collection のメタデータフィルタ方式であり、物理分離・クォータもない。

---

## 4. Current Commercial Readiness

採点は厳格に行った。過大評価しない。

| 項目 | スコア (0-100) | 理由 |
|---|---|---|
| Commercial PoC readiness | **70** | 承認 Q&A 22/22 実 PDF 検証、ガード付き RAG、Docker、578 テスト。単一テナント・社内/クローズド PoC なら今すぐ可能。減点は実コーパス評価の薄さ。 |
| Limited beta readiness | **45** | 認証・キャッシュ・観測・パッケージングは揃うが、キー→テナント紐付けなし、TLS/リバースプロキシなし、バックアップなしで外部ユーザーには出せない。 |
| Large-scale SaaS production readiness | **15** | プロセス内メトリクス/キャッシュ、単一コンテナ、レートリミットなし、シークレット管理なし、水平スケール設計なし。SaaS 本番は遠い。 |
| RAG trustworthiness | **55** | 実ベクトル距離ガード+正直な回答不可+引用第一は demo 水準を大きく超える。ただし閾値の実コーパス校正が未完で、信頼性の「証明」がまだない。 |
| API security baseline | **50** | opt-in キー認証・admin 認証・debug 遮断・CORS 許可リストは実装/テスト済み。共有キー方式・認可マッピングなし・レートリミットなしで 50 止まり。 |
| Operations/observability | **40** | /metrics・ガード理由カウンタ・段階レイテンシは良い出発点。だがプロセス内限定・エクスポートなし・アラートなし・ログ保持ポリシーなし。 |
| Deployment readiness | **45** | Dockerfile(非 root・データ非同梱)・compose・healthcheck・CI は正しく作られている。TLS・本番プロキシ・マウント済み実データでの本番 smoke・ロールバック手順が未。 |
| Multi-tenant readiness | **35** | データ面の分離(検索・キャッシュ・承認 QA)はテスト付きで実装済み。しかし認可面(キー→tenant)が存在しないため、現状は「敵対的でないテナント間の分離」に留まる。 |
| Accuracy readiness | **35** | ヒューリスティック・リランクのみ(セマンティック・リランクなし)、評価コーパス極小(retrieval 25 件・smoke 21 件・approved QA サンプル 3 件)、実コーパスは PDF 1 冊(104 チャンク・22 Q&A)。精度を主張できる証拠がまだ薄い。 |

---

## 5. Similar Services And Positioning

外部製品に関する記述は安定したカテゴリ特性に基づく。市場の最新動向はこの実行中に未検証(not freshly verified)。

### Dify / Flowise / Langflow / AnythingLLM(OSS の RAG/チャットボットプラットフォーム)

- **何か**: ノーコード/ローコードで RAG パイプラインとチャット UI を組めるセルフホスト可能なプラットフォーム。
- **類似点**: セルフホスト RAG、ドキュメント ingest、API 提供、マルチテナント志向(Dify)。
- **本リポジトリが弱い点**: UI が事実上ない(管理レビュー画面程度)、コネクタ/データソースの種類、コミュニティ・エコシステム、導入の手軽さ。
- **差別化できる点**: 日本語特化(日本語チャンキング・かな/カナ/漢字・表形式 Q&A PDF 抽出)、承認済み Q&A の決定的回答という「回答ガバナンス」、実証拠距離に基づくガードと正直な回答不可。汎用プラットフォームはここまで回答統制を作り込んでいない。
- **競合方針**: 直接競合すべきでない。「日本語・高統制・引用必須」の縦特化カスタム実装として位置取る。

### Intercom Fin / Zendesk AI / Freshworks Freddy AI(商用サポートボット SaaS)

- **何か**: ヘルプデスク製品に組み込まれたフルマネージド AI 回答サービス。課金・SLA・UI・チケット連携込み。
- **類似点**: 「サポート質問に根拠付きで答える」というユースケースは同一。
- **本リポジトリが弱い点**: 製品成熟度のほぼ全部 — UI、チケット連携、課金、SLA、運用体制、多言語、スケール。
- **差別化できる点**: データを顧客環境内に保てる(フル・セルフホスト)、承認回答の決定性(SaaS ボットは生成のたびに揺れうる)、回答経路の完全な検査可能性、日本語企業文書への最適化。
- **競合方針**: 直接競合は不可能かつ不要。「SaaS にデータを出せない日本企業向けのプライベート RAG 構築」として補完的に位置取る。

### LangChain / LlamaIndex / Haystack(開発者向け RAG フレームワーク)

- **何か**: RAG アプリを組むためのライブラリ/フレームワーク。製品ではなく部品。
- **類似点**: 本リポジトリも retrieval→rerank→generation のパイプラインを自前実装しており、レイヤーは同じ。
- **本リポジトリが弱い点**: 汎用性・コネクタ数・抽象化・ドキュメント・コミュニティ。フレームワークとしては再利用しにくい。
- **差別化できる点**: フレームワークが提供しない「完成した意見」を持つ — 承認 QA ルーティング、ガード方針、評価ゲート、運用チェックリストまで一体。依存が薄く(requirements.txt は小さい)監査しやすい。
- **競合方針**: 競合ではなく対照。本リポジトリは「フレームワークを使わずに統制を作り込んだ縦型実装」であり、それ自体がセールスポイントになる。

### AWS Bedrock Knowledge Bases / Azure AI Search + Azure OpenAI / Google Vertex AI Search(クラウドマネージド RAG)

- **何か**: クラウドが ingest・ベクトル化・検索・生成をマネージドで提供する RAG 基盤。
- **類似点**: 提供機能(ingest→検索→引用付き回答)はほぼ同じ守備範囲。
- **本リポジトリが弱い点**: スケーラビリティ、可用性、セキュリティ認証(SOC2 等)、運用コスト、ベクトル DB の堅牢性 — マネージドの土俵では全面的に劣る。
- **差別化できる点**: クラウド外/オンプレ/閉域要件への対応、日本語チャンキングと表形式 Q&A 抽出の質、承認回答の決定性、ベンダーロックイン回避。
- **競合方針**: 競合しない。閉域・データ主権・日本語品質が要件の案件で「マネージドが使えない/不十分な場合の選択肢」として位置取る。

**総合ポジショニング**: 本リポジトリは「プラットフォーム製品」として売るのではなく、**日本語企業向けプライベート RAG の受託/テンプレート実装**(高統制・引用必須・承認回答ガバナンス込み)として商用化するのが現実的である。

---

## 6. Competitive Comparison Table

| Product/category | Main strength | This repo advantage | This repo weakness | Commercial implication |
|---|---|---|---|---|
| Dify | ノーコード UI と運用機能一式 | 承認 QA 決定的回答・距離ガード・日本語表 Q&A 抽出 | UI なし・エコシステムなし | プラットフォームでなく縦特化実装として売る |
| Flowise | ビジュアルフロー構築の手軽さ | 回答統制とテスト網(578 件)の深さ | 構築の柔軟性・コネクタ不足 | PoC スピードでは負ける。統制要件で勝つ |
| Langflow | フロー実験の自由度 | 本番志向のガード/監査/評価ゲート | 実験 UI なし | 「実験後の本番実装」需要を取る |
| AnythingLLM | ローカル完結の簡単 RAG | テナント分離・API ハードニング・CI | デスクトップ的手軽さなし | 個人/小規模では不利。組織導入で優位 |
| PrivateGPT 系 | 完全オフライン動作 | 運用 API・観測性・承認 QA ワークフロー | オフライン LLM 同梱なし(OpenAI 依存が既定) | 閉域案件ではローカル LLM 対応が課題 |
| Intercom Fin | 完成 SaaS・チケット連携・SLA | セルフホスト・データ主権・回答決定性 | 製品成熟度すべて | 競合不可。SaaS 不可案件の受け皿 |
| Zendesk AI | ヘルプデスク市場での既存基盤 | 日本語文書特化・検査可能性 | UI/連携/運用体制なし | 同上。共存前提 |
| Freshworks Freddy AI | 中小向け価格と統合 | カスタマイズ自由度・透明性 | 即日導入性なし | 「カスタム要件あり」の案件のみ狙う |
| LangChain/LlamaIndex/Haystack | 汎用部品・コミュニティ | 完成した統制方針・薄い依存・監査容易 | 汎用性・再利用性 | フレームワーク非依存を監査性として訴求 |
| AWS Bedrock KB | フルマネージド・スケール | 閉域対応・日本語チャンキング品質・ロックイン回避 | 可用性・スケール・認証取得 | マネージド不可案件専用 |
| Azure AI Search + OpenAI | エンタープライズ統合(Entra 等) | 同上+承認回答ガバナンス | セキュリティ認証・運用 | 同上 |
| Vertex AI Search | 検索品質とマネージド運用 | 同上 | 同上 | 同上 |

---

## 7. What Is Still Missing Before Real Commercial Use

### Must-have before paid customer deployment(有償顧客デプロイ前に必須)

1. **API キー → tenant_id 認可マッピング**: 現状 `API_AUTH_KEYS` はフラットな共有リストで、`tenant_id` はリクエストボディの自己申告(`webapi/main.py:115-169`)。キーごとに許可テナントを定義し、サーバ側で強制すること。これがないと複数テナント有償提供は不可。
2. **実コーパス評価**: 現在の検証済み実データは PDF 1 冊(104 チャンク・22 承認 Q&A)。顧客想定の実文書セットでの retrieval/guard/answer 評価が必須。
3. **実ベクトルコーパスでのガード閾値キャリブレーション**: `RAG_MAX_DISTANCE` 等は実コーパス分布で校正されていない。ブランチ名どおり、この作業(real-vector evidence)を完了させる。
4. **シークレット管理**: `.env` ファイル直置きが前提。顧客環境では最低限、ファイル権限方針とローテーション手順、可能なら secrets store(SOPS/Vault/クラウド KMS 等)の手順書が必要。
5. **バックアップ / リストア**: vectorstore・approved_qa JSONL・監査ログの定期バックアップと復元手順が存在しない。顧客データを預かる以上、契約前に必須。
6. **本番リバースプロキシ / TLS**: uvicorn 直公開しかない。nginx/caddy/Traefik 等での TLS 終端・タイムアウト・ボディサイズ制限の参照構成を用意する。
7. **テナントオンボーディングのデータガバナンス**: テナント追加時の ingest 手順、削除(退去)時のデータ消去手順、誤テナント ingest の検出と是正の手順が未定義。

### Should-have before broader beta(広いベータ前に必要)

1. **クロスエンコーダ・リランク(Prompt013)**: 現リランクはヒューリスティックのみ。プロファイルゲート付きセマンティック・リランクで精度の頭打ちを解消する(prompt013 ファイル作成済み・未実行)。
2. **Q+A ペアチャンク**: 表形式 Q&A の Q と A を 1 チャンクに結合して索引化する(roadmap/README に方針記載済み・未実装)。表型文書の検索精度に直結。
3. **評価コーパス拡充**: retrieval 25 件・abstain ラベル含む smoke 21 件は回帰検知には足りるが品質主張には不足。実文書由来で 100 件以上規模へ。
4. **集約監視**: `/metrics` はプロセス内カウンタのみ。Prometheus exporter ないし JSONL メトリクスの集約と、ガード発火率・フォールバック率のアラート閾値。
5. **ログ保持ポリシー**: 監査 JSONL のローテーション・保持期間・個人情報の扱いを明文化。
6. **インバウンド・レートリミット**: 現状なし(確認済み)。キー単位のレート制御。
7. **マウント済み実データでの本番デプロイ smoke**: compose 起動 → 実 vectorstore マウント → /health・/chat・/metrics の end-to-end 確認を手順化(イメージビルドはこの分析では未実施)。

### Nice-to-have after accuracy improves(精度向上後の追加価値)

1. 類似質問の承認 Q&A 候補提示の自動回答昇格(現在は候補のみ、設計方針どおり)
2. 簡易チャット UI / 埋め込みウィジェット
3. ローカル LLM(閉域案件向け)対応
4. キャッシュ・メトリクスの外部ストア化(Redis 等)による水平スケール
5. Kubernetes マニフェスト / Helm
6. フィードバック由来リランクプロファイルの本番昇格ワークフロー自動化

---

## 8. Recommended Roadmap From Here

リポジトリの証拠(ブランチ名 `eval/real-vector-evidence`、prompt013 作成済み、tenant_id 自己申告の構造)を踏まえ、プロンプト指定の順序をほぼ踏襲しつつ、**実ベクトル校正を 3 番目に前倒し**する(現ブランチの作業文脈と連続しており、Q+A ペアチャンク導入はコーパス分布を変えるため、校正→拡充の往復を 1 回で済ませる狙い。なお厳密には rerank 評価も校正済みガードの上で行うのが理想だが、prompt013 はプロファイルゲート付きで既定無効のため先行実装してよい)。

### Step 1: Prompt013 クロスエンコーダ・リランク実装
- Goal: プロファイルゲート付き・既定オフのセマンティック・リランク段を追加(`prompts/claude/prompt013_phase5a_cross_encoder_rerank.md` をそのまま実行)。
- Why now: 精度トラックの最初の一手として既にプロンプト化済み。既定オフなので本番挙動を変えず安全。
- Exact expected output: `rag_core/cross_encoder_reranker.py`、config 3 knob、eval runner の `hybrid_rerank_ce` モード、フェイクモデルでのテスト一式。
- Verification: 新規テスト+`pytest --collect-only`+smoke スクリプトが従来どおりパス。既定設定で挙動不変をテストで担保。
- Risk: sentence-transformers の optional import 周りの環境差。フェイクモデルテストで吸収する。

### Step 2: Q+A ペアチャンク
- Goal: 承認 Q&A レコードを「Q+A を 1 チャンク」として canonical JSONL 化し ingest する経路を追加。
- Why now: 表形式 Q&A PDF が現実の主データであり、Q と A の分断が検索精度の既知の上限要因(README に明記)。
- Exact expected output: 変換スクリプト+チャンク種別メタデータ(`doc_type=qa_pair` 等)+ingest 検証+retrieval 評価ケース追加。
- Verification: 既存 22 Q&A で「ペアチャンクが該当質問の top 候補に入る」ことを eval runner で計測。
- Risk: 既存チャンクとの重複・二重ヒット。dedup とメタデータでの出自区別が必要。

### Step 3: 実ベクトルコーパスでのガード閾値キャリブレーション
- Goal: 実コーパス(Step 2 反映後)の距離分布を計測し、`RAG_MAX_DISTANCE` 等のハード/ソフト閾値を根拠付きで設定。
- Why now: 現ブランチ `eval/real-vector-evidence` の本来の作業。閾値が未校正のままでは「正直な回答不可」の信頼性を主張できない。
- Exact expected output: 距離分布レポート(answerable vs abstain ケース別)+推奨閾値+設定変更+abstain 評価ケースでの誤答/誤拒否率。
- Verification: abstain ラベル付きケースで false-answer 率と false-abstain 率を計測し、変更前後を比較。
- Risk: コーパスが小さいうちは分布が不安定。Step 4 と往復が必要になりうる。

### Step 4: 評価コーパス拡充
- Goal: 実文書由来の retrieval/abstain/approved-QA 評価ケースを 100 件規模へ拡充。
- Why now: Step 1〜3 の効果測定も、商用の精度主張も、25 件では統計的に語れない。
- Exact expected output: `eval/cases/` への追加 JSONL(gold doc/chunk ラベル付き)+mode 別サマリの基準値更新。
- Verification: eval runner の全モード実行が完走し、ベースライン数値が記録される。
- Risk: ラベル作成の人手コスト。Q&A PDF 由来の半自動生成で軽減。

### Step 5: API キー → テナント認可マッピング
- Goal: キーごとに許可 tenant_id を定義し、リクエストの自己申告 tenant_id をサーバ側で検証・強制する。
- Why now: 複数テナント有償提供の唯一最大のセキュリティブロッカー。精度トラックと独立に進められる。
- Exact expected output: キー→テナント設定(env または設定ファイル)+`require_api_auth` 拡張+不一致時 403+`tests/test_api_auth.py`/`test_tenant_isolation.py` への敵対ケース追加。
- Verification: 「テナント A のキーでテナント B を指定すると 403」「キャッシュ・承認 QA・検索のいずれも越境しない」をテストで担保。
- Risk: 既存のシングルテナント利用(tenant_id 省略)との後方互換。default テナント許可の明示で解決。

### Step 6: マウント済み実データでの本番デプロイ smoke
- Goal: `docker build` → compose 起動(実 vectorstore/approved_qa マウント、API 認証有効)→ /health・/chat・/chat/stream・/metrics の end-to-end 確認を手順化・自動化。
- Why now: パッケージングは構文検証まで(本分析でもビルドはスキップ)。実コンテナでの動作証明がないと「デプロイ可能」と言えない。
- Exact expected output: `scripts/deploy_smoke.sh`(起動→curl 検証→停止)+README 手順+結果記録。
- Verification: スクリプトがクリーン環境で exit 0。approved Q&A 22/22 がコンテナ経由でも再現。
- Risk: ローカル埋め込みモデルのダウンロードがイメージ外で必要。手順に明記する。

### Step 7: バックアップ / リストア
- Goal: vectorstore・`data/approved_qa/`・監査ログ・設定の定期バックアップと復元の手順+スクリプト。
- Why now: 顧客データを預かる前の契約上の必須要件。Step 6 のボリューム構成が固まってから着手するのが効率的。
- Exact expected output: `scripts/backup.sh` / `scripts/restore.sh`+復元後の整合性検証(embedding フィンガープリント照合を再利用)+ドキュメント。
- Verification: バックアップ→空環境へ復元→smoke 一式パス、を実演。
- Risk: Chroma の整合性(コピー中の書き込み)。停止スナップショット方式を既定にする。

### Step 8: 監視集約 / エクスポータ
- Goal: プロセス内カウンタの限界を超える。Prometheus 形式エンドポイントまたはメトリクス JSONL の定期書き出し+ガード発火率・フォールバック率・エラー率の閾値アラート定義。
- Why now: ベータ運用で「品質劣化に気づける」体制が必要。Step 1〜4 で得た KPI(ガード理由分布)をそのまま監視項目にできる。
- Exact expected output: exporter 実装(依存追加が必要なら最小限)+アラート閾値ドキュメント+複数 worker 時の集約方針。
- Verification: メトリクスがプロセス再起動をまたいで集約可能であることを確認。
- Risk: 依存追加の重さ。まず JSONL 書き出し+外部集約の軽量案から始める。

(Step 9 以降の候補: ログ保持ポリシー策定、リバースプロキシ/TLS 参照構成、レートリミット、テナントオンボーディング/オフボーディング手順 — §7 Must-have の残件をベータ開始前に消化する。)

---

## 9. Final Judgment

- **位置づけ**: **商用 PoC(強)〜限定ベータ候補の入口**。toy/demo は明確に超えている(ガード・認証・テナント分離・観測・パッケージング・578 テスト)。production SaaS ではない。
- **今売れるもの**: (1) 単一顧客・閉域/社内向けの「日本語文書 RAG + 承認 Q&A 決定的回答」**PoC/受託構築**。(2) 表形式 Q&A PDF → 承認 Q&A 化のワークフロー(22/22 実検証済み)。いずれも「ベストエフォート・精度保証なし・運用は提供側同伴」の条件付きで。
- **まだ売ってはいけないもの**: 複数テナント相乗りの有償サービス(キー→テナント認可がないため)。無人運用前提のサービス(バックアップ・監視集約・TLS がないため)。精度を数値保証する契約(評価コーパスが小さすぎるため)。
- **最も価値ある次のプロンプト**: **Prompt013(クロスエンコーダ・リランク)を実行する**。既に作成・コミット済みで、既定オフのため安全に進められ、精度トラック(rerank → Q+A ペア → 評価拡充 → ガード校正)の起点になる。並行して、セキュリティ面の最重要課題として「API キー → テナント認可マッピング」を Prompt014 として起案すべきである。

---

## 10. Appendix: Commands Run

| コマンド | 結果要約 |
|---|---|
| `pwd` | `/home/rai/chatbot` |
| `git branch --show-current` | `eval/real-vector-evidence` |
| `git log --oneline --decorate -20` | HEAD=`b459ec9`(prompt012 タグ)。prompt001〜012 が連続コミット。 |
| `git status --short` | コード変更なし。未追跡: data/、pdfs/、prompts/claude/analysis/・backlog/・prompt001 md、roadmap.md |
| `git tag --list \| grep -E "prompt..."` | prompt001〜prompt012 の 12 タグすべて存在 |
| `git rev-list --count main..HEAD` | 69(ハードニング系列は main 未マージ) |
| `ls -la`(ルート、Docker 系、CI、prompt013) | Dockerfile/.dockerignore/docker-compose.yml/.env.example/ci.yml/prompt013 md すべて存在 |
| `git show --stat prompt012-phase4b-deployment-packaging` | 8 ファイル・275 行追加(パッケージング一式+prompt013 md) |
| `grep -R "tenant_id" rag_core webapi scripts tests` | 304 箇所。retrieval where 句・キャッシュキー・承認 QA・プロファイルに貫通 |
| `grep -R "stage_latency_ms\|metrics_registry\|answer_cache\|answer_query_stream\|require_api_auth\|_create_chat_completion\|vector_distance\|keyword_index_status"` | 全機能の実装+対応テストを確認 |
| `sed docs/production_readiness_checklist.md` | smoke/運用チェックリスト確認(本番承認そのものではないと明記あり) |
| `sed README.md` | citation-first 方針、22/22 検証実績、レイヤード回答ルーティングを確認 |
| `.venv/bin/python -m pytest --collect-only -q` | **578 テスト収集成功** |
| `bash scripts/product_readiness_smoke.sh` | **117 テストパス + py_compile 成功** |
| `docker compose config --quiet` | **成功**(構文有効) |
| Docker イメージビルド | **スキップ** — プロンプト指示(必要時のみ)に従い、構文検証で十分なため |
| `grep cross_encoder rag_core/ webapi/ config.py` | ヒットなし → Prompt013 未実行を確認 |
| `grep -i "rate.limit" webapi/ rag_core/` | インバウンド・レートリミットなし(上流 429 の分類処理のみ) |
| `ls eval/cases/`+行数 | retrieval 25 / smoke 21 / approved QA 3 件 — 評価コーパス極小を確認 |
| `ls docs/reports/` | 空ディレクトリ(2026-06-12 00:18 作成)— 前回分析実行の中断痕跡 |

注: `.env` は読み取り・出力していない。シークレット・トークン・実キーは本レポートに含まれない。
