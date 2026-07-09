# Real-Document PoC Evaluation Workflow (Operator)

Repeatable workflow to **measure** answer / abstain / error / citation quality on
a customer's **sanitized** documents during a PoC. **No customer data is ever
committed.** Synthetic templates and tooling live in the repo; customer corpora,
question sets, and outputs stay in **gitignored/local** paths only.

## Data handling rules (read first)

- Customer documents, sanitized question sets, and eval outputs go under
  `runs/poc/<customer_alias>/` — `runs/` is gitignored. Never `git add` them.
- Use a customer **alias**, never the real company/tenant name, in any file that
  could be committed.
- Sanitize documents (remove PII / third-party confidential content) before use.
- Nothing customer-specific is committed: templates and tooling only.

## Steps

1. **Prepare** a sanitized canonical chunk JSONL under `runs/poc/<alias>/chunks.jsonl`.
2. **Author the question set** by copying
   `eval/templates/poc_question_set_template.jsonl` to
   `runs/poc/<alias>/questions.jsonl` and filling in sanitized queries. Keep the
   five categories: answerable, citation-required, approved-QA, abstain (vague →
   must abstain), out-of-corpus (must no-answer).
3. **Validate** the set: `python scripts/poc_eval_check.py runs/poc/<alias>/questions.jsonl`
   (checks schema + required categories; warns on PII-looking markers).
4. **Measure** with the existing runner (output to a gitignored path):
   `PYTHONPATH=. .venv/bin/python -m eval.runner --cases runs/poc/<alias>/questions.jsonl
   --chunks-jsonl runs/poc/<alias>/chunks.jsonl --output runs/poc/<alias>/result.json`
5. **Human review** each answer with the scoring sheet below.
6. **Fill the PoC result report** from `docs/reports/poc_result_report_template.md`
   (store the filled copy under `runs/poc/<alias>/` — not committed).

## Human-review scoring sheet (per question)

| Field | Values | Meaning |
| --- | --- | --- |
| answer_correct | yes / partial / no / n-a | for answerable/citation/approved cases |
| cited | yes / no / n-a | a source citation was shown |
| abstained_appropriately | yes / no / n-a | for abstain / out-of-corpus cases |
| notes | free text | reviewer notes (no PII) |

## Metrics to compute

- **First-answer rate** = answerable cases answered (not abstained) / answerable.
- **Answer-correct rate** = `answer_correct=yes` / answerable (human-judged).
- **Citation rate** = `cited=yes` / citation-required.
- **Correct-abstain rate** = `abstained_appropriately=yes` / (abstain +
  out-of-corpus).
- **Error rate** = wrong-but-confident answers / answerable (the key safety
  metric: should be ~0 given abstain-first).

## What this measures vs not

- **Measures:** retrieval + abstain/no-answer behavior and human-judged answer/
  citation correctness on the customer's sanitized corpus.
- **Does NOT guarantee:** accuracy beyond the measured set; production readiness;
  HA/SLA. Report results as measured PoC outcomes, not guarantees.
