# Approved QA Operator Workflow Audit

監査日: 2026-07-10

## 結論

既存のapproved QA回答経路、validator、runner、CSV/JSON/JSONL intake、PDF表変換、canonical JSONLからのcandidate生成、review/promote/reject/export CLI、approved QAからcanonical Q+A pairへの変換はすでに存在する。今回これらは再実装せず、欠けていた非エンジニア向けXLSX candidate intakeだけを追加した。出力は常に`draft` candidateであり、production QA、vectorstore、Chroma collectionを変更しない。

## 既存機能一覧

| 機能 | 状態 | 実装 | 主なテスト |
|---|---|---|---|
| approved QA schema / validator / loader | 実装済み | `rag_core/approved_qa.py` | `tests/test_approved_qa.py` |
| exact matcher | 実装済み | `rag_core/approved_qa.py::lookup_approved_answer`、`webapi/main.py` | `tests/test_approved_qa.py`、`tests/test_no_answer_citations.py`、tenant関連tests |
| question normalization | 実装済み | `rag_core/question_normalization.py` | `tests/test_approved_qa.py`、`tests/test_qa_to_approved_jsonl.py` |
| approved QA runner | 実装済み | `eval/approved_qa_runner.py` | `tests/test_approved_qa.py`、`tests/test_qa_to_approved_jsonl.py` |
| CSV/JSON/JSONL intake | 実装済み | `scripts/qa_to_approved_jsonl.py` | `tests/test_qa_to_approved_jsonl.py` |
| table-style Q&A PDF変換 | 実装済み | `scripts/qanda_table_pdf_to_approved_qa.py` | `tests/test_qanda_table_pdf_to_approved_qa.py` |
| canonical JSONLからdraft candidate | 実装済み | `scripts/canonical_jsonl_to_approved_qa_candidates.py` | `tests/test_canonical_jsonl_to_approved_qa_candidates.py` |
| review/list/validate/promote/reject/export | 実装済み | `scripts/approved_qa_review.py` | `tests/test_approved_qa_review.py` |
| approved QAからcanonical Q+A pair | 実装済み | `scripts/approved_qa_to_canonical_jsonl.py` | `tests/test_approved_qa_to_canonical_jsonl.py` |
| 旧pair chunk converter | 実装済み | `scripts/approved_qa_to_pair_chunks.py` | `tests/test_qa_pair_chunks.py` |
| source/page normalization | 実装済み | `rag_core/source_metadata.py` | `tests/test_source_metadata.py` |
| tenant分離 | 実装済み | approved QA keyは`(tenant_id, normalized_question)` | `tests/test_tenant_isolation.py`、`tests/test_approved_qa.py` |
| candidate/production分離 | 実装済み | candidateは`draft`、production loaderは`approved`のみ。exportは明示CLI | `tests/test_approved_qa_review.py` |
| QA管理画面/API | approved QA専用は未実装 | review queue UI/APIはあるが、approved QA fileの管理UIではない | `tests/test_product_review_actions.py`等 |
| audit event | chat/review queue用は実装済み、approved QA CLI用は未実装 | `rag_core/audit_log.py`、`webapi/main.py`。`approved_qa_review.py`は監査イベントを保存しない | audit関連tests |

## Schemaとcontract

runtimeが読む中心fieldは以下である。

- `qa_id`
- `question`
- `normalized_question`
- `approved_answer`
- `approved_citations`（各要素に最低`source_doc`、任意の`source_pages`、`chunk_id`、`title`等）
- `tenant_id`（未指定時`default`）
- `language`（未指定時`ja`）
- `doc_version`
- `tags`
- `status`

review workflowは加えて`created_at`、`notes`、`reviewed_by`、`reviewed_at`、`review_notes`、`rejection_reason`を保持する。statusは`draft`、`approved`、`rejected`。validatorはapproved recordに非空citationを要求し、同一tenantのnormalized question重複を拒否する。exact lookupのproduction対象は`status=approved`のみである。

## 現在の入力形式

