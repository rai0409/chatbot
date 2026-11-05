# フィードバック・影子比較・ドリフト監視

2-6 の状態機械（観測→…→影子→…→監視）の前後に使う関数群。画面・定期実行・実トラフィックの分割は含まない
（それらが呼ぶ関数）。しきい値はすべて初期値で、実データで調整する。

## フィードバック（`feedback.py`）

- チャット UI の 👎 の理由コードを、最初に見るべき提案カテゴリに対応づける（人が決める、最初の 1 つが第一候補）。

  | 理由 | 候補カテゴリ |
  |---|---|
  | `wrong_intent`（質問の意図と違う） | `synonym`, `boost_rule` |
  | `incorrect`（内容が誤っている） | `validation_rule`, `boost_rule` |
  | `outdated`（情報が古い） | `stale_content` |
  | `wrong_source`（出典が合っていない） | `boost_rule`, `retrieval_setting` |
  | `incomplete`（情報が足りない） | `ingest_gap`, `retrieval_setting` |
  | その他 | `other` |

- `aggregate(events, min_count=5)`: フィードバックトークンごとに最新の 1 票だけ数え、テナント別に good/bad、
  不満率（Clopper–Pearson の両側区間）、理由・カテゴリ別件数、`min_count` 件以上の理由を「観測」として返す。
- `open_observations(observations, profile_id=, actor=)`: 観測ごとに `observed` の提案を作る（テナント・理由・日付で一意、
  再実行しても重複しない）。この後は `classify` → `propose` …（`docs/operations/change_control.md`）。
- **提案に保存するのは件数・理由コード・フィードバックトークンだけ**。コメントと質問文は写さない。
  本番の監査ログは質問とコメントをマスク/ハッシュ化するため、フィードバックからゴールデン QA を自動で作ることはできない
  （作るのは人が、本人の質問と期待する回答を確認して `golden_qa.jsonl` に書く）。

## 影子比較（`shadow.py`）

- `compare_runs(questions, baseline_fn, candidate_fn)`: 同じ質問を基準と候補に通し、回答の相違、回答→棄権、棄権→回答、
  出典の喪失/獲得、先頭距離の変化、エラー、遅延比を数える。行には質問文も回答文も入れず、索引だけを持つ。
- `verdict(summary)`: `ok` / `review`（理由つき）/ `insufficient`（100 件未満）。基準: 新たな棄権 2% 超、出典の喪失 2% 超、
  回答の相違 30% 超、候補のエラー増。
- `evidence_text(summary, decision)`: `advance`（影子の段）に書く根拠の 1 行。
- 質問の元は、ゴールデン QA、または平文モード（`AUDIT_QUESTION_MODE=plain`）の監査ログ。本番（既定でマスク）のログは使えない。
  実ユーザーのトラフィックを使う影子配備は、サービング側の変更が要るため別。

## ドリフト監視（`monitoring.py`）

- `compare(基準期間のチャットイベント, 現在期間のチャットイベント)`: ガード率・フォールバック率（2 標本の比率検定）、
  出典数・遅延（PSI と KS）、質問意図の構成（カテゴリ PSI）。キャッシュヒットは数値指標から除く。
- 状態: `ok` / `warn` / `alert` / `insufficient`（各期間 200 件未満）。PSI は 0.1 未満が安定、0.25 以上が明確な変化。
  比率は 5 ポイント以上動き、かつ有意（p < 0.01）で warn、10 ポイント以上で alert。KS は有意かつ D ≥ 0.1。
- 先頭距離は監査イベントに記録されていないため、現時点の監視対象外（必要なら `chat_event` に追加する別の変更）。
