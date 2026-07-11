# Approved QA Alias Contract

## 目的

approved aliasは、operatorが内容を確認して明示承認した、canonical approved questionのexact-equivalent表現である。semantic similarity候補ではない。登録されていない似た質問からapproved answerを自動選択する用途には使わない。

## Exact question、alias、semantic similarityの違い

- exact question: approved recordの`question`を既存normalizationしたkey。
- approved alias: `approved_aliases`へ明示承認された文字列を同じnormalizationでkey化したもの。
- semantic similar candidate: embedding/keyword等で近い候補。approved answerの自動返却には使わない。
- normal RAG question: exact-equivalent matchがなく、通常retrieval/groundingへ進む質問。
- unknown question:十分なevidenceがなくabstainする質問。

## 正式schema

既存approved QA recordへ任意fieldを追加する。

    "approved_aliases": ["明示承認された別表現", "別の承認表現"]

contract:

- `list[str]`、最大20件。
- 1alias最大500文字。
- 空、null byte、control characterは禁止。
- canonical questionとnormalize後同一は禁止。
- record内raw/normalized duplicateは禁止。
- 同一tenantの他QA canonical question/aliasとの衝突は禁止。
- 同じmatch keyでanswerが異なる状態は禁止。
- formal aliasは`status=approved`だけに保存できる。
- fieldがない既存recordはaliasなしとして後方互換で読む。

## Normalization

aliasはcanonical questionと同じ`normalize_question_for_exact_match`だけを使用する。NFKC、安定した句読点/whitespace差は吸収するが、語彙、数値、年度、entity、肯定/否定を自動変換しない。

## Conflict rulesとtenant isolation

collisionはwarningではなくvalidator errorである。同一tenant内ではalias対canonical、alias対alias、normalized duplicateを拒否する。異なるtenantの同一aliasは許可するが、index/lookup/cacheはtenant scopedである。

数値、年度、人名/会社名、肯定/否定、開始/取消、利用可能/利用不可が異なる表現は自動aliasにしない。明示登録されていなければno approved matchである。

## Review and approval workflow

1. XLSX dry-runが`candidate_metadata.aliases`を持つ`draft` candidateを生成する。
2. operatorがalias件数/内容をlistで確認する。
3. aliasを承認しない通常promotionではformal fieldを作らない。
4. aliasを承認する場合だけ`--approve-aliases`を明示する。
5. promotion時に全record conflict validationを再実行する。
6. `export-approved`がapproved recordだけを別fileへ出力し、candidate metadataを除去する。

確認例:

    PYTHONPATH=. .venv/bin/python scripts/approved_qa_review.py list \
      --in artifacts/approved_qa_excel_import/run/valid_candidates.jsonl \
      --status draft

明示承認例（production pathを指定しない）:

    PYTHONPATH=. .venv/bin/python scripts/approved_qa_review.py promote \
      --in artifacts/approved_qa_excel_import/run/valid_candidates.jsonl \
      --out /tmp/approved_qa_reviewed.jsonl \
      --qa-id qa_example \
      --reviewer operator-id \
      --approve-aliases

`promote-all --approve-aliases`も存在するが、全aliasを個別確認できる場合に限る。いずれも入力fileのin-place変更やproduction fileへの直接出力を避ける。

## XLSX candidateとの接続

XLSX importだけではruntime aliasは有効にならない。importはcandidate metadataを生成し、existing productionのformal canonical/aliasとのcollisionを検査し、alias candidate件数とcollision件数をreportする。正式fieldへの移行はreview CLIの明示optionだけが行う。

## Runtime lookup order

1. approved canonical question exact/normalized match。
2. approved formal alias exact/normalized match。
3. no approved exact-equivalent match。

alias no-matchからsemantic searchへ自動昇格する新処理は追加していない。既存normal RAG flowは従来通りno-match後に動く。

## Answer modeとresponse

canonical matchは`approved_exact_match`、alias matchは`approved_alias_match`。alias responseは既存approved answer/citationsを完全保持し、`approved_qa_id`、`canonical_approved_question`、`matched_alias`、`retrieval_required=false`、`llm_used=false`を含む。alias文字列をanswerへ混ぜず、LLMで書き換えない。

## Audit fields

既存audit policyのbounded JSONLに、request/trace/tenant、入力質問、normalized input、answer mode、QA ID、matched alias、canonical question、citation count、`retrieval_required=false`、`llm_used=false`を残す。秘密情報fieldや新たなsource本文保存は追加しない。

## Evaluation gate

`eval/approved_qa_alias_runner.py`は既存exact件数と分離して次を測定する。

- alias QA total/passed/failed/pass rate。
- false positive、tenant isolation failure。
- canonical answer/citation mismatch。
- validator collision rejection。
- LLM/retrieval未使用trace。

実行例:

    PYTHONPATH=. .venv/bin/python -m eval.approved_qa_alias_runner \
      --fixture eval/cases/approved_qa_alias_fixture.json \
      --output-dir artifacts/approved_qa_alias

existing exact 118件は従来gateで独立測定し、alias fixture件数へ混ぜない。

## 100%正解の定義

100%保証は次に限定する。

- 登録済みapproved question。
- 既存normalizationで一致するapproved question。
- operatorが明示承認し、validation済み`approved_aliases`へ登録したalias。

未登録の自由質問、semantic similarityだけで近い質問、曖昧質問、複数QA候補、evidence不足、別tenant、数値/日付/entity/肯定否定が異なる質問は保証しない。

## Rollback

alias deployment前のreviewed/export fileを保持する。rollbackは、候補側の`approved_aliases`を除去してvalidator/runnerを再実行し、既存の別file export/deployment手順へ戻す。runtime codeはfieldなしrecordに後方互換なため、旧approved QA fileへ戻すだけでalias lookupは停止する。vectorstore/collection変更やresetは不要である。
