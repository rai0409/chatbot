# Unknown Regression Fix Audit

## Scope

This audit covers the unknown abstention regression introduced after the grounded extractive quality fix. The regression affected unknown questions that had related retrieved text but no evidence for the requested answer type.

## Regressed Cases

### unknown_003

- category: missing制度
- question: 令和8年度の同じ事業の予算額はいくらですか。
- pre-fix actual answer_mode: grounded_extractive
- pre-fix actual answer summary: The answer cited IT導入補助金 and 小規模事業者持続化補助金 text from 令和3年度補正予算, plus unrelated 令和5年税額計算 table-of-contents text.
- retrieved evidence summary: Related subsidy evidence existed: 令和3年度補正予算, 補助率, 補助上限額, and software/hardware support descriptions. The retrieved text did not contain 令和8年度, the same business context for that year, or a budget amount for that year.
- why related text is not answer evidence: The question asks for a specific fiscal year and monetary budget amount. Evidence about a different fiscal year or a subsidy ceiling is not evidence for the requested 令和8年度 budget amount.
- fix strategy: Require exact requested fiscal-year support in evidence for specific-year questions, and require selected answer evidence to include a money expression for budget/amount questions. Related subsidy terms alone cannot recover from too_general/soft_distance or pass extractive sufficiency.
- post-fix expected result: fallback / abstain with no citations.
- post-fix observed result: answer_mode=fallback, guard_reason=too_general, used_fallback=true, citations_count=0.

### unknown_022

- category: 文書にない数値
- question: Java Plug-in警告が出たPCは過去に何台ありましたか。
- pre-fix actual answer_mode: grounded_extractive
- pre-fix actual answer summary: The answer cited Java Plug-in警告 display text, 2バイト文字 cause candidates, and .java.policy setup text.
- retrieved evidence summary: Related Java Plug-in warning and PC/user-name evidence existed. The retrieved text described warning causes and remediation, not historical occurrence counts.
- why related text is not answer evidence: The question asks for a count of PCs, expressed by 何台. Evidence that a warning can appear, or why it appears, does not support a historical PC count unless a 台 quantity is present for the same target.
- fix strategy: For 何台/何件/何人/何社 style count questions, require selected evidence to include a matching numeric counter expression. Related terms such as Java Plug-in and PC are not enough.
- post-fix expected result: fallback / abstain with no citations.
- post-fix observed result: answer_mode=fallback, guard_reason=soft_distance, used_fallback=true, citations_count=0.

## Code Fix

- Added answer-type support checks in `rag_core/qa.py`.
- Specific-year questions require the requested year to appear in evidence and in selected answer evidence.
- Budget/amount questions require selected answer evidence with a money expression, and if a year is requested, the money evidence must align with that year.
- Count questions using counters such as 台, 件, 人, 名, 社, 個, 枚 require selected evidence with a matching numeric counter expression.
- The fix is pattern-based and evidence-based; it does not hardcode unknown_003 or unknown_022.

## Validation

- grounded_extractive_quality: 14/14 passed
- exact QA: 118/118
- unknown abstention: 32/32 abstained
- unsupported_answer_count: 0
- normal retrieval: hybrid_hit@5=1.0
- failed_checks: []

## Safety Notes

- Evaluator conditions were not relaxed.
- Unknown gate pass conditions were not relaxed.
- Grounded quality gate pass conditions were not relaxed.
- No vectorstore deletion, collection reset, ingestion reset, production promote, or production overwrite was performed.
