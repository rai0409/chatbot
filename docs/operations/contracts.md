# 契約台帳と期限の計算（`rag_core/contracts.py`）

契約書から抽出した項目（相手方・契約終了日・更新・通知期限など）を台帳にし、期限を計算する。
**最大のリスクは、日付の誤りや欠落に誰も気づかないこと**。そのため、抽出された値は**人が確認するまで「確認済み」にならない**。

## 項目の状態

| 状態 | 意味 |
|---|---|
| `missing` | 値が無い（抽出できなかった、または検証に通らず保存しなかった） |
| `extracted` | 抽出された値。**未確認**。抽出側が自信ありと言っても、ここまで |
| `confirmed` | 人が「抽出された値は正しい」と確認した（`confirm_field`） |
| `corrected` | 人が値を入れた・直した（`set_field`） |

- `import_extracted`: 抽出の出力（`schema: "contract-extract/v1"`、`fields: {項目: {value, confidence, page, text}}`）を
  保存する。**検証に通らない値は保存せず**、`rejected` に理由を返す（存在しない日付、負の通知日数など）。
  人が確認・訂正した項目は、あとの抽出で**上書きしない**（値が違えば履歴に `extraction_differs` を残す）。
- 項目: `counterparty` `title` `effective_date` `end_date` `auto_renewal` `renewal_period_months` `renewal_notice_days`
  `termination_notice_days` `amount` `currency` `governing_law` `contact_email`。
- 日付は ISO、`2026/10/27`、`2026年10月27日`、`令和8年10月27日`（平成・昭和・元年）を受け付ける。
- 誰が・いつ・何を、は項目と履歴（`history`）に残る。同時更新は検出して拒否する（`rev`）。

## ステータス

`needs_review`（相手方と終了日が、確認または訂正されるまで）→ `active` → `expired`（確認済みの終了日を過ぎた）。

## 期限（`deadlines`）

| 種類 | 計算 |
|---|---|
| `end` | 契約の終了日 |
| `non_renewal_notice` | 終了日 − `renewal_notice_days`（**自動更新あり**の契約だけ）。更新しない旨の通知期限 |
| `termination_notice` | 終了日 − `termination_notice_days`。解約の通知期限 |

各期限は `days_left`（今日からの日数）と **`verified`**（計算に使う項目がすべて確認済みか）を持つ。`verified: false` の期限は
「未確認の値に基づく」ものとして扱う（通知にも明示する、7-2）。

## 限界（法的な判断ではない）

- カレンダーの日数で数える。**休日・営業日の調整はしない**。契約の条文を読んで解釈するものでもない（「起算日」「到達主義」など）。
  期限は人が行動するための目安で、**契約書の文言が優先する**。
- 抽出は別のプロジェクトの出力に依存する。このリポジトリには抽出器は無い（スキーマ `contract-extract/v1` が取り込み口）。
- 保存先は `CONTRACTS_DIR`（既定 `data/contracts/`、`.gitignore` 済み）。契約の内容は取引上の機密なので、バックアップと
  アクセス権の対象にする。
