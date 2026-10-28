# Phase 3 — 日本語検索精度・比較評価の強化

## objective
既存Dense、BM25、Weighted RRF、DBSF、Cross-Encoder Rerankerを再利用し、日本語RAGの検索精度を同条件で再現可能に比較する。実測で改善・安全性を確認した構成のみ採用候補にする。商用プロダクト基準。

## repository
`rai0409/chatbot`。Phase 2が検証済みでmainに反映された事実を確認し、mainのclean cloneから実施。

## base / branch / HEAD
repo root、remote、branch/HEAD、upstream、base、dirty/untracked、AGENTS.md、関連契約、Phase 2の反映を実測確認。条件を満たすときだけ`feat/chatbot-phase3-retrieval-quality`を新規作成。baseを推測しない。

## 現状
製品コードにDense/BM25/Hybrid/RRF/DBSF/Rerankerと`eval/bench`が存在。別ラボのJaGovFaqs（22,794文書・原文100問）実測はANN Hit@1=55%、Hit@10=86%、Recall@20=92%。Exactでgold順位が改善したのは1問だけ。この結果を製品の本番回答精度や未使用test精度として扱わない。

## 非対象
本番Embeddingモデル変更、実Qdrant/索引書換え、別DBのproduction導入、外部LLM・大規模ベンチマークの自動実行、契約/メール機能、公開API破壊、LICENSE変更。

## 変更可能path
`rag_core/retrieval.py`、`rag_core/bm25_index.py`、`rag_core/cross_encoder_reranker.py`、`rag_core/keyword_scorer.py`、`rag_core/query_preprocess.py`、`rag_core/ja_tokenizer.py`、`eval/bench/`配下、`eval/runner.py`、`eval/rerank_promotion_gate.py`、`eval/approved_similar_decision_eval.py`、`tests/test_hybrid_fusion.py`、`tests/test_bench_metrics.py`、`tests/test_bench_pipeline_parts.py`、`tests/test_bench_compare_runs.py`、`tests/test_bench_stats.py`、`tests/test_stage5_accuracy.py`、`tests/test_normalization_golden.py`、`tests/test_retrieval_quality_regression.py`（新規可）。必須修正pathが不許可の場合はBLOCKED。

## 禁止操作
reset/restore/clean/stash/rebase、既存差分の破棄、main直接commit/push、稼働中サービス停止/再起動、本番索引変更、実ユーザー情報送信、dependency/secret/visibility/license変更、merge/tag、force-push。

## 実装要件
1. **必須要件→変更path→既存test/fixture/consumer→変更許可→検証方法→両立可否**を実コードで照合し、`.phase_work/phase3/`（リポジトリ内・追跡対象外）へ作業前差分保存。検索API・評価CLI・JSON key・固定集合・固定path・テストassertionをrepo全体からexact検索。
2. 実装済みDense/BM25/Weighted RRF/DBSF/Rerankerの処理経路とproduction設定を確認し、同じcorpus hash・質問/gold集合・Embedding Profile・index generationで比較可能にする。
3. Hit@1/3/5/10、Recall@20/50、MRR@10、nDCG@10、候補取りこぼし、p50/p95 latency、改善/悪化した質問IDのペア比較を記録する。
4. 日本語の固有名詞・年度・金額・否定・条件違い・類似FAQを観測する。既存のSudachi形態素解析＋文字バイグラム（`KEYWORD_TOKENIZER`の`auto`/`sudachi`/`heuristic`）を確認して利用し、方式間の比較対象に含める。
5. テナントフィルタと同点順位の決定性、引用元の整合を維持する。Rerankerは候補にないgoldを救えないという制限を明記する。
6. 既知のJaGovFaqs test100問は探索専用。ハイパーパラメータをそのデータで選んだ結果を未使用の正式testに転用しない。未使用の評価集合を別に保持できる契約を作る。
7. モデルサイズ/次元やVector DBの選択をハードコードせずPhase 2のProfile/Generationを使う。
8. 大規模の実モデル比較はWSL直接実行コマンドとして生成するだけで自動実行しない。結果未実行なら精度改善をPASSと宣言せずUNEXECUTEDと記録。

## 既存互換
既存`/chat`、`/search`のレスポンス、tenant制約、Canonical Chunk、出典/引用情報、評価CLIと結果JSON構造を維持。テストを通すための閾値緩和やassertion削除を禁止。

## test
同一fixtureでDense・BM25・RRF・DBSF・Rerankerの決定性・結果指標・同点処理をテスト。tenant別結果、期間/否定条件の回帰、JSON schema互換も対象。commit後に狭いclean rerun→関連全体test。実benchmarkを実行していない場合は指標を未実行とする。

## runtime
外部LLM、実Embedding、GPU benchmark、22,794文書再Embedding、実Qdrant変更は実行しない。小規模ローカルfixtureのみ。

## artifact
`.phase_work/phase3/`にbaseline・比較設定・本番環境へ影響しない直接実行コマンド・テスト結果・未実行項目を保存し読戻す。

## commit / push / PR / tag 権限
**条件付き許可**：必要なunit/fixtureテストPASSで`feat/chatbot-phase3-retrieval-quality`に対象変更だけcommitし通常push可。実benchmark未実行ならPRはdraftで作成可。実測採用gateがPASSするまでdefault設定を昇格しない。merge/tag/force-push禁止。

## 完了条件
既存の検索機能と公開契約を壊さず、方式比較を再現でき、未実測の精度向上を誤認しないこと。実データの改善可否は実benchmark実行後に判定する。

## 最終報告
RESULT、branch/HEAD、変更path、test分類、実測済み/未実測指標、artifact、commit/push/PR実施状況と残存リスクを簡潔に報告。
