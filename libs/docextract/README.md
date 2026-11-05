# docextract

PDF の読み取り・表の抽出・日本語 OCR を、ひとつの小さな API にまとめたライブラリです。どのバックエンドも任意で、実行時に入っているものを使うので、ほかのリポジトリにもそのまま持っていけます（依存はゼロ。必要な extras だけ入れる）。**PyMuPDF（AGPL-3.0）は使いません。**

```python
from docextract import extract_pdf_tables, ocr_image, open_pdf

with open_pdf("manual.pdf") as doc:                 # pdfplumber + pypdfium2
    for page in doc.pages:
        tables = page.tables()                       # 罫線の表、無ければ罫線なしの推定
        text = page.text(exclude=[t.regions[0].bbox for t in tables])
        image = page.render(dpi=300)                 # OCR 用の画像

found = extract_pdf_tables("manual.pdf")            # {"backend", "tables", "errors"}
result = ocr_image(open("scan.png", "rb").read())   # EMF/WMF も可（変換ツールがあれば）
print(result.engine, result.confidence, result.text)
```

- `python -m docextract check`: 入っているもの・版・ライセンスの注意（`--require-ocr`／`--require-tables` で、無ければ終了コード 1）
- `python -m docextract compare-ocr 画像... [--expected 正解.txt]`: OCR エンジン・PaddleOCR の版（既定 PP-OCRv6 と PP-OCRv5）を、自社の画像で文字正解率・確信度・時間で比べる

## 選び方（2026-10 時点。自社の日本語文書で必ず比較すること）

| 用途 | 推奨 | ライセンス | 備考 |
|---|---|---|---|
| PDF の本文・単語位置・罫線・画像位置 | pdfplumber（pdfminer.six） | MIT | 和文の字間の空白を除去、縦書きは位置から判定して上→下・右→左で読む |
| PDF の頁画像（OCR 用） | pypdfium2 | Apache-2.0／BSD-3 | pdfplumber が依存しているので追加不要 |
| 表（構造の正確さ重視・罫線なし・結合セル・スキャン） | Docling（TableFormer ACCURATE） | MIT | PyTorch とモデル（約500MB）。CPU では遅い。スキャンの表は `DOCEXTRACT_DOCLING_OCR=tesseract` |
| 表（軽量） | pdfplumber | MIT | 罫線の表＋罫線なしの推定（妥当性の点数付き） |
| 日本語 OCR（最良・許容的ライセンス） | PaddleOCR 3.7＋ PP-OCRv6（medium） | Apache-2.0 | 公開値（日本語）: 認識 90.5（PP-OCRv5_server は 73.7）、検出 84.3（同 77.2）。paddleocr < 3.7 なら自動で PP-OCRv5 |
| 日本語 OCR（軽量・代替） | Tesseract 5（jpn＋eng、縦書きは jpn_vert） | Apache-2.0 | 精度は下がる。OS パッケージ |
| 日本語の表＋OCR（精度最上位候補） | YomiToku | CC BY-NC-SA 4.0 | **業務利用は商用ライセンスが必要**。取得した場合のみ `DOCEXTRACT_YOMITOKU_LICENSED=true` |
| スキャン PDF の表（Apache 系で） | PaddleOCR PP-StructureV3 | Apache-2.0 | 表の位置を返さないので、表の文字は本文にも残る |

ライセンスの記載は要約です。配布前に各配布物のライセンスファイルとモデルカードを必ず確認してください。

推奨構成:
- 標準（製品向け）: 表 `docling`、OCR `paddle,tesseract`（`pip install -e "libs/docextract[light,docling,paddle]"`）
- 軽量: 表 `pdfplumber`、OCR `tesseract`（`pip install -e "libs/docextract[light]"`）

PP-OCRv6 と PP-OCRv5（このライブラリの開発時に、合成した和文画像で確認した値。参考値）:
きれいな画像では両方とも文字正解率 1.0。ぼかし・雑音を加えた小さい文字では PP-OCRv6 1.0／PP-OCRv5 0.943。CPU での時間は PP-OCRv6 が約半分（oneDNN 無効時）。

## 設定（環境変数）

