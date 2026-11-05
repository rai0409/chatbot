# 質問の振り分け・解釈の表示・「もっと詳しく調べる」

## 振り分け（`rag_core/query_route.py`）

`classify_query_type` が質問の種類を判定し、種類ごとにハイブリッド検索の 3 つの経路（dense / keyword / exact）の
重みに倍率を掛ける。`QUERY_ROUTING_ENABLED=true` のときだけ（既定 `false`）。

| 種類 | dense | keyword | exact | 狙い |
|---|---|---|---|---|
| `exact_lookup`（引用語・短い語句） | 0.6 | 1.5 | 1.5 | 語句の一致を重く |
| `identifier`（型番・番号） | 0.5 | 1.6 | 1.8 | 識別子の一致を最も重く |
| `faq` | 1.2 | 0.9 | 1.0 | 意味の近さを少し重く |
| `broad`（長い質問） | 1.3 | 0.8 | 1.0 | 意味の近さを重く |
| その他 | 1.0 | 1.0 | 1.0 | 設定どおり |

倍率は**初期値**。`tenant_eval.py evaluate --knob QUERY_ROUTING_ENABLED=true` で顧客のゴールデン QA に対して
評価してから有効にする（倍率の調整はコードの `ROUTES`）。倍率は `retrieval.fusion_scale()` の中の検索にだけ掛かる
（ContextVar。ほかの処理には影響しない）。ベクトルのみのステージング検索には適用しない。

## 解釈の表示（`rag_core/interpretation.py`）

トレースの `interpretation` と、`/chat`・`/chat/stream` の応答の `interpretation`:

```json
{"query_type": "procedure", "type_label": "手順の質問", "intent": "procedure",
 "cleaned_query": "…" , "expansions": ["…"], "variants": ["…"], "route": "identifier", "deep": false,
 "summary": "手順の質問として解釈しました。検索語: …。言い換えも検索: …"}
```

チャット画面は、回答の下に `summary` を小さく表示する。ボットの理解が違っていれば、言い方を変えて聞き直せる。

## もっと詳しく調べる（`deep`）

`ChatRequest.deep=true` のリクエストは、

- 取得件数 `top_k` を 2 倍にする（上限は最大値まで）。
- その 1 回だけ複数クエリ検索（3-2）を有効にする（最低 3 本、最大 5 本）。
- **回答キャッシュを読まず・書かない**（通常の回答と内容が違うため）。

画面は、回答の下に「もっと詳しく調べる」ボタンを出し、同じ質問を `deep=true` で聞き直す。結果の解釈表示には
「（詳しく調べた結果）」が付く。検索回数が増えるため遅延も増える。
