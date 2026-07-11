# Approved QA Excel Candidate Import

## 安全境界

この機能は非エンジニア向けXLSXを既存approved QA review workflow用の`draft` candidateへ変換する。production approved QA、vectorstore、Chroma collectionは変更しない。`--dry-run`はoperatorの意図を明示するoptionであり、CLIはdry-run指定の有無にかかわらずcandidate-onlyである。promotion/exportは既存CLIによる別操作である。

XLSX QA管理とXLSX一般文書ingestionは別機能である。本CLIは一般文書をchunk化・ingestしない。

## Template

`templates/approved_qa_import_template.xlsx`には以下のsheetがある。

- `QA入力`: 日本語列、架空の入力例、値validation。
- `入力説明`: 意味、必須/任意、入力例、禁止事項、よくあるエラー。
- `値候補`: 有効値、category、language、status、expected answer modeの候補。

templateは次で再生成できる。

    .venv/bin/python scripts/build_approved_qa_import_template.py

QA入力sheetの例は架空データであり、実データを含まない。Excelのdata validationだけには依存せず、Python側で全rowを再検証する。

## 基本操作

    .venv/bin/python tools/import_approved_qa_excel.py \
      --input "受領 QA（7月）.xlsx" \
      --sheet-name "QA入力" \
      --output-dir artifacts/approved_qa_excel_import/20260710 \
      --dry-run

filenameは固定ではなく、日本語、空白、括弧を含められる。sheet indexはzero-basedである。

    .venv/bin/python tools/import_approved_qa_excel.py \
      --input vendor_questions.xlsx \
      --sheet-index 1 \
      --mapping-file templates/approved_qa_column_mapping.example.json \
      --map "問い合わせ=question" \
      --output-dir artifacts/approved_qa_excel_import/vendor_run \
      --dry-run

主なoption:

- `--input`: 任意名の`.xlsx`。
- `--sheet-name` / `--sheet-index`: 片方だけ指定。未指定時は`QA入力`、なければ先頭sheet。
- `--mapping-file`: JSON object又は`columns` object。
- `--map INPUT_COLUMN=canonical_field`: 複数指定可。
- `--output-dir`: candidate artifactの新規出力先。
- `--dry-run`: production非反映の意図を明示。
- `--strict`: warningが1件でもfailure status/exit 1。
- `--max-rows`、`--max-file-size-mb`: default上限をさらに制限。
- `--existing-approved-qa`: 読み取り専用衝突比較先。defaultは`data/approved_qa/default.jsonl`。
- `--corpus-jsonl`: 任意。source/page/required termの存在確認に使うcanonical corpus。

## Excel contract

必須canonical fieldは`question`、`approved_answer`、`source_doc`である。現在のoperator templateでは`source_pages`と`tenant_id`も各rowで必須とする。`source_doc`と`source_pages`、任意の`title`/`chunk_id`は既存schemaの`approved_citations` 1要素へ変換する。

| Excel列 | 出力 |
|---|---|
| 質問 | `question`、既存normalizerによる`normalized_question` |
| 正解回答 | `approved_answer` |
| 出典文書/ページ/タイトル | `approved_citations[0]` |
| 分類 | `tags`（既存schema内） |
| テナント | `tenant_id` |
| 文書版 | `doc_version` |
| 有効、言い換え、除外質問、必須キーワード | `candidate_metadata`。production runtime schemaへ追加しない |
| 備考 | `notes` |

全出力recordの`status`は`draft`である。XLSXで`approved`を指定するとerrorになる。

## Column mapping

解決優先順位は次の通りで、曖昧なfuzzy推定をしない。

1. CLI `--map`
2. mapping JSON
3. canonical fieldとの完全一致
4. 定義済み日本語alias

日本語aliasには`質問`、`問い`、`QA質問`、`正解`、`回答`、`正解回答`、`出典`、`出典文書`、`文書名`、`ページ`、`頁`、`出典ページ`、`分類`、`カテゴリ`、`テナント`、`文書版`、`有効`、`使用`、`言い換え`、`別表現`、`除外質問`、`不正解質問`、`必須語`、`必須キーワード`、`備考`を含む。同じ入力列又はcanonical fieldが多重解決されるmappingはerrorになる。

## Validation and security

row validation:

- question/answer/tenant/source document/page/enabled/status。
- malformed list/JSON、巨大cell、formula/formula-like cell、null byte、control character。
- absolute path、path traversal。
- active HTML/script疑い、secret-like pattern。
- alias/termの件数上限。

file/cross-row validation:

- duplicate QA ID、question、normalized question、alias。
- aliasと別question、aliasと既存approved QAの衝突。
- existing QA ID/question、同一tenantのanswer conflict。
- column mapping conflict、required column不足。
- file size、worksheet数、row数、column数。
- macro形式、`.xls`、`.xlsm`、`.ods`の拒否。

corpus validation:

- `--corpus-jsonl`指定時はsource document、page、required termを検査する。
- 未指定時は検証不能をwarningにし、passedとみなしたという表現はしない。

default limitsはfile 20 MiB、worksheet 20、row 10,000、column 80、cell 20,000文字、alias 20、term 30である。

## Deterministic candidate ID

QA ID未指定時は、次のUTF-8 payloadをSHA-256でdigestし、先頭16 hexを`qa_`へ連結する。

    tenant_id + newline
    normalized_question + newline
    canonical JSONのsource identity（source_doc/source_pages） + newline
    SHA-256(answer)

Python組み込み`hash()`は使わない。同じ意味入力は同じIDになり、tenant、normalized question、source identity、answerの意味ある差分でIDが変わる。

## Output

- `validation_summary.json`: 件数、status、error/warning数。
- `valid_candidates.jsonl`: 既存`approved_qa_review.py`互換のdraft candidate。
- `invalid_rows.jsonl`: row番号、候補preview、構造化error。
- `warnings.jsonl`: warning一覧。
- `resolved_column_mapping.json`: 実際のmappingと優先順位。
- `import_report.md`: operator向け短縮report。
- `input_manifest.json`: filename、SHA-256、sheet、limits、安全境界。

warningは通常exit 0だが、`--strict`ではexit 1。exit codeは0=passed、1=validation failure、2=CLI/config error、3=unsupported/unreadable input、4=internal processing errorである。

## Review workflowへの接続

candidateを確認する。

    PYTHONPATH=. .venv/bin/python scripts/approved_qa_review.py validate \
      --in artifacts/approved_qa_excel_import/20260710/valid_candidates.jsonl

    PYTHONPATH=. .venv/bin/python scripts/approved_qa_review.py list \
      --in artifacts/approved_qa_excel_import/20260710/valid_candidates.jsonl \
      --status draft

promotion/rejection/exportは既存手順に従う別の明示操作である。本import CLIはそれらを呼び出さない。特に`data/approved_qa/default.jsonl`へ直接出力しないこと。

`candidate_metadata.aliases`はreview情報であり、XLSX importだけではruntime有効にならない。operatorが既存review CLIで`--approve-aliases`を明示した場合だけ、validation後にformal `approved_aliases`へ移る。通常のpromotionではaliasを承認しない。

## 100%正解の定義と未対応範囲

100%正解と表現できる対象は以下に限定する。

- 登録済みapproved question。
- 既存normalizationで一致するquestion。
- 独立したapproved questionとして明示承認・登録されたalias。

自由質問全体の100%正解は保証しない。approved QA専用web管理画面、semantic similarityによるalias自動生成/承認、`.xls`/`.xlsm`/`.ods`、production promote/deploy、vector ingestion、CLI review監査イベントは実装していない。
