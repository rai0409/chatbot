# Approved QA Alias Contract Audit

監査日: 2026-07-10

## 結論

既存approved QA runtimeは`rag_core/approved_qa.py`のJSONL loader/indexを最優先で参照し、`(tenant_id, normalized_question)`によるcanonical question lookupを行う。XLSX intakeの言い換えは`candidate_metadata.aliases`に保存される一方、loader dataclass/indexにはそのfieldがなく、review/exportもformal runtime fieldへ昇格しなかったためruntimeへ届かなかった。

最小変更範囲は、既存schema validator/loader/indexへの後方互換`approved_aliases`追加、review時の明示承認、既存chat payload/auditのmatch種別追加、独立alias runnerである。semantic similarity、embedding、approved-similar candidate、normal RAG、Q+A canonical converterは変更対象ではない。

## Exact matchの現在の呼出順

通常`/chat`と`/chat/stream`:

1. request tenantを既存contractでnormalizeし、tenant authorizationを検査する。
2. staging collection指定時はapproved QA bypassを維持する。
3. `webapi.main._approved_qa_lookup`が`APPROVED_QA_PATH`をtenant込みcache keyでloadする。
4. `rag_core.approved_qa.load_approved_qa`が全recordをvalidateし、指定tenantの`status=approved`だけindex化する。
5. `lookup_approved_answer`がcanonical question index、formal alias indexの順で検索する。
6. match時はapproved answer/citationsをそのまま返し、LLM/retrievalへ進まない。
7. no match時だけ、既存artifact exact index fallback、その後の通常RAG処理へ進む。

product previewも最初に`_approved_qa_lookup`を行い、no match時だけapproved-similar candidate検索へ進む。

## Normalizationの現在の仕様

`rag_core/question_normalization.py::normalize_question_for_exact_match`は次だけを行う。

- Unicode NFKC。
- 全角空白、全角疑問符/感嘆符、引用符等の安定した表層差の変換。
- 連続whitespaceの単一space化とtrim。
- 一部句読点・括弧周囲spaceの除去。

語彙置換、stemming、semantic similarity、数値・年度・entityの同一視はしない。aliasも同じnormalizerだけを使う。

## Tenant分離

index keyはcanonical/aliasとも`(tenant_id, normalized string)`である。loader自体も指定tenant以外のrecordをindex化しない。異なるtenantで同一aliasを登録できるが、lookup時にtenantをまたがない。cache keyも`path::tenant`である。

## Approved QA index構造

- `records`: 指定tenantのapproved `ApprovedAnswer` tuple。
- `by_tenant_question`: normalized canonical questionからrecord。
- `by_tenant_alias`: normalized formal aliasから `(record, original alias)`。

lookupはcanonical indexを先に参照する。異なるQAのcanonicalとaliasが同一keyになる入力はvalidatorが拒否するため、正常なfileでは曖昧状態を作れない。

## Candidate metadataの扱い

XLSX intakeは引き続き言い換えを`candidate_metadata.aliases`にだけ保存し、`approved_aliases`を生成しない。review listはcandidate/formal aliasの件数と内容を表示する。

通常の`promote`ではaliasはformal化されない。operatorが`--approve-aliases`を明示したpromotionだけがcandidate aliasを`approved_aliases`へcopyし、その後に全record conflict validationを再実行する。`export-approved`はapproved recordだけを出力し、operator用`candidate_metadata`をproduction exportから除去する。

## Aliasesがruntimeへ届かなかった理由

変更前は次の理由でruntimeへ届かなかった。

- `ApprovedAnswer`にalias fieldがなかった。
- `ApprovedQAIndex`にalias indexがなかった。
- lookupがcanonical question mapしか参照しなかった。
- candidate metadataはruntime schemaではなく、export時にもformal化されなかった。
- chat payloadにalias match種別がなかった。

## 変更した最小範囲

- `rag_core/approved_qa.py`: schema validation、formal alias読込、tenant-scoped alias index、canonical-first lookup。
- `scripts/approved_qa_review.py`: alias表示、明示承認option、candidate metadata除去export。
- `webapi/main.py`とproduct contract: alias answer mode/payload/audit。
- XLSX importer: existing formal alias conflictとreport count。
- 独立fixture/runner/tests/docs。

## 変更してはいけない範囲

- approved answer本文/citationsの生成・書換え。
- semantic similarity routing、embedding threshold、approved-similar自動回答。
- normal RAG/unknown/grounded evaluatorの緩和。
- canonical Q+A pair converterの再実装。
- production approved QA file、vectorstore、Chroma collection、ingestion/reset。
