# Mail batch ingestion (staging only)

This module connects `mail_ingest.process()` to the existing canonical
JSONL/staging ingestion boundary. It does **not** publish email to the
production collection or implement per-message ACL or thread-aware answers.

## Input and authorization

* Supported: EML, mail table CSV/TSV/XLSX/XLSM. An individual input is a file,
  never a directory. Send all messages from the same conversation in one batch
  to allow citation and parent reconstruction. `.msg` is not supported.
* Administrator endpoint: `POST /admin/ingestion/mail` with JSON
  `{"inputs":["uploads/<tenant>/<YYYYMMDD>/<upload_name>.eml"],
  "expected_tenant":"tenant_a","collection":"tenant_a_mail_stage",
  "execute":false,"async_job":false}`. `execute=false` is the default.
* File paths **must resolve inside** the configured `INGEST_UPLOAD_DIR` for
  the same tenant; the upload route is `PUT /admin/ingestion/upload?filename=example.eml&tenant=tenant_a`
  with the file bytes as raw request body. The API returns the stored path.
* `INGEST_ALLOWED_ROOTS` must contain the configured upload directory.
  Paths for other tenants, symlinks or directories are rejected.
* All routes require existing admin authentication and the existing tenant scope.
  Job records and audit metadata must not contain raw email bodies.

## Job behavior

* `mail_ingest.iter_inputs()` aggregates the batch, `process()` runs once, and
  `scripts.mail_to_canonical_jsonl._canonical_rows()` creates canonical rows.
* The initial mode deliberately disables the learned noise model. The source
  EML must be retained separately; extracted body text is not an immutable
  byte-for-byte copy of the original email.
* Only original messages are indexed, not reconstructed quote-only messages.
  `thread_hint` is **batch-local**, and is NOT a durable cross-import thread ID.
* `build_manifest()` runs before writes. Normal duplicate checks stay intact.
  A different original message's `text` includes its stable source ID to
  prevent false duplicate-text findings for short identical replies.
* Dry run writes **only** a job record in the configured registry; it does not
  write a vector collection or keyword corpus.
* `execute=true` is restricted to a non-production staging collection.
  The collection write lock and ownership registry are reused. A backend
  connection failure during existence detection cannot authorize a new
  collection. Interrupted/failed partial writes are **not** marked `ready`.
* Status is visible with existing `/admin/ingestion/jobs/{job_id}`.

## Limits

* `MAIL_INGEST_MAX_FILES=100` (also subject to `INGEST_MAX_FILES`).
* `MAIL_INGEST_MAX_FILE_BYTES=15728640` (15 MiB).
* `MAIL_INGEST_MAX_TOTAL_BYTES=52428800` (50 MiB).
* `MAIL_INGEST_MAX_MESSAGES=2000`.

These bounds reduce accidental load; they do NOT provide a hard CPU/memory
limit while parsing a hostile spreadsheet. Do not expose this ingestion path
to untrusted customer uploads without process isolation and decompression
limits. An Excel parser can expand archives beyond the compressed input size.

## Known limitations / release gate

* Without a valid Message-ID, the existing canonical converter uses a
  source-path fallback. Re-uploads under a new random upload path may therefore
  create another `source_doc`. Do not promise global idempotence for such rows.
* Identical Message-ID with different content in the **same** import is
  refused. Revisions across separate imports replace rows for the same
  `(tenant_id, source_doc)`; source provenance must be reviewed before
  enabling automatic version updates.
* No email-level ACL exists in this integration. It is suitable for
  restricted single-access-class staging evaluation **only**. Do not promote
  private mail to a broadly accessible tenant collection until effective
  user/role ACL has been implemented and tested at both retrieval and thread
  expansion points.
* End-to-end Qdrant-backed retrieval, embedding fingerprint compatibility,
  and thread-aware answers are **not** validated by the synthetic unit tests.
* User data should not be committed in git; do not log `text`, `searchable_text`
  or raw headers. BCC is not copied into the canonical row.

## Validation

From the repository root using its own Python environment:

    python -m pytest -q tests/test_mail_ingestion_contract.py
    python -m pytest -q tests/test_ingestion_ui_job_status.py tests/test_ingestion_tenant_paths.py tests/test_stage14_security_and_ingestion.py tests/test_stage3_ingestion.py
    git diff --check

For initial activation, upload only **synthetic** mail and call the new
endpoint with `execute=false`. Inspect the returned `manifest_ok`,
`chunks_generated` and job status. Do not set `execute=true` until existing
staging-backend connectivity and authorization are verified on the target host.


## 安全隔離（メール単位ACL導入前）

メールはTenantだけでの閲覧を許可しません。現行の認証主体はAPIキーと個人を
統一して識別できず、認可済みのメール別ポリシー正本が未実装です。
`rag_core/mail_access.py` により、メールは既定で通常のDense/Keyword/Hybrid検索、
親・隣接展開、`/chat/source`の引用本文取得から除外します。
これは**一時的なFail-Closed隔離**であり、ユーザー別ACLを実装完了した意味ではありません。

- stagingに登録済みのメールがある場合でも、`/chat` や `/search` で取得しません。
- オペレーターによるstagingの登録・読戻しは別の管理経路を用いて検証します。
- 非メール資料の検索互換性を維持します。Qdrantは `type != email` で事前除外し、
  両バックエンドで結果を再検査します。Chromaの候補上限によるRecall低下は未解決です。
- 過去のメールを含み得る回答キャッシュは新しい名前空間へ切り替えます。
  既存ノードを同時切替できない期間や外部ログ・既存承認QAの消去までは保証しません。
- 個人単位ACLを公開するには、IdP主体の永続識別子、承認済みポリシー正本、
  権限更新時の再評価・失効、全consumerの検証が別途必要です。
- **本番データの取り込み・本番公開は不可**。本項目はACL完成証明ではありません。

昇格 API と `manage_documents.py promote` は同じ検査を使用し、対象テナントの
ステージングにメールを示すチャンクが一件でもある場合、混在データ全体を拒否します。
ドライランも拒否し、メタデータが不完全または整合しない場合も公開しません。
検査は本番への書込み、削除、キーワード更新の前に実施します。メールの個人単位 ACL は
引き続き未実装です。
