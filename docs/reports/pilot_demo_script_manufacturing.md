# KuraDen 蔵伝 — Manufacturing Pilot Demo Script (Synthetic)

Reproducible demo for a manufacturing one-department PoC using the existing
`/chat-ui`. **Synthetic data only — never load real customer documents in a
demo.** Clearly fictional content: 架空精機 / 装置X.

## 0. Pre-demo setup (operator)

- Build the synthetic corpus + cases:
  `python scripts/build_manufacturing_pilot_pack.py`
  (writes `eval/cases/manufacturing_pilot/manufacturing_{chunks,cases}.jsonl`).
- Validate them (no ingest) into an explicit **non-production** collection via
  the admin ingestion dry-run (`/admin/ingestion`) or
  `scripts/onboard_documents_dry_run.py --input-dir <dir> --tenant-id pilot_demo`.
  The tool refuses the production/default collection by design.
- Open `/chat-ui`. Serve via `production_safe`. On-prem / closed network; no
  cloud egress.

## 1. The five demo questions (with expected response shape)

| # | Ask | Expected response shape | Point to make |
| --- | --- | --- | --- |
| 1 | 装置Xの起動手順を教えて | **Cited answer** from `souki_procedure.pdf` (the steps) | grounded answer **with source** |
| 2 | 外観検査の合否はどのように判定しますか | **Approved-Q&A exact match** (deterministic) — キズ0.5mm以下で合格 |言い切りの承認回答 |
| 3 | アラームE12の原因と対処 | Cited answer from `souki_troubleshooting.pdf` | troubleshooting from past cases |
| 4 | これは？ | **Abstain**: 「確実な根拠が登録文書内に見つからなかったため、回答できません」 | **never guesses** (too-general) |
| 5 | 経理の月次締め日は？ | **No-answer**: 関連情報が見つかりませんでした | **out-of-corpus → no fabrication** |

These shapes are backed by the measured eval (see
`docs/reports/prompt039_pilot_validation_and_demo_readiness_pack.md`): the seven
grounded lookups + the approved-exact-match retrieve the correct top-1 source
with no fallback; the two too-general questions and the out-of-corpus question
all abstain / return no-answer.

## 2. What to show in the UI

- **Citations panel** (right side): each grounded answer lists its source
  document and page.
- **Abstain message**: for #4/#5 the calm "分かりません"-style banner instead of a
  fabricated answer.
- **Feedback controls**: 役に立った / 役に立たなかった / 人に確認したい under each
  answer (posts to `/chat/feedback`).
- **Branding**: the workspace header shows the customer name/theme (via
  `/branding`).

## 3. How to explain the value (honest)

- **On-prem / no cloud:** "Your documents never leave your network. Data
  processing is local; the deploy smoke proves the image carries no secrets."
- **Never wrong, not always answering:** "When the evidence is weak it says
  『分かりません』 instead of guessing — for SOP・規程・安全 that is the point."
- **Deterministic approved answers:** "Reviewed Q&A are answered exactly, with a
  citation, every time."

## 4. What NOT to claim in the demo

- No accuracy numbers on the customer's real documents (measured during the PoC,
  not in advance).
- No general-production / HA / 24×7 SLA / compliance certification.
- SSO/OIDC and alerting are validated against the customer's real IdP/endpoints
  during staging (the in-repo tests use mocks).

## 5. Reminder

Synthetic, clearly-fictional data only (架空精機 / 装置X). Do not use real customer
documents, names, or identifiers in any demo or screenshot.
