# 承認済み QA の言い換え（approved_aliases）の候補

質問側の言い換えの仕組みは以前からある。承認済み QA の 1 件に `approved_aliases`（最大 20 個、各 500 文字まで）を持たせると、
利用者の質問が別表現と（正規化後に）一致したとき、承認済みの回答に直接届く
（`approved_qa.lookup_approved_answer`、応答の `answer_mode` は `approved_alias_match`）。
3-5 は、**どの言い換えがまだ届かないか**を調べ、**追加する候補**を出す。

## 網羅の確認（`coverage`）

承認済みの各質問を機械的に揺らし（3-1 の摂動）、変種が同じ承認済み回答に届くかを、実際の照合関数で確かめる。
種類ごとの「届く数 / 変種の数」と、届かなかった変種の一覧が得られる。

```
python scripts/alias_candidates.py coverage --tenant ID [--approved ファイル] [--kinds casual_ending,keywords_only] [--out ファイル]
```

候補にするのは、利用者が実際に繰り返し打ちそうな種類（既定: `casual_ending` `keywords_only` `katakana_hiragana` `spacing`）の
うち届かなかったもの。誤字・字落ちは繰り返されにくいので既定では候補にしない。1 件に追加できる数の残り
（20 − 既存）、既存の質問・別表現との重複、制御文字、500 文字超を除き、1 つの質問につき `max_per_question`（3）まで。

## 質問からの抽出（`mine`）

承認済みの回答に届かなかった利用者の質問（1 行 1 質問のファイルを**ご自身で用意**する）を、文字 2 グラムの類似度で
最も近い承認済みの質問（別表現を含む）に対応づける。しきい値（既定 0.5）以上の最良の 1 件だけを候補にする。

```
python scripts/alias_candidates.py mine --tenant ID --questions ファイル [--threshold 0.5] [--out ファイル]
```

本番の監査ログは質問をマスクするため、元の質問は平文モードのログ、または利用者の同意のある収集から用意する。

## 出力と運用

- 出力は JSONL（`status: "pending"`、`candidate_id`、`qa_id`、`question`、`alias`、`source`、`kind` または `similarity`）。
  既定の出力先は `RUNS_DIR/alias_candidates/<tenant>_<コマンド>.jsonl`。
- **コードは承認済み QA のファイルを変更しない**（出力先に承認済み QA のファイルは指定できない）。
  候補は提案にすぎない。人が「承認済みの回答が、その言い方の質問にも答えているか」を確認してから、
  既存のレビューの手順で `approved_aliases` に追加する。似ているだけで答えが違う質問を足すと、誤った承認済み回答を返す。
- 質問ファイルと出力には利用者の文章が入りうるので、リポジトリ内の無視されるフォルダ（`runs/`）に置く。
