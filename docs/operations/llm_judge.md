# LLM による採点（`eval/tenant_eval/llm_judge.py`）

ゴールデン QA は語の有無で合否を決める。それでは「言い回しの違う正しい回答」と「誤った回答」を区別しきれない。
LLM の機能 `judge`（`LLM_ENABLED=true` と `LLM_FEATURES=judge`、`docs/operations/llm_policy.md`）で、回答が**基準の回答**と
同じ内容かを採点できるようにする。既定はオフ。

## ゴールデンケースの `reference`

`golden_qa.jsonl` の各行に、任意項目 `reference`（人が正しいと認める回答文）を書ける。`reference` が無ければ、採点には
`expected_all` / `expected_any` / `forbidden_any` の語を基準として渡す。`reference` だけを持つケースも「何かを検査する」
ケースとして有効（語の検査は素通りし、LLM 採点が検査する）。

## 採点（`judge_case` / `judge_rows`）

質問・基準・回答を渡し、`{"verdict": "pass" | "fail", "reason": "…"}` の JSON だけを受け取る。**拒否（ポリシー）・失敗・読めない
応答はすべて `error`** で、合格にはならない。`judge_rows` は評価ランナーの行（回答があるもの）を採点する。

## 人の採点との一致（校正）

LLM 採点は、人の採点と十分に一致すると確かめられるまで**使わない**。

- `agreement(人の採点, LLM の採点)`: 両方が採点したケースの件数、正解率、Cohen の κ、混同行列、
  **誤合格率**（人が不合格にしたものを LLM が合格にした割合）とその Clopper–Pearson 上限。
- `usable(報告)`: 50 件以上、κ ≥ 0.6、誤合格率の上限 ≤ 10% のすべてを満たすときだけ使える。満たさない理由を返す。
- `conservative_pass(語の結果, LLM の判定, 使えるか)`: 語による結果を基本とし、**使える採点者は不合格にする（拒否権）ことだけ**ができる。
  語で不合格のケースを LLM が合格にすることはない。

## 手順

1. `reference` つきのゴールデンケースを用意する。
2. 評価を実行して回答（`answer_text`）を得る（`tenant_eval` のランナー）。
3. 一部（50 件以上）を人が採点し、`judge_rows` の結果と `agreement` / `usable` で突き合わせる。
4. `usable` が真になってから、`conservative_pass` を評価のゲートに組み込む。

しきい値は初期値で、実データで調整する。LLM の採点は費用と遅延がかかり、同じ回答でも揺れうるので、ゲートの
合否は語による採点を土台にしたままにする。
