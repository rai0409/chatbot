# 文書ソースの管理 API と画面（5-4）

`/admin/sources`（管理者の認証が必要。管理者のテナント範囲に従う）。登録・走査・計画・反映・承認・有効/無効・
登録解除・変更履歴の API と、それを操作する画面。

## API

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/admin/sources` | 画面（共通の管理ヘッダつき HTML） |
| GET | `/admin/sources/list` | 範囲内のソース: テナント・種類・収集・間隔・有効・状態・**保留中の変更**とガードの判定。サーバーのパスは返さない |
| POST | `/admin/sources` | 登録。フォルダは `path`（`INGEST_ALLOWED_ROOTS` の中）、SharePoint は `options` |
| POST | `/admin/sources/{id}/scan` | 今すぐ走査。件数・読まなかった数・同名ファイルの数 |
| GET | `/admin/sources/{id}/plan` | 保留中の変更（各 200 件まで）、plan の SHA-256、ガードの判定 |
| POST | `/admin/sources/{id}/apply` | ステージング収集へ反映。保留なら `held` と理由。`{"approval": SHA}` で承認済みの plan を実行 |
| POST | `/admin/sources/{id}/approve` | `{"plan_sha256": SHA}`: **その plan だけ**を承認 |
| POST | `/admin/sources/{id}/enable` `disable` `remove` | 定期の対象にする/外す/登録解除（収集・マニフェスト・台帳は残る） |
| GET | `/admin/sources/{id}/changes?limit=` | そのソースの台帳の記録（要約） |

- 範囲外のテナントのソースは、存在しないものと同じ **404**（他テナントのソース ID の有無を教えない）。
  別テナントを指定した登録は 403。
- すべての変更を管理監査ログに記録する（`source_created` / `source_scanned` / `source_applied` / `source_approved` /
  `source_enabled` / `source_disabled` / `source_removed`）。
- **承認者の記録は、管理者の「範囲」**（`installation` またはテナント名）。いまの管理者認証には個人の識別が無いため。
  それでも承認は 1 つの plan の SHA-256 に結びつき、あとから変更が変わると無効になる。個人名で記録したい場合は、
  管理者認証に個人の識別を足す別の変更が要る。

## 画面

ソースの一覧（保留中の変更・最終走査・最終反映）と、ボタン: 走査 / 計画 / 反映 / 履歴 / 無効・有効 / 登録解除。
反映が保留になると理由を示し、確認のうえ「この計画を承認して反映」できる。登録フォームはフォルダと SharePoint に対応。
すべての文字は `textContent` で挿入する。管理メニューに「文書ソース」を追加した。

## 流れ（運用）

1. ソースを登録する（`schedule_minutes` で定期の間隔）。
2. cron などから `python scripts/sources.py run-due --apply`（保留でないものだけを反映。保留は人を待つ）。
3. 画面で保留中のものを確認し、計画を見て承認する。
4. 反映後、ステージング収集を `/admin/ingestion` で確認し、昇格する。
