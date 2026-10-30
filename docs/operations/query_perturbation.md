# 質問の揺れ（摂動）と前処理の評価

クエリ前処理（`rag_core/query_preprocess.py`、`QUERY_PREPROCESS_ENABLED`、既定オフ）は以前からある。
「入れたら良くなるのか」を数字で言えるようにするのが 3-1。

## 摂動（`eval/tenant_eval/perturb.py`）

ゴールデン QA の質問を、人が実際に打つ形に機械的に揺らす。規則だけで決まり（モデルなし）、同じ質問・同じ seed なら
いつも同じ変種になる。適用できない種類（英数字が無いのに全角化、など）は変種を作らない。

| 種類 | 例 |
|---|---|
| `fullwidth` | 英数字を全角にする |
| `spacing` | 長い語の途中に空白を入れる |
| `filler` | 「えっと、」「すみません、」などを前に付ける |
| `casual_ending` | 「〜ですか」を「〜なの？」「〜かな？」に、または「〜んだけど教えて」を付ける |
| `typo_swap` | 隣り合う 2 文字を入れ替える（長音符を除く） |
| `typo_drop` | 1 文字落とす |
| `katakana_hiragana` | カタカナ語をひらがなにする |
| `punctuation` | 末尾の「？」を付け外しする |
| `keywords_only` | 重要語だけを空白でつなぐ |

`perturb_cases()` は通常のゴールデンケースを作る。ID は `<元のID>~<種類>`、期待は元と同じ、`group` は元と同じ
（K 分割で元のケースと変種が別の fold に分かれない）、優先度は 0（保護ケースにはしない）、タグは
`perturb:<種類>` と `from:<元のID>`。

## 使い方

```
python scripts/tenant_eval.py evaluate --profile ID --proposal pre1 \
    --knob QUERY_PREPROCESS_ENABLED=true --perturb [--perturb-kinds filler,typo_swap] \
    --report runs/tenant_eval/reports/pre1.md
```

基準（前処理オフ）と候補（オン）を、元の質問と揺らした質問の両方で動かし、ゲートの判定に加えて、レポートに
「質問の揺れへの強さ」の表（種類ごとに、元で合格しているケースが揺らして落ちた数）を載せる。
揺らした質問は評価セットに加わるので、評価セットの ID（ダイジェスト）が変わり、評価回数予算は別に数える。

## 注意

- 変種は元の質問から機械的に作ったもので、実際の利用者の入力ではない。実トラフィックの傾向（本番の質問は監査ログで
  マスクされる）の代わりにはならない。改善の確認と、劣化の検出に使う。
- `QUERY_PREPROCESS_ENABLED` はノブ登録され、評価用に `config` の属性として一時的に上書きされる
  （`qa.py` は属性があれば環境変数より優先して読む）。本番の設定は環境変数のまま。
