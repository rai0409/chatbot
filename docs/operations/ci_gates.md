# CI gates and branch protection

`.github/workflows/ci.yml` has one job, `test`. Every check is a step with an `id` and
`continue-on-error: true`, so one failure does not hide the others; the last step
("Diagnostic summary and mandatory failure gate") writes a PASS / FAILED / BLOCKED /
UNEXECUTED table to the job summary and fails the job when any gated step did not succeed.

## Branch protection (repository settings)

Configure on `main` (Settings > Branches > Branch protection rule, or a ruleset):

- Require a pull request before merging.
- Require status checks to pass: **`test`** (the job name; shown as `CI / test` in the
  pull request). Enable "Require branches to be up to date before merging".
- Do not allow bypassing the above settings (administrators included).

The single job is deliberate for now: a failing step is still visible in the summary table.
When the job is split later, every new job name must be added to the required checks.

## Gated step ids

Every id below must succeed (a step skipped because its prerequisite failed counts as a failure):

| id | what |
|---|---|
| `product_readiness` | `scripts/product_readiness_smoke.sh` |
| `normalization` | normalization golden cases |
| `stage_regression` | stage 1/2/3/5 and query preprocessing suites |
| `hardening` | auth, tenant isolation, answer cache, confidence guard suites |
| `dependency_audit` | pip-audit of the hash-locked sets (see below) |
| `bandit` | bandit, product code, medium or higher |
| `secret_scan` | detect-secrets against `.secrets.baseline` |
| `production_guard` | production settings guard |
| `retrieval_gate` | synthetic retrieval regression gate (`--margin 0.02`) |
| `stage8` | stage 8 suite |
| `pyflakes` | pyflakes, no finding allowed |
| `ruff_broad_except` | BLE001 / S110 on changed lines |
| `node_unit` | screen script unit tests (`node --test`) |
| `webui_install`, `webui_typecheck`, `webui_tests`, `webui_build` | console install, typecheck, unit tests, build |
| `webui_bundle_budget` | console bundle size budget (see below) |
| `webui_audit` | `npm audit --audit-level=high` |
| `playwright_package`, `playwright_browser`, `playwright_e2e` | browser tests (`KURADEN_E2E_REQUIRED=1`: a skip is a failure) |
| `stage10_install`, `stage10_tests`, `stage10_check` | docextract `[light]` install (constrained to the audited pins), tests, backend check |
| `postgres` | PostgreSQL quota store integration tests |
| `full_suite` | the whole suite with `CI_FAIL_ON_UNEXPECTED_SKIP=1` (coverage report is informational) |

A new step must get an `id` and be added to the `ids` list of the final step;
`tests/test_infra_hardening_20261127.py` fails when a step id is not gated.

## Unexpected skips

The job sets `REQUIRE_OPTIONAL_DEPS=1` (a missing `anthropic` / `redis` fails the tests that
need them) and the full suite runs with `CI_FAIL_ON_UNEXPECTED_SKIP=1`: at the end of the
session (`tests/conftest.py`) every skip is compared with `tests/skip_allowlist.txt`
(`<nodeid glob>` TAB `<reason regex>` TAB `<why it is allowed>`), and a skip no entry matches
fails the run and is listed under "unexpected skips".

Policy:

- Allowed: skips that depend on the runner or are opt-in by design: the real-vector release
  gate (`CHATBOT_REAL_VECTOR_RELEASE_GATE=1`), the PaddleOCR model test
  (`DOCEXTRACT_RUN_MODEL_TESTS=1`), single-CPU runners, file systems without symlinks, and the
  benchmark-only `pyarrow` extra.
- Never allowed: a missing package of the product install (`qdrant_client`, `sudachipy` and its
  dictionary, `pdfplumber`, `pypdfium2`, `PIL`, `chromadb`, `anthropic`, `redis`) or of the CI
  tools (`pyflakes`, `node`). Fix the install instead.
- Browser tests are not allowed as skips: the full suite runs with `--ignore=tests/e2e`, and the
  `playwright_e2e` step runs them with `KURADEN_E2E_REQUIRED=1`.
- Each new entry needs a reason in its third column and a reviewer in the pull request.

Locally the variable is unset and nothing changes; `pytest -rs` (in `pytest.ini`) lists the skips.

## Dependency audit and ignored advisories

`dependency_audit` audits what is installed, not a loose list:

```
python -m pip_audit --require-hashes --disable-pip -r requirements.lock.txt $IGNORES
python -m pip_audit --require-hashes --disable-pip -r requirements-embedding.lock.txt $IGNORES
```

Both lock files pin every package with hashes (required for `--disable-pip`).
`requirements.lock.txt` also pins the docextract `[light]` extras, and the `stage10_install`
step installs them with `-c constraints/linux-py312.txt`, so the audited versions are the
installed ones.

Ignored advisories live in `security/pip-audit-ignore.txt`, one per line, tab-separated:
advisory id, package, reason, expiry date (`YYYY-MM-DD`), reviewer role.
`scripts/check_audit_ignores.py --print-args` validates the file and prints the
`--ignore-vuln` arguments; an expired, undated, duplicate or malformed entry fails the step,
and an entry expiring within 14 days is reported as a warning.

Policy:

- Ignore only an advisory that has no fixed release or that does not reach the product
  (state why in the reason column).
- Expiry at most 90 days ahead. At expiry, re-review: upgrade and remove the entry, or extend it
  with a new date and an updated reason in a reviewed pull request.

## Console bundle budget

`webui_bundle_budget` (after `webui_build`) measures gzip sizes (level 9) in `webui/dist`:

- entry: the module script of `index.html` plus the chunks it preloads, at most 120000 bytes;
- all JavaScript chunks together, at most 220000 bytes.

Over budget: split routes (`React.lazy`) or drop a dependency; raise a budget only in a
reviewed pull request that states why.

## Notes

- No per-test timeout: `pytest-timeout` is not in `constraints/linux-py312.txt`; the step
  `timeout-minutes` bounds a hung run. Add it to the constraints and the lock first if needed.
- `pytest.ini` sets the test paths (`tests`, `libs/docextract/tests`) and keeps `即削除/`,
  `data/`, `runs/`, `webui/` and other non-test folders out of collection.
