from __future__ import annotations

import json
import socket
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from scripts import run_commercial_retrieval_dev_v1_embedding_comparison_v2 as runner
from scripts import validate_commercial_retrieval_dev_v1_embedding_comparison_v2_contract as contract_validator


def _contract() -> dict:
    return contract_validator.load_json(contract_validator.CONTRACT)


def _cases() -> list[dict]:
    rows = []
    for index in range(300):
        flags = ["hard_negative"]
        if index < 80:
            flags.append("low_lexical_overlap")
        if index < 45:
            flags.append("condition_exception_negation")
        if index < 40:
            flags.append("ocr_typo_terminology_noise")
        if index < 35:
            flags.extend(["version_sensitive", "multi_document"])
        gold = [f"gold_{index}"]
        if index < 35:
            gold.append(f"gold_{index}_second")
        rows.append({"case_id": f"a{index}", "query": "query", "answerable": True, "gold_chunk_ids": gold, "challenge_flags": flags})
    rows.extend({"case_id": f"x{index}", "query": "query", "answerable": False, "gold_chunk_ids": [], "challenge_flags": []} for index in range(100))
    return rows


def _rankings(cases: list[dict]) -> dict[str, list[str]]:
    values = {case["case_id"]: [f"miss_{number}" for number in range(50)] for case in cases}
    values["a0"][0] = "gold_0"
    values["a1"][5] = "gold_1"
    values["a1"][6] = "gold_1_second"
    values["a2"][20] = "gold_2"
    values["a3"][49] = "gold_3"
    return values


def test_exact_normalized_dot_product_tie_breaking_and_top_50_evidence():
    corpus = runner.l2_normalize(np.asarray([[2.0, 0.0], [1.0, 0.0]] + [[0.0, 4.0]] * 58, dtype=np.float32))
    query = runner.l2_normalize(np.asarray([[3.0, 0.0]], dtype=np.float32))[0]
    ranked = runner.rank_top_k(corpus, query, ["chunk_b", "chunk_a"] + [f"chunk_{index:02d}" for index in range(58)])

    assert ranked[:3] == ["chunk_a", "chunk_b", "chunk_00"]
    assert len(ranked) == 50


def test_frozen_metrics_include_top_50_coverage_mrr_ndcg_and_multi_document_completeness():
    contract, cases = _contract(), _cases()
    aggregate, slices, multi = runner.evaluate_rankings(cases, _rankings(cases), contract)

    assert aggregate["recall_at_5"] == {"denominator": 300, "numerator": 1, "value": 1 / 300}
    assert aggregate["recall_at_10"]["numerator"] == 2
    assert aggregate["recall_at_20"]["numerator"] == 2
    assert aggregate["recall_at_50"]["numerator"] == 4
    assert aggregate["candidate_coverage_at_20"]["numerator"] == 2
    assert aggregate["candidate_coverage_at_50"]["numerator"] == 4
    assert aggregate["mrr_at_20"]["value"] == pytest.approx((1 + 1 / 6) / 300)
    ideal_two = 1 + 1 / np.log2(3)
    assert aggregate["ndcg_at_20"]["value"] == pytest.approx((1 / ideal_two + (1 / np.log2(7) + 1 / np.log2(8)) / ideal_two) / 300)
    assert slices["multi_document"]["case_count"] == 35
    assert multi["at_least_one_required_support_at_20"]["numerator"] == 2
    assert multi["all_required_supports_at_20"]["numerator"] == 1
    assert multi["at_least_one_required_support_at_50"]["numerator"] == 4
    assert multi["all_required_supports_at_50"]["numerator"] == 1


def test_all_six_candidates_dispatch_with_frozen_formatting_and_truncation():
    candidates = {candidate["id"]: candidate for candidate in _contract()["candidate_embeddings"]}
    adapters = {candidate_id: runner.build_adapter(candidate, Path("/tmp/unused")) for candidate_id, candidate in candidates.items()}

    assert isinstance(adapters["plamo_embedding_1b_japanese_higher_capacity"], runner.PlamoEmbeddingAdapter)
    assert all(isinstance(adapter, runner.TransformerPoolingAdapter) for candidate_id, adapter in adapters.items() if candidate_id != "plamo_embedding_1b_japanese_higher_capacity")
    assert adapters["qwen3_embedding_0_6b_global_challenger"].format_query("何ですか") == "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:何ですか"
    assert adapters["ruri_v3_310m_japanese_specialist"].format_query("質問") == "検索クエリ: 質問"
    assert adapters["ruri_v3_310m_japanese_specialist"].format_document("文書") == "検索文書: 文書"
    assert adapters["plamo_embedding_1b_japanese_higher_capacity"].truncation == 1024
    assert adapters["qwen3_embedding_0_6b_global_challenger"].truncation == 8192
    assert adapters["granite_embedding_311m_multilingual_r2_enterprise_challenger"].truncation == 8192
    assert adapters["paraphrase_multilingual_minilm_l12_v2_historical_control"].truncation == 128


