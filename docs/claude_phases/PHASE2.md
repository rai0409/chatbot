# Phase 2 — Embedding / Vector DB交換・索引世代管理

## objective
Ruri/Qdrantに固定せず、既存EmbeddingProviderとVector Store抽象化を利用して、Embedding Profile、正本チャンク、索引世代、互換性検証、旧世代への安全な切替を整備する。商用プロダクト基準。

## repository
`rai0409/chatbot`。既存のPhase 1がmainへ検証済みで取り込まれた**事実**をGitHub/ローカル両方で確認し、最新mainのclean cloneで実行する。

## base / branch / HEAD
repo root、remote、branch、HEAD、upstream、base、AGENTS.md、dirty/untrackedを実測で確認。Phase 1の主要変更がmainに含まれていない場合はBLOCKED。clean mainから`feat/chatbot-phase2-pluggable-index`を新規作成。origin/mainを確認なしに正しいbaseと仮定しない。

## 現状
既存`rag_core/embedding_provider.py`、`rag_core/embedding_fingerprint.py`、`rag_core/store.py`、`rag_core/vector_backend.py`にモデルとDBの切替がある。fingerprint不明の旧索引を許容する経路、新旧索引の厳格な検証・切替手順の不足が疑われる。実コードと既存manifest/consumerを再照合。

## 非対象
別Embeddingモデルの実ロード・再Embedding、本番索引構築/削除/切替、外部有料API、検索順位最適化、契約・メールの業務schema追加、既存文書の破壊的移行。

## 変更可能path
`rag_core/embedding_provider.py`、`rag_core/embedding_fingerprint.py`、`rag_core/store.py`、`rag_core/vector_backend.py`、`rag_core/retrieval.py`、`rag_core/index_manifest.py`（新規可）、`rag_core/index_generation.py`（新規可）、`config.py`、`scripts/build_canonical_index.py`、`scripts/check_index_consistency.py`、`scripts/migrate_chroma_to_qdrant.py`、`scripts/compare_index_generations.py`（新規可）、`tests/test_embedding_fingerprint.py`、`tests/test_embedding_fingerprint_metadata.py`、`tests/test_embedding_prefix.py`、`tests/test_embedding_dependency_contract.py`、`tests/test_staging_collection_query_selection.py`、`tests/test_stage10_additions.py`、`tests/test_index_manifest.py`（新規可）、`tests/test_index_generation.py`（新規可）。他は読み取りのみ。必須のconsumer/schema/test変更が別pathならBLOCKED。

## 禁止操作
reset/restore/clean/stash/rebase、無関係差分破棄、main直接commit/push、Docker/WSL/serviceの停止・再起動、本番DB/既存索引の変更、dependencyとLICENSEの無断更新、secret/visibility変更、merge/tag、force push。

## 実装要件
1. **必須要件→変更path→既存test/fixture/consumer→変更許可→検証方法→両立可否**を先に照合。schema・JSON key・CLI・固定パス・固定集合・API参照をrepo全体でexact検索。/tmpへ作業前diffを保存。
2. 既存Provider/Backendに追加する最小の共通契約だけ実装し、余分な抽象層を作らない。文書/チャンクの正式IDはbackend point IDと分離。
3. Embedding Profile: provider/model ID/revision/dimension/query-prefix/document-prefix/normalization/distanceを記録。未宣言・不一致は厳格モードで拒否。
4. Index Manifest: source hash、chunking schema/version、Embedding Profile fingerprint、backend、collection、generation、point count、検証状態、権限metadata要件を記録。既存fingerprintと互換の読込/出力を提供。
5. 旧索引は既存動作を維持し、厳格モードで「fingerprint不明」を拒否できるようにする。本番動作のデフォルト変更は行わない。
6. 既存世代を上書きせず、新世代を独立に準備→全件/metadata整合確認→選択する契約を作る。失敗時には旧世代を引き続き選択可能にする。
7. Vector DBの移設とEmbeddingモデル交換を明確に分離し、モデル変更は再Embeddingが必要であることを機械検証可能にする。
8. テナント/アクセス権/引用の共通IDを索引世代変更で失わない。各provider/DB固有機能は実装層に閉じる。

## 既存互換
公開API、CLI、既存collection、Canonical JSONL、tenant filter、チャンクID/出典ID、結果キー、既存fingerprintを維持。必要なschema変更はバージョン付きで後方互換とし、固定assertionを弱めない。

## test
mockまたは完全に隔離した小規模fixtureで、Ruri Profile、別Profile、異なるdimension/prefix/revision、fingerprintなし旧索引、厳格モードでの拒否、Chroma/Qdrant両経路、世代の衝突防止、不正世代の選択拒否、rollback可能性、tenant/引用維持をテスト。commit後に狭いclean rerun→関連全体test。passed/failed/skipped/blocked/flaky/unexecutedを分ける。

## runtime
大規模索引作成、実Embeddingロード、外部API、本番切替は一切実行しない。既存サービスを変更しない。

## artifact
/tmpに新旧索引プロファイルの例、互換性判定、テスト実測結果、手動切替手順を保存し読戻す。

## commit / push / PR / tag 権限
**条件付き許可**：必要な関連検証PASS時のみ`feat/chatbot-phase2-pluggable-index`へ対象変更をcommit・通常push、main向けPR作成可。CI未確定ならdraft。merge/tag/force-push禁止。

## 完了条件
小規模検証で異なるEmbedding Profile/Backend/Generationを安全に区別でき、既存検索の後方互換を保持。実モデル比較はUNEXECUTEDとして明記。

## 最終報告
RESULT、branch/HEAD、変更path、test分類、artifact、commit/push/PR実施状況、残存制約のみ。長いdiff不要。
