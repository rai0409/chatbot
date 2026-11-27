# 変換精度の測定（eval/conversion）

文書変換（`rag_core/document_converters`: csv / xlsx / docx / pptx / pdf）の出力を、手書きの正解ファイルと比べて数値で評価する。
検索評価（`eval/bench`）より手前の段で、「文書の中身が正しくチャンクに入ったか」を測る。

## 1. 指標（文書ごと）

| 指標 | 意味 | 良い向き |
|---|---|---|
| `text.cer` | 文字誤り率 = 編集距離 / 正解文字数（NFKC・空白除去後。表以外の本文） | 小 |
| `text.normalized_levenshtein_similarity` | 1 − 編集距離 / max(正解長, 出力長) | 大 |
| `text.char_accuracy` | `libs/docextract` の `char_accuracy`（OCR比較と同じ定義。読めなければ同式の自前実装） | 大 |
| `reading_order.kendall_tau` | 正解の本文ブロック順と、出力中の出現位置の順の Kendall τ（見つからないブロックは `blocks_missing`） | 大 |
| `table.precision / recall / f1` | 表のセルを（列見出し, 値）の組で比べた多重集合 P/R/F1。結合セルは左上の値を覆う全セルに展開（変換器と同じ扱い）、複数行見出しは「上限額 国内」のように空白で連結 | 大 |
| `table.value_only_f1` | 値だけで比べた F1。`f1` との差 = 値は取れたが列見出しを誤った分 | 大 |
| `headings.precision / recall` | 見出し（docx の見出し・pptx のスライドタイトル・表題）の集合比較 | 大 |
| `typed_cells.exact_match_rate` | 数値・日付・和暦・数式セルが **表示どおり完全一致** した率（strip のみ、正規化しない） | 大 |
| `header_footer_leakage.rate` | ヘッダー/フッター文字列を含む本文チャンクの割合（`header_footer` 種別のチャンクは対象外） | 小（0） |
| `excluded_text_leakage.rate` | 出てはいけない文字列（ふりがな等）を含むチャンクの割合 | 小（0） |
| `warnings.recall` | 期待する変換警告（例: `formula_without_cached_value`）が出た率 | 大 |
| `encoding.correct` | CSV の文字コード判定が正解と一致したか | 大 |

正解ファイルに無い項目は `null` になり、集計（平均）から外れる。

## 2. 正解ファイル（`<文書名>.truth.json`, schema `conversion-truth-v1`）

書式は `eval/conversion/harness.py` の冒頭に記載。要点:

- `text_blocks`: 表以外の本文を **読む順** に（見出しも含める）。ラベル（`ノート:` `グラフ:` `系列:` `項目:` `画像内テキスト:`）は比較前に除くので書かない
- `tables[].cells` は人が見たままの格子、`merged` は 0 始まりの結合範囲、`header_rows` は列見出しの行数、`title_rows` は表の上の表題行数
- 数式で再計算前の値など、変換器が原理的に出せない値も **人が見る値** を書く（再現率の上限として正直に残す）
- 実在の人名・社名・連絡先は書かない（固定例: 山田太郎 / 株式会社サンプル / @example.co.jp / 03-1234-5678）

## 3. 実行

```bash
# フィクスチャの再生成（標準ライブラリのみ。バイト列は再現可能で、テストが一致を確認する）
.venv/bin/python tools/make_dummy_docs.py --conversion-fixtures

# 測定（出力 eval/conversion/out/ は git 管理外）
.venv/bin/python -m eval.conversion run --fixtures tests/fixtures/conversion \
    --out eval/conversion/out/run.json --markdown eval/conversion/out/run.md \
    --thresholds eval/conversion/thresholds_v1.json      # 閾値違反で exit 1

# 変更前のコードを同じフィクスチャで測る（別プロセスで。--root は rag_core を含むツリー。
# 展開先はリポジトリ内の git 管理外 eval/conversion/out/ に置き、済んだら 即削除/ へ移動）
mkdir -p eval/conversion/out/base_tree && git archive <base-commit> | tar -x -C eval/conversion/out/base_tree
.venv/bin/python -m eval.conversion run --fixtures tests/fixtures/conversion \
    --root eval/conversion/out/base_tree --out eval/conversion/out/base.json

# 比較（文書×指標の差分、劣化の列挙。--fail-on-regression で劣化時 exit 1）
.venv/bin/python -m eval.conversion compare --base eval/conversion/out/base.json \
    --candidate eval/conversion/out/run.json --out eval/conversion/out/compare.json --fail-on-regression
```

