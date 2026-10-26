# Phase 1 — Qdrant CRUD・テナント安全性

## objective
既存ChatbotのQdrant adapterと呼び出し元のCRUD・フィルタ・IDの安全性を修正する。既存Chroma経路とAPIを維持し、異なるtenantの取得・削除・上書きを防止する。商用プロダクト基準。

## repository
`rai0409/chatbot`。ローカル実作業ディレクトリは`git rev-parse --show-toplevel`で確認する。対象は`feat/chatbot-v3-patch-20261022`のCI修正済みコミットを含むローカルコピー。

## base / branch / HEAD
開始時にrepository root、remote URL、HEAD、branch、upstream、tracked/untracked差分、base候補、AGENTS.mdおよび関連契約を確認する。現地の状態を正本とし、古いHEADを固定値として扱わない。基準は`feat/chatbot-v3-patch-20261022`（checkpoint＋CI修正）の実HEAD。初回は当該ブランチのclean cloneから新しい`feat/chatbot-phase1-qdrant-safety`を作成する。mainが正しいbaseとは仮定せず、PR targetはbaseの差分を確認して選択する。既存の無関係なdirty/untrackedがある場合はbranch切替を行わずBLOCKED。

## 現状（監査時点の事実と要再確認事項）
`rag_core/vector_backend.py`で`get(ids,where)`と`delete(ids,where)`がwhereを無視、upsertが長さ不一致を検出せず、get offsetとmetadata-only updateも非互換。`scripts/approved_qa_to_canonical_jsonl.py`は`qa_id`由来IDでtenant間衝突の可能性。`webapi/collection_admin.py`、`scripts/ingest_canonical_jsonl.py`には取得後にフィルタなしで削除する経路がある。変更前に実コードで再現・照合する。

## 非対象
Embedding/Ruri、Vector DB交換、検索ランキング、BM25/Rerankerの調整、契約/メール機能、公開API全面変更、既存実索引の移行・再作成、LICENSE変更。

## 変更可能path
`rag_core/vector_backend.py`、`rag_core/store.py`、`rag_core/approved_qa.py`、`scripts/approved_qa_to_canonical_jsonl.py`、`scripts/backfill_tenant_id.py`、`scripts/ingest_canonical_jsonl.py`、`webapi/collection_admin.py`、`webapi/collection_promotion.py`、`tests/test_stage7_remaining.py`、`tests/test_stage8_additions.py`、`tests/test_stage9_additions.py`、`tests/test_stage10_additions.py`、`tests/test_stage14_security_and_ingestion.py`、`tests/test_stage15_documents_and_admin.py`、`tests/test_tenant_strict_filter.py`、`tests/test_qanda_table_pdf_to_approved_qa.py`、`tests/test_approved_qa_review.py`、`tests/test_approved_qa_to_canonical_jsonl.py`、`tests/test_qdrant_adapter_contract.py`（新規可）。他は読取のみ。必要な変更が範囲外なら勝手に拡張せずBLOCKED。

## 禁止操作
reset/restore/clean/stash/rebase、無関係な変更の削除、稼働中Docker/WSL/serviceの停止/再起動、実Qdrant/DBの書換え、外部ユーザーへの送信、secret・可視性・LICENSE変更、mainへの直接commit/pushを禁止。既存tagへの操作およびmergeは禁止。

## 実装要件
1. 最初に**必須要件→変更path→既存test/fixture/consumer→変更許可→検証方法→両立可否**を実コードで照合。adapterのメソッド・API・ID・payload key・assertionをrepository全体でexact検索。
2. 作業前のtracked/staged binary patch、対象untrackedのコピー、HEAD/statusを固有`/tmp`に保持。
3. get(ids,where)は両条件を同時適用し、`ids=[]`では何も返さない。whereのみ/idsのみ、Chroma互換include/limit/offset/戻り値を検証。
4. delete(ids,where)はQdrant側の単一フィルタ条件で選別し、read→deleteの競合を作らない。空IDsによる全件削除を禁止。呼び出し元のtenant/source_doc条件を最終削除へ伝播する。
5. upsertは外部書込みより前に全入力の長さ、ID、ベクトル次元、finite値を検査し、不正入力で部分書込みをしない。
6. metadata-only updateは存在IDにだけ適用し、本文・vector・無関係なmetadataを保持。Chroma互換APIは実際のconsumerに合わせる。
7. 承認済みQAの`qa_id`衝突を防ぐ方式を検討し、旧IDを参照する索引・引用・consumer・固定assertionを維持する。破壊的なID一括変更や本番索引移行はしない。互換性を確保できない部分はBLOCKEDとして移行案を報告する。
8. 修正に無関係な検索結果順位、JSON出力schema、CLI、公開エンドポイントは不変。

## 既存互換
ChromaとQdrant両方の既存API契約、metadata、結果JSON key、文書ID、引用IDを維持。security fixに伴う期待値変更以外はtest assertionを弱めない。

## test
既存`.venv`がある場合は利用。最初に狭い既存testのbaselineを確認し、隔離fixture/mockのみで誤tenant get/delete、ids+where、空IDs、異常upsert、offset、metadata update、QA ID衝突、削除/昇格consumerを検証。commit後に狭いテストを再実行し、必要な全体検証を行う（実モデル・長時間benchmarkは実行しない）。失敗をpassed/failed/skipped/blocked/flaky/unexecutedと原因区分で報告。

## runtime
既存Docker・Qdrant・PostgreSQLへ接続しない。テスト専用の一時ディレクトリ/インメモリQdrantのみ。必要な外部接続が生じたらBLOCKED。

## artifact
`/tmp/chatbot-phase1-*`へ実測テスト結果・変更path・整合チェック・残件を保存し、実際に読戻す。生成物は追跡対象へ無断追加しない。

## commit / push / PR / tag 権限
**条件付き許可**：必要な関連テストがPASSし、変更pathを検査してから、`feat/chatbot-phase1-qdrant-safety`に対象変更だけcommit、同ブランチへ通常push、`gh`が利用可能ならmain向けPRを作成してよい。PRにcheckpointからの差分が入ることを明記する。PRはCI待ち/CI失敗ならdraftのまま。merge/tagは禁止。force-push禁止。許可対象外のファイルはstageしない。

## 完了条件
許可path・既存互換・権限テストが成立し、対象変更だけがfeature branchにpushされ、検証artifactが存在すること。実Qdrantの動作やCIが未完了ならその項目は未確定として報告。

## 最終報告
RESULT、branch/HEAD、変更path、各testのpassed/failed/skipped/blocked/flaky/unexecuted、artifact、commit/push/PRの実施有無・URL、残存問題を簡潔に報告。長いdiff不要。