| 変数 | 既定 | 意味 |
|---|---|---|
| DOCEXTRACT_TABLE_BACKEND | auto | docling／pdfplumber／paddle／yomitoku／none |
| DOCEXTRACT_TABLE_PREFERENCE | docling,pdfplumber | auto の優先順。実行時に失敗したら次へ |
| DOCEXTRACT_BORDERLESS_TABLES | true | 罫線の無い表を文字の並びから推定 |
| DOCEXTRACT_TABLE_MIN_CONFIDENCE | 0.65 | 罫線なしの推定をこの点数未満なら捨てる |
| DOCEXTRACT_MERGE_CONTINUED_TABLES | true | 頁をまたぐ表をつなぐ |
| DOCEXTRACT_X_TOLERANCE_RATIO | 0.25 | 文字の間隔（文字幅比）がこれを超えたら語を区切る |
| DOCEXTRACT_VERTICAL_RATIO | 0.5 | 縦に並ぶ文字の割合がこれ以上なら縦書きとして読む |
| DOCEXTRACT_DOCLING_MODE／DOCEXTRACT_DOCLING_OCR | accurate／none | Docling の精度とスキャン頁の OCR |
| DOCEXTRACT_OCR_ENGINES | paddle,tesseract | 試す順 |
| DOCEXTRACT_OCR_ACCEPT_CONFIDENCE | 60 | これ未満なら次のエンジンも試し、確信度の高い方を使う |
| DOCEXTRACT_PADDLE_OCR_VERSION | PP-OCRv6 | PP-OCRv5 などを明示する時 |
| DOCEXTRACT_PADDLE_TIER | medium | small で CPU 約3倍速（精度は少し下がる。tiny は日本語非対応） |
| DOCEXTRACT_PADDLE_DET_MODEL_DIR／_REC_MODEL_DIR | なし | オフライン環境でモデルを置いたフォルダ |
| DOCEXTRACT_PADDLE_MKLDNN | auto | paddlepaddle 3.3 の CPU で既知の oneDNN エラーが出たら自動で無効化 |
| DOCEXTRACT_PADDLE_ENGINE | なし | onnxruntime など（PaddleOCR 3.7 の推論エンジン指定） |
| DOCEXTRACT_TESSERACT_LANG／_PSM | jpn+eng／3 | Tesseract の言語とページ分割 |
| DOCEXTRACT_VERTICAL_RETRY_BELOW | 55 | Tesseract の横書きの確信度がこれ未満なら jpn_vert でも読む |
| DOCEXTRACT_YOMITOKU_LICENSED | false | 商用ライセンスを持つ場合のみ true |
| DOCEXTRACT_DEVICE | なし | cpu／gpu:0 など |
| DOCEXTRACT_RENDER_DPI | 200 | 画像系の表バックエンドの頁描画解像度 |
| DOCEXTRACT_SOFFICE／_INKSCAPE／_CONVERT_TIMEOUT_SECONDS／_TMP_DIR | | EMF/WMF の変換 |

PaddleOCR はモデルを初回にダウンロードします（既定の保存先はホームの .paddlex）。保存先は `PADDLE_PDX_CACHE_HOME` で変えられます。オフライン環境では事前に取得して `DOCEXTRACT_PADDLE_*_MODEL_DIR` を指定してください。

## ほかのリポジトリで使う

1. `libs/docextract` をコピー（または git submodule）
2. `pip install -e "libs/docextract[light]"`（用途に応じて `[docling]`／`[paddle]`）
3. OS 側: Tesseract を使うなら `tesseract-ocr`、`-jpn`、`-jpn-vert`。EMF/WMF を読むなら LibreOffice
4. `python -m docextract check` で確認、`compare-ocr` で自社文書での精度を確認
5. テスト用の `docextract.testing.simple_pdf()`（依存なしで小さな PDF を作る）も使える

## 制限（事実）

- pdfplumber は PyMuPDF より遅い（純 Python）。大量取り込みは並列に流す
- 縦書きの判定は文字位置による推定。PP-OCRv6 の縦書き性能は公開情報で確認できていない（縦書きの多い文書は Tesseract jpn_vert とも比較する）
- 罫線なしの表の推定は誤検出を避けるため保守的
- PP-StructureV3 は表の位置を返さないため、本文側から表の文字を除けない
- YomiToku・PaddleOCR・Docling の API は版で変わることがある。版を固定して使う
