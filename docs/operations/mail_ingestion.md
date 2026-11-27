# Mail batch ingestion (staging only)

This module connects `mail_ingest.process()` to the existing canonical
JSONL/staging ingestion boundary. It does **not** publish email to the
production collection, enforce per-message ACL at retrieval, or provide thread-aware answers.

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

* `MAIL_MAX_MIME_PARTS=200`, `MAIL_MAX_MIME_DEPTH=20` (per EML; the MIME walk
  is iterative; exceeding either fails the batch with `mail_mime_limit`).
* `MAIL_CONVERT_TIMEOUT_SECONDS=300`: the API job parses the batch in a fresh
  spawned worker process (`isolated.run_task("mail_batch")`) that is killed at the
  limit (`error_type=ConversionTimeout`); `0` parses in the calling process.
* `MAIL_CONVERT_MAX_MEMORY_MB=2048`: `RLIMIT_AS` of that worker (`0` = none).
  The worker starts without secret-looking environment variables, so the ACL
  key is never present while hostile input is parsed.

The synchronous route (`async_job=false`) still holds the request thread while
it waits for the worker; prefer `async_job=true` for untrusted uploads.

## Per-person ACL data model (enforcement still fail-closed)

* Every canonical row carries `acl_principals` (sorted HMAC-SHA256 hex digests of
  normalized lowercase From/To/Cc addresses, plus `mailbox_owner` when the job
  is given one), `acl_version` (1), `acl_key_id` and `sender_principal`.
  No plaintext address is stored in ACL fields.
* BCC: a BCC recipient is added only when it is the given `mailbox_owner` (the
  owner's own copy). BCC addresses never appear in row text or metadata (they
  are replaced by `[非表示]` if quoted in the body).
* `MAIL_ACL_HMAC_KEY` is required with `APP_ENV=production` (ingestion is refused
  with `mail_acl_key_required`). Development uses a fixed key and marks rows
  `acl_key_id=dev`; such rows are never visible in production.
* `rag_core.mail_access.mail_visible_to(record, principals)` is True only for a
  supported ACL whose digests intersect the caller's; `mail_record_allowed()`
  additionally requires `MAIL_SEARCH_ENABLED` (default false). Retrieval still
  excludes all mail via `is_private_mail_record()`.
* Migration: rows ingested before this change have no ACL fields and stay
  invisible even when search is enabled; re-ingest them. Rotating the key
  requires re-ingestion as well.

## Quote handling

* A quote is replaced by a reference to another message only when the parties
  of the message holding the referenced text include every party of the quoting
  message (BCC excluded). Otherwise the quote stays inline
  (`照合済みの引用（原本メール内に保持）`), so a person added later keeps context
  without being joined to a message they did not receive.
* `superseded_by`: the source_doc of a later message that quotes the full
  content of this one and whose parties include all of this one's.
  `MAIL_KEEP_LATEST_ONLY=true` (default false) keeps such quotes inline in the
  latest message and drops the quoted parts (never the new content) of the
  superseded row when the latest message contains them (`quoted_text_dropped`).
* Cross-import matching: a per tenant/collection state store
  (`<dir of INGESTION_REGISTRY_PATH>/mail_state/<tenant>/<collection>.jsonl`,
  mode 0600) keeps a bottom-k sketch of salted 4-gram hashes per message
  (`MAIL_SKETCH_MAX_SHINGLES=512`, sketches for the newest
  `MAIL_SHINGLE_STORE_MAX_MESSAGES=5000`, entries `MAIL_STATE_MAX_MESSAGES=100000`).
  No body text or address is stored. Quotes of earlier imports become
  `quote_refs` instead of `未照合の引用` when the ACL condition holds. The store is
  local to the node; it is not shared with `INGESTION_REGISTRY_BACKEND=postgres`.

## Update safety and deletion

* Re-importing a known source_doc (Message-ID): identical content →
  `skipped_identical`; older Date → `skipped_older`; same Date with different
  content, missing Date, or a different sender digest → `conflict_same_message_id`
  (existing rows kept); newer Date from the same sender → `replaced_newer`.
  Counts are in the job's `mail_counts`.
* Identity without Message-ID: EML → hash of the file bytes; tables → id column
  (8+ characters), otherwise hash of the file bytes + row/id. A file name alone
  is never used (two different `mails.csv` uploads do not collide).
* Deletion: `webapi.mail_ingestion_jobs.delete_mail(tenant, collection,
  source_doc=... | message_id=..., commit=False)` returns the chunk ids,
  `dependent_source_docs` (rows whose quote was replaced by a reference to it;
  re-ingest them) and `superseded_source_docs`. Delete the chunks by source_doc,
  then call again with `commit=True` to drop the state entry.
* Generic CSV/XLSX ingestion should refuse mail-shaped tables with
  `mail_access.looks_like_mail_table(header)`.

## Known limitations / release gate

* Without a valid Message-ID, identity is content based: an edited file is a
  new `source_doc`.
* Identical Message-ID with different content in the **same** import is
  refused. Across imports see "Update safety" above.
* The ACL data model exists but is not enforced by retrieval yet. It is suitable for
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

    python -m pytest -q tests/test_mail_ingestion_contract.py tests/test_mail_acl_isolation_20261122.py
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
データモデル（acl_principals 等）のみ実装済みで、検索・昇格での適用は引き続き未実装です。

## Parsing isolation and multi-node (2026-11)

* `convert_mail_inputs` and `scripts/mail_to_canonical_jsonl.py` parse in the isolated `mail_batch` worker by default
  (time and memory limited, secrets removed). `--in-process` (CLI) / `isolate=False` (function) is for debugging only.
* The mail state store is node-local files. With `INGESTION_REGISTRY_BACKEND=postgres` it is NOT shared: run mail
  ingestion on one node only. A warning is logged at startup and on first use.