- `scripts/qa_to_approved_jsonl.py`: CSV、JSON、JSONL。
- `scripts/qanda_table_pdf_to_approved_qa.py`: 特定のtable-style PDF。
- `scripts/canonical_jsonl_to_approved_qa_candidates.py`: canonical JSONL。
- `rag_core/document_converters/xlsx_converter.py`: 一般文書ingestion用XLSX。approved QA operator intakeではない。
- `tools/convert_reviewed_qa_to_fixed_cases.py`: 評価用review workbookからfixed casesへの変換。production approved QA intakeではない。
- `tools/generate_pdf_qa_candidates.py`等: review用XLSXの出力。汎用列mapping付きapproved QA intakeではない。

監査前には、任意列名・任意worksheetを持つ非エンジニア作成XLSXをapproved QA candidateへ安全に変換する経路はなかった。

## 現在のcandidate形式

既存candidateは独自queue schemaではなく、approved QA schemaと同形で`status=draft`のJSONLである。`scripts/approved_qa_review.py`がこのJSONLをvalidate/list/promote/rejectし、`export-approved`がapproved recordだけを別ファイルへ出力する。今回の`valid_candidates.jsonl`もこの形式に合わせた。Excel固有fieldは`candidate_metadata`に隔離し、runtime schemaを拡張していない。

## 現在のproduction反映経路

1. candidate JSONLを生成する。
2. `scripts/approved_qa_review.py validate/list`で確認する。
3. reviewerを明示して別出力へ`promote`又は`reject`する。
4. `export-approved`でapproved-only JSONLを明示的に出力する。
5. operatorが別途production deployment pathを選ぶ。

今回のExcel CLIは1までしか行わず、2以降を自動実行しない。`data/approved_qa/default.jsonl`を読み取り専用の衝突比較に使うが、書き込まない。

## 現在の重複検査

既存validator/intake/reviewは、主に同一tenantの`normalized_question`重複を検出する。既存CSV/JSON/JSONL intakeには既存production fileとのcross-file衝突、alias衝突、同一質問のanswer conflictの詳細reportはなかった。今回のXLSX intakeは、candidate内のQA ID、question、normalized question、alias、answer conflictと、既存approved QAのQA ID・question・alias衝突を検査する。

## 現在のalias対応

XLSX intakeは言い換えを`candidate_metadata.aliases`としてレビュー対象に保持し、importだけではruntimeへ反映しない。後続のalias contract実装により、operatorがreview CLIの`--approve-aliases`を明示した場合だけ`approved_aliases`へ昇格し、tenant-scoped exact-equivalent lookupの対象になる。類義語/aliasに関する別のretrieval profile機能はapproved exact QAのalias contractではない。

## 現在のExcel対応

一般文書ingestionや評価用XLSXは存在したが、approved QA operator向けの以下は不足していた。

- 再生成可能な日本語template
- 任意filename / worksheet選択
- 明示column mappingと安全な日本語alias
- file/row/security limits
- invalid/warning report
- existing production QAとの衝突検査
- candidate-only dry-run artifact一式
- 明確なexit code

## 今回実装した範囲

- `.xlsx`限定のcandidate intake CLI。
- 任意filename、`--sheet-name`、zero-based `--sheet-index`。
- CLI、mapping JSON、canonical完全一致、日本語aliasの順による明示mapping。
- 既存normalizer、approved QA validator、draft review JSONL contractの再利用。
- deterministic SHA-256 QA ID。
- row/file/corpus/security validationとerror/warning/info相当の構造化report。
- 既存approved QAとの読み取り専用衝突検査。
- production非変更のcandidate artifact出力。
- 日本語XLSX templateとgenerator。

## 今回実装しない範囲

- exact-match回答route、loader、validator、runnerの再実装又は変更。
- `scripts/approved_qa_to_canonical_jsonl.py`の再実装又は変更。
- production promote、overwrite、deployment。
- vectorstore/Chroma ingestion又はreset。
- `.xls`、`.xlsm`、`.ods`。
- approved QA専用web管理画面/API。
- approved QA CLI review actionの永続audit event。
- semantic similarityからのalias自動生成・自動承認。
- 自由質問全体の100%正解保証。

「100%正解」と表現できる対象は、登録済みapproved question、既存normalizationで一致するquestion、および独立したapproved questionとして明示承認・登録されたaliasに限定される。
