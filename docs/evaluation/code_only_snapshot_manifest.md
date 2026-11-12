# Code-only evaluation snapshot

Source branch: `feat/candidate-preservation-optimization-experiment-v1`
Source commit: `88c41f825b38f28ea8d53e23703023473b7cc226`
GitHub main base: `e5eb5119e3e876b8dc14b6237720681b5979fb78`

This review branch combines the public main base with selected local evaluation code. It is not a complete copy of the source commit or its history.

## Added files

- `config/evaluation/external_retrieval_eval_v1.contract.json`
- `scripts/inspect_external_eval_datasets_v1.py`
- `scripts/validate_external_retrieval_eval_v1.py`
- `tests/test_external_retrieval_eval_v1.py`
- `config/evaluation/commercial_retrieval_dev_v1_embedding_comparison_v2.contract.json`
- `tests/test_commercial_retrieval_dev_v1_embedding_comparison_v2_assets.py`
- `tests/test_commercial_retrieval_dev_v1_embedding_comparison_v2_contract.py`
- `tests/test_commercial_retrieval_dev_v1_embedding_comparison_v2_execution.py`
- `tests/test_commercial_retrieval_dev_v1_embedding_comparison_v2_runner.py`

## Omitted data and limitations

New copies of source documents, indexes, approved QA, evaluation datasets, model assets, rankings, per-query output, metrics, receipts, and dataset manifests are omitted. The external evaluation runner and documentation are omitted because they contain a user-specific absolute path. The v2 implementation scripts are outside the authorized candidate paths. Consequently, the included external evaluation scripts and tests, and v2 tests, reference missing modules and artifacts. This is a code analysis snapshot; execution compatibility is not established.

## Validation performed

- The added source files matched their source SHA256 values after copying.
- The added Python files parsed successfully; both JSON contracts parsed successfully.
- The external evaluation contract's candidate source SHA256 matched the included v2 contract.
- Related tests were blocked in this snapshot by omitted dependencies and data. No benchmark execution was performed.
