# 契約の管理 API と画面（7-3）

7-1 の契約台帳と、7-2 の通知・下書きを、管理画面から使う。`/admin/contracts`（管理者の認証が必要。`tenant_id` は管理者の
テナント範囲で確認する）。

## API

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/admin/contracts` | 画面 |
| GET | `/admin/contracts/list` | 契約の一覧: 相手方・件名・状態・直近の期限・**未確認の値の数** |
| GET | `/admin/contracts/upcoming` | これからの期限（`within_days`、既定 90 日。期限超過は 30 日前まで） |
| GET | `/admin/contracts/{id}` | 1 件: 全項目の状態・信頼度・根拠（頁と文）、期限（`verified` つき）、履歴（直近 50 件） |
| POST | `/admin/contracts/import` | 抽出結果（`contract-extract/v1`）の取り込み。**すべて未確認で保存**。保存しなかった項目と理由を返す |
| POST | `/admin/contracts/{id}/field` | 人が値を入れる・直す（訂正済み） |
| POST | `/admin/contracts/{id}/confirm` | 抽出された値が正しいと確認する（確認済み） |
| POST | `/admin/contracts/notify` | 通知の実行。`dry_run`（既定 `true`）は一覧だけ。`false` で送信（各 1 回） |
| POST | `/admin/contracts/{id}/draft` | 期限 1 つについての Outlook の**下書き**を作る（送信しない） |

- 下書きは、相手方の連絡先メールが確認・訂正済みで、その期限が確認済みの値だけに基づくときだけ作れる。メールのアクセスが
  未設定なら 409、Graph の失敗は詳細を出さない 502。
- 監査ログ（`contract_imported` / `contract_field_set` / `contract_field_confirmed` / `contract_notify_run` /
  `contract_draft_created`）には、契約 ID・項目名・件数だけを書き、**値は書かない**。判断した人は、他の管理 API と同じく
  管理者の「範囲」（`installation` またはテナント名）。

## 画面

- 契約の一覧（状態・直近の期限・未確認の数）と、これからの期限の一覧。行を選ぶと、右に項目ごとの**値・状態・根拠**が出て、
  「確認」「訂正して保存」ができる。期限には**確認済みか未確認か**が出て、Outlook の下書きを作れる。
- 「通知の予定を見る（送らない）」と「通知を送る」（送る前に確認ダイアログ）。
- 「抽出結果を取り込む」に、抽出の JSON を貼って取り込む。
- 文字はすべて `textContent` で挿入する。管理メニューに「契約管理」を追加した。

## 運用の流れ

1. 契約の抽出結果を取り込む（すべて「未確認」）。
2. 原本と見比べて、相手方と終了日、通知期限を**確認・訂正**する。確認するまで、契約は「要確認」で、期限は「未確認」と表示される。
3. 通知は cron などで `contract_notify.run` を呼ぶ（画面から手動でも実行できる）。
4. 更新しない旨の連絡などが必要なときは、下書きを作り、Outlook で読み、直して、人が送る。

期限は暦日の目安で、法的な判断ではない。契約書の文言が優先する。
