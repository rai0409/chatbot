# PoC Result Report — TEMPLATE (fill with real measured data; do not fabricate)

> Store the filled copy under a gitignored path (`runs/poc/<alias>/`). Use a
> customer **alias**, never the real name. No PII / raw document text here.

- Customer alias: `<ALIAS>`
- Department / scope: `<DEPT>`
- Corpus size: `<N documents / N chunks>` (sanitized)
- Question set size: `<N questions>` (by category)
- Dates: `<START> – <END>`
- Deployment: on-prem / closed network; `production_safe` profile

## Measured metrics (from the eval run + human review)

| Metric | Value | Denominator | Notes |
| --- | --- | --- | --- |
| First-answer rate | `<x/y>` | answerable | not abstained |
| Answer-correct rate | `<x/y>` | answerable | human-judged |
| Citation rate | `<x/y>` | citation-required | source shown |
| Correct-abstain rate | `<x/y>` | abstain + out-of-corpus | abstained appropriately |
| Error rate (wrong-but-confident) | `<x/y>` | answerable | safety-critical; target ~0 |

## Limitations (state honestly)

- Measured on a sanitized subset; not a guarantee beyond it.
- Generated-answer wording judged by `<reviewer>`; no external benchmark.
- `<other corpus/coverage caveats>`.

## Reviewer notes

- `<free-text observations; no PII>`

## Incidents / issues during the PoC

- `<none / list>`

## Recommendation

- `<continue / adjust corpus / not yet>` — with reasons.