出力 `conversion-run-v1` には git commit、変換器モジュールごとの sha256（変換器にはバージョン文字列が無いため）、
フィクスチャと正解ファイルの sha256（`fixtures_sha256`）を記録する。`compare` は `fixtures_sha256` が異なると警告する
（違うフィクスチャ同士の差は変換器の差ではない）。

## 4. 本番相当文書での測定（本番PCで実施）

リポジトリ内のフィクスチャは境界条件の回帰検知用で、精度の代表値ではない。実際の精度は顧客文書で測る:

1. 顧客文書から 20〜50 件を層別抽出（形式 × 作成元。表の多い Excel、段組みの Word、図の多い PowerPoint、スキャン PDF）
2. 各文書の `*.truth.json` を人手で作る（表は全セル、本文は代表 2〜3 ページで可）。**顧客データなのでリポジトリにコミットしない**
   （`data/extraction/` など git 管理外に置き、不要になったら `即削除/` へ移動）
3. `python -m eval.conversion run --fixtures <そのディレクトリ> --out eval/conversion/out/<日付>_customer.json`
4. 変換器を変えたら同じディレクトリで再測定し `compare` で差を確認。劣化した文書は `missing_examples` /
   `blocks_missing` / `misses` で原因を確認する

## 5. 2026-11-22 時点の結果（tests/fixtures/conversion, 7 文書）

| 指標（平均） | 修正前 | 修正後 |
|---|---|---|
| text.cer | 0.302 | 0.000 |
| reading_order.kendall_tau | 0.643 | 1.000 |
| table.f1 | 0.719 | 0.995 |
| headings.recall | 0.667 | 1.000 |
| typed_cells.exact_match_rate | 0.700 | 1.000 |
| excluded_text_leakage.rate | 0.500 | 0.000 |
| warnings.recall | 0.000 | 1.000 |
| 変換失敗（UTF-16 BOM なし CSV） | 2 | 0 |

修正前は UTF-16（BOMなし）CSV 2件が変換エラーのため平均から外れている。修正後の table.f1 が 1 でないのは、
キャッシュ値の無い数式セル（book.xlsx E4）の値が原理的に取れないため（警告で検知する）。

修正内容: pptx の位置順読み（行の許容幅 `PPTX_ROW_TOLERANCE_EMU`）・グループ図形の座標変換・グラフ/SmartArt の文字、
xlsx の和暦書式（令和/平成、`ggge` `ge.m.d` 等）・ふりがな（rPh）除外・キャッシュ値の無い数式の警告
（チャンクの `conversion_warnings`）・全列結合の表題行を見出しと誤認しない、csv の BOM なし UTF-16 LE/BE 判定、
docx の複数行見出し（`w:tblHeader` / 列グループの横結合 / 下への縦結合）と全幅1セルの表題行。

## 6. 既知の限界

- docx のヘッダー/フッターは本文には混ざらず、`header_footer` チャンクとして保存されるが、既定では検索対象外（`searchable=0`、
  `chunk_role=header_footer`）。`DOCX_HEADER_FOOTER_SEARCHABLE=true` で検索対象に戻せる。ヘッダー/フッターの文書番号・版・施行日・
  機密区分は、同じ文書の全本文チャンクのメタデータ（`doc_number` `revision` `effective_from` `confidentiality`）に入る
- 本文の位置特定は完全一致（無ければ先頭 12 文字）。大きく崩れたブロックは順序評価から外れ `blocks_missing` に出る
- 編集距離は純 Python の O(n·m)。数万字の文書では遅いので、本文は代表ページに絞って正解を作る
- PDF は正解ファイルを置けば同じ指標で測れるが、リポジトリ内に PDF フィクスチャは無い（reportlab が必要）
