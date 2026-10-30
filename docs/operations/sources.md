# 文書ソースの登録・変更の検出（5-1）

手作業の取り込みを、登録した**ソース**と、**何が変わったかの記録**に置き換える土台。この段階では、変更を**見つけて記録する**
だけで、コレクションには何も書かない（取り込みへの反映は 5-2）。

## ソース（`rag_core/source_registry.py`）

| 項目 | 内容 |
|---|---|
| `source_id` | 英小文字・数字・`_`・`-`、48 文字まで |
| `tenant_id` | このソースの文書の持ち主 |
| `kind` | `folder`（今回）。`sharepoint` は 5-3（ローカルのミラー経由） |
| `path` | `INGEST_ALLOWED_ROOTS` の中の実在するフォルダ。シンボリックリンクは拒否（取り込みジョブと同じ規則） |
| `collection` | 書き込み先のステージング収集（既定 `src-<id>`）。**本番（配信中）のコレクションは指定できない** |
| `schedule_minutes` | 0 = 手動。1 以上なら、その分数ごとの走査の対象（`due`） |
| `enabled` | 無効にすると定期の対象から外れる |

保存先は `SOURCES_DIR`（既定 `data/sources/`、`.gitignore` 済み）。原子的に書き、同時更新は検出して拒否する。
ソースの登録解除は、コレクション・マニフェスト・台帳を**消さない**。

## 走査と差分（`rag_core/source_scan.py`）

- ソースごとに 2 つのマニフェストを持つ: **applied**（コレクションに反映済みの状態）と **observed**（最新の走査）。
  変更 = observed と applied の差: `added` / `modified`（内容の SHA-256 が違う）/ `deleted`。
- 大きなフォルダでも軽い: サイズと更新時刻が前回と同じファイルは、ハッシュを読み直さない。
- 読まないもの: 隠しファイル・一時ファイル（`.`始まり、`~$`、`.tmp` など）、シンボリックリンク、`INGEST_MAX_FILE_BYTES` 超、
  対応していない拡張子。件数を記録する。
- **フォルダが無い・マウントされていない場合は「空」ではなく「利用不可」**として失敗を記録する。全件削除とは見なさない。
- 取り込みは文書をファイル名で識別するため、同じファイル名が複数あると互いを置き換えてしまう。そのパスを
  `name_conflicts` として報告する（5-2 はそれらを取り込まない）。

## 変更台帳（`rag_core/change_ledger.py`）

走査のたびに、ハッシュ連鎖つきの追記専用 JSONL（`changes.jsonl`）へ 1 件書く。件数・読まなかったものの数・
先頭 100 件の変更を載せ、全件は `runs/<run_id>.json` に保存してその SHA-256 を記録する（`read_changes` が照合する）。
`verify_chain()` が、書き換え・欠落・挿入を検出する。ローカルのファイルで、監査ログではない。

## 使い方

```
python scripts/sources.py add --id policies --tenant tenant_a --path /data/ingest/policies --schedule 60 --by 担当
python scripts/sources.py scan --id policies     # 変更を記録（コレクションは変えない）
python scripts/sources.py due                    # 走査の時間が来たソース
python scripts/sources.py changes --id policies
python scripts/sources.py verify
```

定期実行は、`due` を cron などから呼ぶ（5-2 で `run-due` が加わる）。
