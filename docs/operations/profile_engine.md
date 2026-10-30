# プロファイルの検索ブースト規則（profile_engine）

顧客プロファイルの `retrieval_boost_rules.json` を、検索結果の並べ替えに使う。
`PROFILE_BOOST_ENABLED=true` のときだけ動く（既定 `false`。プロファイルを評価してから有効にする）。

## 動き

1. 規則が適用されるのは、`applies_to_question_type` が質問タイプと同じで、`when_question_contains_any` が
   空でないときは質問にその語のどれかが含まれるとき。`enabled: false` の規則は無視する。
2. 規則の語 = `positive_terms` / `negative_terms` ＋ `positive_categories` / `negative_categories` に属する
   有効な `domain_terms` の `term` と `aliases`。
3. チャンク本文（NFKC 正規化・大文字小文字無視）にプラスの語が 1 つでもあれば `+|positive_weight|`、
   マイナスの語が 1 つでもあれば `-|negative_weight|` の点数。規則ごと・向きごとに 1 回だけ数え、全規則の点数を足す。
4. スコア（距離。小さいほど良い）を `score -= PROFILE_BOOST_MAX_DELTA × clamp(点数 / 100, -1, 1)` で動かし、
   安定ソートで並べ直す。1 つの設定で全規則の影響の上限が決まる。
5. 位置は `rerank_chunks` とキーワードブーストの後、クロスエンコーダの前。プロファイルの読み込みに警告が
   ある場合は、回答検証と同じく適用しない。

トレースは `staged_retrieval.profile_boost`（適用した規則・動かした件数）、チャンクの
`score_details.profile_boost`（規則・点数・変化量）。回答キャッシュのキーに設定が含まれる。

## 注意

- 重みは 0〜100 の目盛りとして扱う（デモの +35 / −25 / +40 など）。`PROFILE_BOOST_MAX_DELTA` の初期値 0.05 は
  既存のキーワードブーストと同じで、実データの評価（2-4 以降）で調整する。
- `domain_terms` の `weight` / `negative_weight` は使わない（規則側の重みだけ）。
- 検証規則（`validation_rules.json`）は従来から回答生成後に実行されている。今回、`enabled: false` の規則が
  適用されていた不具合を直した。
