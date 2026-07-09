# Prompt057: Real-Customer-Document PoC Evaluation Workflow

Implementation report. Adds a repeatable workflow (templates + tooling) to
**measure** answer/abstain/error/citation quality on a customer's **sanitized**
documents during a PoC. **No customer data is committed** — templates + tooling
only.

## Implementation summary

- `eval/templates/poc_question_set_template.jsonl` (new) — synthetic placeholder
  question set in the eval schema, covering all five categories (answerable,
  citation-required, approved-QA, abstain/too_general, out-of-corpus) with
  reviewer fields.
- `scripts/poc_eval_check.py` (new, executable) — validates a question set against
  the schema, requires the key categories, and warns on PII-looking markers.
  Reads no `.env`, no network, no Docker; prints no secrets.
- `docs/operations/real_document_poc_evaluation_workflow.md` (new) — the operator
  procedure: sanitized corpus under gitignored `runs/poc/<alias>/`, author from
  the template, validate, measure with `eval.runner`, human-review scoring sheet,
  metrics (first-answer / answer-correct / citation / correct-abstain / error),
  and strict data-handling rules.
- `docs/reports/poc_result_report_template.md` (new) — metrics table + limitations
  + reviewer notes template; marked "fill with real measured data; do not
  fabricate".
- `tests/test_poc_evaluation_workflow.py` (new).

## Safety / no-secret / no-customer-data result

- Templates are clearly synthetic placeholders (`<...>` / `TEMPLATE`); customer
  documents/question-sets/outputs are mandated to live under gitignored
  `runs/poc/<alias>/` using an alias (never the real name). No secrets in
  templates/docs (scanned). The check script warns on unsanitized PII markers.

## Verification results

- `tests/test_poc_evaluation_workflow.py`: **5 passed**. `--collect-only`:
  **850 collected**. `product_readiness_smoke.sh` exit 0. Full suite **not run**
  for this prompt (docs/templates + isolated new test; no product source change),
  but the new test + collection + smoke confirm no breakage.

## What was not measured / externally validated

- No real customer documents were used or committed; **actual PoC accuracy is
  measured at the customer using this workflow**, not here. The workflow itself
  is validated on synthetic templates only.

## Deliverable paths

`eval/templates/poc_question_set_template.jsonl`, `scripts/poc_eval_check.py`,
`docs/operations/real_document_poc_evaluation_workflow.md`,
`docs/reports/poc_result_report_template.md`,
`tests/test_poc_evaluation_workflow.py`, this report.

## Git diff summary

5 new files + this report. No product source/runtime change; no new dependencies;
orphans untouched.

## Final judgment: PASS

## Next recommendation

Prompt058 — real-IdP SSO end-to-end validation workflow (Entra/Okta).
