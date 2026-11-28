# 回答の根拠記録（2026-11-27）

`/chat` と `/chat/stream` の回答ごとに、サーバーが `request_id` を発行し、どの根拠で答えたかを監査ログに残します。
実装は `rag_core/answer_evidence.py`。

## request_id と trace_id

- 応答の `request_id` は常にサーバーが付けます（検索・生成した回答はパイプラインの ID、承認済み回答・キャッシュの
  回答は新しい ID）。クライアントが送った `trace_id` は応答の `trace_id` と記録の `client_trace_id` に残しますが、
  根拠記録の ID には使いません。
- キャッシュから返した応答は毎回新しい `request_id` を持ち、記録の `origin_request_id` が、その回答を作った
  要求を指します。キャッシュの内部項目 `_origin` は応答に出しません。

## 記録の中身

`<RUNS_DIR>/audit/evidence_events.jsonl`（種類 `evidence`）。ほかの監査ログと同じハッシュチェーン・ローテーション・保存期間で、
`scripts/verify_audit_chain.py` の対象です。`AUDIT_EVIDENCE_ENABLED=0` で止まります。

| 項目 | 内容 |
|---|---|
| `request_id` / `tenant_id` | サーバーの ID とテナント |
| `answer_sha256` | 回答本文の sha256（**本文は残さない**。質問文も残さない） |
| `answer_mode` / `approved_qa_id` | 回答の種類 |
| `cited[]` | 番号、`chunk_id`、`member_chunk_ids`、`source_doc`、`source_pages`、`chunk_sha256`（引用ブロックの本文の sha256）、`source_row_sha256`（取得時に確認したコーパス保存行 text の sha256（不明なら null））、`doc_checksum`、`index_generation` |
| `selected_context_chunk_ids` | 生成に渡したチャンク |
| `collection` / `active_generation_id` | ステージングのコレクション、使用世代が全コンテキストで一致して確定できた場合の索引世代（それ以外は null） |
| `rag_profile_id` / `rag_profile_version` | RAG プロファイル |
| `cache_key_digest` / `served_from_cache` / `origin_request_id` | キャッシュの要約と出どころ |

チャットの監査イベントには、実際に引用した文書 `cited_source_docs` を加えました（`top_source_docs` は並べ替え後の
上位候補のまま、互換のため残します）。

引用（`citations[]`）にも、分かるときは `chunk_sha256`・`doc_checksum`・`index_generation`・`collection` が付きます
（任意項目。古いクライアントは無視できます）。

## 使い道

- `/chat/source?chunk_id=...&request_id=...[&number=N]`: その回答が引用した版と今の行を比べ、
  `stale`（true = 変わった、false = 同じ、null = 不明）、今の `index_generation`、引用時の `cited_index_generation` を返します。
  `request_id` なしは従来どおり。
- `POST /chat/threads` に `request_id` を付けると、アクセススコープと本文の SHA-256 が根拠記録と一致する場合に `answer_mode`・`citations_count`・`source_docs` を根拠記録から取り、
  クライアントの値は使いません（`provenance: "server"`）。一致しない場合や付けないときは従来どおり（`provenance: "client"`）。
  文書の削除（`redact_source_doc`）は、サーバーが埋めた turn に確実に効きます。
- `/chat/feedback` は `request_id` が同じテナント・アクセススコープの根拠記録を指すとき `linked: true` になります。

## 検索の制限

`lookup_evidence` は、このプロセスが書いた最近の記録（`EVIDENCE_LOOKUP_CACHE_SIZE`、既定 4096 件）と、
現在のファイルの末尾（`EVIDENCE_LOOKUP_TAIL_BYTES`、既定 4 MiB）だけを見ます。ローテーションで移った古い記録や、
末尾より前の記録は見つからず、その場合は「不明」（`stale: null`、`provenance: "client"`、`linked: false`）として
従来の動きに戻ります。別のテナントの記録は見つかっても使いません。

## 版の比較についての注意

- `chunk_sha256` は引用ブロック（同じページの結合・親チャンクの展開を含む、モデルに見せた範囲）の本文の要約です。
  1 行の本文とは一致しないことがあるため、`/chat/source` の比較には `source_row_sha256` を使います。
- 生成に渡す根拠は 1 ブロック 1200 文字までで、出力の検証・根拠の確認・引用も同じ範囲の本文で行います
  （モデルが見ていない後半の文で回答が「裏付けられる」ことはありません）。

## キャッシュ根拠の保存

根拠記録が有効なとき、最初に監査保存に成功した回答の根拠をキャッシュ内部に保存します。ヒット時は本文ハッシュ、テナント、アクセススコープ、キー、引用内容と形式を検証し、元の引用・索引世代・プロファイルを再利用して新しい request ID の記録を保存します。旧形式や不整合のあるキャッシュはミスとして再生成します。監査保存が失敗した新規回答は返しますがキャッシュしません。ヒット時の保存失敗も回答は返しますが、その request ID の根拠は参照できません。

`AUDIT_EVIDENCE_ENABLED=0` の間は根拠を作成せず、従来どおりキャッシュします。後に有効化すると、その間のキャッシュは検証済み根拠がないためミスになります。キーワード検索では取得した保存行の text をハッシュ化します。vector や近傍など保存行との同一性を確認できない経路は行ハッシュを null とします。親展開ではモデルに渡した親本文と引用先の子の行を区別し、ページ結合では全要素が一致する版だけを残します。異なる世代や欠損を現在の active pointer で補完しません。

アクセススコープは、共有検索では空文字、ACL で絞る場合は既存の principal digest、確定不能なら null です。異なるスコープ、null、旧記録では根拠参照を許可しません。旧形式キャッシュはミスとして再生成します。これは文書単位 ACL や索引全体の snapshot isolation を追加するものではありません。