def test_plamo_uses_asymmetric_api_and_trust_remote_code(monkeypatch):
    torch = pytest.importorskip("torch")
    candidate = next(item for item in _contract()["candidate_embeddings"] if item["id"] == "plamo_embedding_1b_japanese_higher_capacity")
    calls: list[tuple] = []

    class Tokenizer:
        model_max_length = None

    class Model:
        def __init__(self):
            self.config = types.SimpleNamespace()
            self._parameter = torch.nn.Parameter(torch.ones(1))
            self.rotary = types.SimpleNamespace(
                inv_freq=torch.full((2,), float("nan")),
                dim=4,
                base=10000,
                max_position_embeddings=4,
            )

            def set_cache(*, seq_len, device, dtype):
                self.rotary.cos_cached = torch.ones((1, 1, seq_len, 4), device=device, dtype=dtype)
                self.rotary.sin_cached = torch.ones((1, 1, seq_len, 4), device=device, dtype=dtype)

            self.rotary._set_cos_sin_cache = set_cache

        def to(self, _device):
            return self

        def eval(self):
            return self

        def parameters(self):
            return iter((self._parameter,))

        def modules(self):
            return iter((self, self.rotary))

        def encode_query(self, query, tokenizer):
            calls.append(("query", query, tokenizer))
            return np.ones(2048, dtype=np.float32)

        def encode_document(self, documents, tokenizer):
            calls.append(("document", documents, tokenizer))
            return np.ones((1, 2048), dtype=np.float32)

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            calls.append(("tokenizer", args, kwargs))
            return Tokenizer()

    class AutoModel:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            calls.append(("model", args, kwargs))
            return Model()

    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(AutoTokenizer=AutoTokenizer, AutoModel=AutoModel))
    adapter = runner.PlamoEmbeddingAdapter(candidate, Path("/tmp/plamo"))
    adapter.load()
    adapter.encode_queries(["q"])
    adapter.encode_documents(["d"])

    assert calls[0][2]["trust_remote_code"] is True and calls[1][2]["trust_remote_code"] is True
    assert adapter.tokenizer.model_max_length == 1024
    assert adapter.model.config.max_length == 1024
    assert torch.isfinite(adapter.model.rotary.inv_freq).all()
    assert ("query", "q", adapter.tokenizer) in calls
    assert ("document", ["d"], adapter.tokenizer) in calls


def test_qwen_last_token_pooling_handles_left_and_right_padded_mixed_batches():
    torch = pytest.importorskip("torch")
    candidate = next(item for item in _contract()["candidate_embeddings"] if item["id"] == "qwen3_embedding_0_6b_global_challenger")
    adapter = runner.TransformerPoolingAdapter(candidate, Path("/tmp/qwen"))
    hidden = torch.tensor([
        [[90.0], [91.0], [10.0], [11.0]],
        [[80.0], [20.0], [21.0], [22.0]],
    ])

    left_padded = torch.tensor([[0, 0, 1, 1], [0, 1, 1, 1]])
    right_padded = torch.tensor([[1, 1, 0, 0], [1, 1, 1, 0]])

    assert torch.equal(adapter._pool(hidden, left_padded), torch.tensor([[11.0], [22.0]]))
    assert torch.equal(adapter._pool(hidden, right_padded), torch.tensor([[91.0], [21.0]]))


def test_granite_loader_uses_default_tokenizer_without_fix_mistral_regex(monkeypatch):
    candidate = next(item for item in _contract()["candidate_embeddings"] if item["id"] == "granite_embedding_311m_multilingual_r2_enterprise_challenger")
    calls: list[dict] = []

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(*_args, **kwargs):
            calls.append(kwargs)
            return object()

    class AutoModel:
        @staticmethod
        def from_pretrained(*_args, **_kwargs):
            return types.SimpleNamespace(to=lambda _device: types.SimpleNamespace(eval=lambda: None))

    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(AutoTokenizer=AutoTokenizer, AutoModel=AutoModel))
    runner.TransformerPoolingAdapter(candidate, Path("/tmp/granite")).load()
    assert "fix_mistral_regex" not in calls[0]
    assert calls[0]["local_files_only"] is True


def test_network_prohibition_dimension_candidate_revision_and_dataset_fail_closed(tmp_path):
    attempts: list[str] = []
    with runner.network_guard(attempts), pytest.raises(runner.ExecutionError):
        socket.getaddrinfo("example.invalid", 443)
    assert attempts

    candidate = _contract()["candidate_embeddings"][0].copy()
    candidate["revision"] = "0" * 40
    with pytest.raises(runner.ExecutionError, match="asset identity evidence mismatch"):
        runner._verify_asset_dir(candidate, tmp_path)
    with pytest.raises(runner.ExecutionError, match="frozen dataset count mismatch"):
        runner._validate_dataset([], [], _contract())

    class BadAdapter(runner.EmbeddingAdapter):
        def load(self):
            pass
        def encode_queries(self, queries):
            return self._validated(np.ones((len(queries), 1), dtype=np.float32), len(queries))
        def encode_documents(self, documents):
            return self._validated(np.ones((len(documents), 1), dtype=np.float32), len(documents))

    with pytest.raises(runner.ExecutionError, match="embedding dimension"):
        BadAdapter(_contract()["candidate_embeddings"][0], tmp_path).encode_queries(["x"])


def test_new_v2_sources_do_not_reference_selection_holdout_paths():
    for path in (Path(runner.__file__), Path(__file__).parents[1] / "scripts/validate_commercial_retrieval_dev_v1_embedding_comparison_v2_execution.py"):
        assert "embedding_selection_holdout" not in path.read_text(encoding="utf-8")

def test_as_float32_array_converts_bfloat16_torch_tensor():
    import numpy as np
    import torch

    value = torch.tensor(
        [[1.0, 2.0, 3.0]],
        dtype=torch.bfloat16,
        requires_grad=True,
    )

    result = runner._as_float32_array(value)

    assert result.dtype == np.float32
    assert result.shape == (1, 3)
    assert np.allclose(result, [[1.0, 2.0, 3.0]])
