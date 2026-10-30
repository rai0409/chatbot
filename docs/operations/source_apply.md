# 差分の取り込みと大量変更ガード（5-2）

5-1 の走査が見つけた変更を、ソースの**ステージング収集**へ反映する。既存の取り込みジョブと文書削除の仕組みをそのまま使い、
大量の変更は**人が承認するまで保留**する。配信中のコレクションへの昇格はここでは行わない（`/admin/ingestion/promote` か
インデックスの世代切り替えで、運用者が行う）。

## 流れ

```
scan（5-1）→ plan（保留中の変更と plan の SHA-256）→ ガード → apply → 台帳に記録
```

- **追加・更新**: `ingestion_jobs.run_raw_document_job`（文書名 = ファイル名で行を置き換えるので、更新した文書に古いチャンクが残らない）。
  `INGEST_MAX_FILES`（既定 100）件ずつのバッチ。
- **削除**: `collection_admin.delete_documents`（ベクトル行とキーワード行の両方）。収集がまだ無ければ削除するものは無い。
- **同じファイル名**が複数あるファイル（取り込みが互いを置き換えてしまう）は、取り込まずに `conflicts` として報告する。

## 大量変更ガード

次のどれかに当たる plan は**保留**（`status: held`）になり、承認された plan だけが実行される。

| 理由 | 条件 |
|---|---|
| `all_deleted` | 反映済みのファイルが全部削除されることになる（共有フォルダが空・未マウントの疑い） |
| `mass_delete` | 削除が `SOURCE_MIN_DELETES_FOR_GUARD`（5）件以上で、反映済みの `SOURCE_MAX_DELETE_FRACTION`（20%）を超える |
| `too_many_changes` | 変更の合計が `SOURCE_MAX_CHANGES`（500）を超える |

- 保留は台帳に `hold` として残る。何も書き込まない。
- 承認: `python scripts/sources.py approve --id ID --plan <SHA-256> --by 担当`。承認するのは**その plan 1 つ**で、
  その後の走査で変更が 1 つでも変われば plan の SHA-256 が変わり、承認は無効になる（もう一度見直す）。
- 自動実行（`run-due`）は**承認しない**。保留のものは人を待つ。

## 失敗の扱い

- 取り込みジョブは、バッチ内に 1 件でも問題があると何も書かない。そのため失敗したバッチは**1 ファイルずつ**やり直し、
  問題のあるファイルだけが失敗として残る。失敗したファイルは反映済みにならず、**次の apply でもう一度**試される。
- 反映済みのマニフェストは、成功した分だけ進む。例外が起きたときも、それまでに成功した分は記録される（`apply_failed`）。

## コマンド

```
python scripts/sources.py plan --id ID                  # 変更の数、plan の SHA-256、ガードの判定
python scripts/sources.py apply --id ID                 # 保留でなければ反映。保留なら held と理由
python scripts/sources.py approve --id ID --plan SHA --by 担当
python scripts/sources.py apply --id ID --approval SHA --by 担当
python scripts/sources.py run-due [--apply]             # 時間が来たソースを走査（--apply で保留でないものを反映）
```

cron からは `run-due --apply` を呼ぶ。反映の結果は台帳（`changes`）と、収集の状態（`/admin/ingestion`）で確認する。
