# ゴールデン QA とノブ・レジストリ

## ゴールデン QA（`rag_core/golden_qa.py`）

`config/rag_profiles/<id>/exported/golden_qa.jsonl` の 1 行 = 1 ケース。

| 項目 | 意味 |
|---|---|
| `case_id`, `question` | 必須 |
| `expected_all` | 回答に全部含まれるべき語 |
| `expected_any` | 回答に 1 つは含まれるべき語（空なら検査しない） |
| `forbidden_any` | 回答に含まれてはいけない語 |
| `expected_citation` | 出典が付くべきか |
| `expected_question_type` | 質問タイプ |
| `priority`, `enabled` | 並び順（大きいほど先）、無効化 |
| `group`（任意） | 同じ資料・同じ話題のケースに同じ値を付ける。未指定なら `case_id` 単独 |
| `origin_change`（任意） | そのケースを書くきっかけになった変更の ID |
| `tags`（任意） | 文字列のリスト |

- `load_cases(profile)`: 読めた順位つきケースと、**使えなかった行の問題一覧**（不正な型、`case_id` 重複、
  何も検査しないケース）を返す。黙って捨てない。
- `evaluate_case(case, answer_text=, has_citation=, question_type=)`: 合否と理由
  （`missing_all` / `missing_any` / `forbidden` / `no_citation` / `question_type`）。回答の生成は呼び出し側
  （2-4 のランナー）が行う。マッチは NFKC 正規化・大文字小文字無視。
- `assign_folds(cases, k, seed=)` / `split_by_fold`: **グループ単位**で K 分割する。同じグループは必ず同じ fold
  になり（ある変更を調整したケースの言い換えで、その変更を評価してしまうのを防ぐ）、fold は `(seed, group)` だけで
  決まるのでケースを足しても他のケースは動かない。
- `exclude_motivating(cases, change_id)`: その変更のために書いたケース（`origin_change` 一致）を評価から外す。

## ノブ・レジストリ（`rag_core/knob_registry.py`）

提案できる設定を、登録された名前・型・範囲に限る。`check_value` / `check_change` が、未登録の名前、型の違い
（真偽値に数値、整数に小数など）、範囲外、有限でない数を `KnobError` で拒否する。`stage` は作用する段、
`risk="high"` は回答するかどうかを決める距離ガード（より厳しい承認が必要）。`snapshot()` は現在値
（環境変数由来）で、評価台帳に記録する。

- 範囲は**初期値**。実データの評価で調整する。
- レジストリの全項目が回答キャッシュのキーの設定ダイジェストに含まれることをテストで保証する
  （含まれないノブを変えるとキャッシュが古い回答を返すため）。この確認で
  `CROSS_ENCODER_RERANK_ENABLED` が抜けていたのを追加した。
