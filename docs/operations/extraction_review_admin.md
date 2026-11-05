# 抽出レビューの管理 API と画面（6-3）

6-1 で保存した OCR の行と、6-2 の判断（補正・確認）を、管理画面から操作する。`/admin/extraction`（管理者の認証が必要。
`tenant_id` は管理者のテナント範囲で確認する）。

## API

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/admin/extraction` | 画面 |
| GET | `/admin/extraction/docs` | 記録のある文書: 行数、未確認・補正済み・確認済みの数、ページ画像の可否 |
| GET | `/admin/extraction/items` | 見るべき行（信頼度の低い順）。`max_confidence`, `include_reviewed`, `source_doc`, `limit` |
| GET | `/admin/extraction/page` | 1 ページ分: 画像の大きさ、各行の文字・信頼度・位置・判断 |
| GET | `/admin/extraction/page-image` | ページの PNG。OCR と同じ解像度で、元のファイルから描画する（行の枠がそのまま合う） |
| POST | `/admin/extraction/correct` | 行の文字を直す |
| POST | `/admin/extraction/confirm` | 「このまま正しい」 |
| POST | `/admin/extraction/revert` | 判断を取り消す |
| POST | `/admin/extraction/reingest` | その文書を、テナントのソースが次の反映で取り込み直すよう予約する |

- ページ画像は、元のファイルが `INGEST_ALLOWED_ROOTS` の中にあり、`pypdfium2` が使えるときだけ返す。そうでなければ **404**
  （画面は画像なしで文字だけを表示する）。**ファイルの場所はどの応答にも出さない**。
- すべての変更を管理監査ログに記録する（`extraction_corrected` / `extraction_confirmed` / `extraction_reverted` /
  `extraction_reingest_requested`）。判断した人は、ソース API と同じく**管理者の「範囲」**（`installation` またはテナント名）。

## 画面

左に見るべき行の一覧（文書・頁・信頼度・OCR の文字・状態）。行を選ぶと、右にページ画像が出て、**すべての行に枠**が付く
（信頼度の低い行は赤、選んだ行は太い橙）。その下のフォームで、文字を直して「補正を保存」、そのままなら「このまま正しい」、
間違えたら「判断を取り消す」。「この文書の再取り込みを予約」で、反映に必要な取り込み直しを予約する。
文字はすべて `textContent` で挿入する。管理メニューに「抽出レビュー」を追加した。

## 流れ（運用）

1. 取り込みのあと、画面を開いて、信頼度の低い行を原本と見比べる。
2. 直す・確認する。
3. 文書ごとに「再取り込みを予約」する（登録したソースの文書）。次の `apply`（`run-due --apply` など）で取り込み直され、
   インデックスに補正後の文字が入る。手でアップロードした文書は、アップロードし直す。
