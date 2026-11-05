import json

import pytest

import config
from eval.bench import conditions, runner
from eval.bench.compare_runs import compare_conditions, compare_runs, run_conditions, to_markdown
from eval.bench.dataset import EvalQuery, Relevant
from eval.bench.encoders import EmbeddingSpec
from eval.bench.metrics import RankedChunk
from rag_core import index_manifest


def test_required_metric_set():
    assert conditions.missing_required_metrics([1, 3, 5, 10, 20, 50, 100]) == []
    assert conditions.missing_required_metrics([1, 10]) == [
        "hit@3", "hit@5", "recall@20", "recall@50",
    ]


def test_embedding_profile_follows_the_resolved_prefixes():
    ruri = conditions.embedding_profile(EmbeddingSpec(model="cl-nagoya/ruri-v3-130m"), 512)
    assert ruri == {
        "embed_model": "cl-nagoya/ruri-v3-130m",
        "embed_model_revision": None,
        "embed_prefix_scheme": "ruri-v3-retrieval",
        "embed_normalize": "true",
        "embed_distance": "cosine",
        "embedding_dim": 512,
    }
    plain = conditions.embedding_profile(EmbeddingSpec(model="other/model", prefix="none", revision="abc"), 384)
    assert plain["embed_prefix_scheme"] == "none" and plain["embed_model_revision"] == "abc"


def _query(query_id, chunk_id, answerable=True):
    relevant = (Relevant(chunk_id, None, None, 1),) if answerable else ()
    return EvalQuery(query_id, "質問", "dev", "default", answerable, relevant, (), query_id)


def test_candidate_miss_marks_questions_the_candidates_cannot_answer():
    views = [RankedChunk("c1", "d1", "k1"), RankedChunk("c2", "d2", "k2")]
    assert conditions.candidate_miss(views, _query("q1", "c2"), "chunk") == 0.0
    assert conditions.candidate_miss(views, _query("q2", "c9"), "chunk") == 1.0
    assert conditions.candidate_miss(views, _query("q3", "c1", answerable=False), "chunk") is None


def _writer(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "per_query").mkdir(parents=True)
    queries = [_query("q1", "c1"), _query("q2", "zzz")]
    views = [RankedChunk("c1", "d1", "k1"), RankedChunk("c2", "d2", "k2")]
    settings = {"ks": [1, 2], "match_mode": "chunk", "ci_metrics": ["recall@2"], "resamples": 20, "seed": 0}
    return runner._Writer(run_dir, queries, views, settings), run_dir


def _evaluate(writer, extras=None):
    ranked = {"q1": [1, 0], "q2": [1, 0]}
    scores = {"q1": [0.9, 0.5], "q2": [0.9, 0.5]}
    stage_ms = {"embed": {"q1": 1.0, "q2": 1.0}}
    writer.evaluate({"config": {"fusion": "x"}}, ranked, scores, stage_ms, extras)
    return json.loads((writer.run_dir / "summary.jsonl").read_text(encoding="utf-8").splitlines()[-1])


def test_summary_reports_the_candidate_miss_rate(tmp_path):
    writer, _ = _writer(tmp_path)
    assert _evaluate(writer)["candidate_miss_rate"] == pytest.approx(0.5)  # q1 found at rank 2, q2 never
    writer, _ = _writer(tmp_path / "again")
    extras = {"q1": {"candidate_miss": 1.0}, "q2": {"candidate_miss": 1.0}}
    assert _evaluate(writer, extras)["candidate_miss_rate"] == 1.0


def _manifest(name, corpus_path, *, model="cl-nagoya/ruri-v3-130m", scheme="ruri-v3-retrieval"):
    return {
        "schema_version": 1, "generation_id": f"gid-{name}", "collection": name, "backend": "qdrant",
        "keyword_index_path": str(corpus_path), "created_at": "2026-01-01T00:00:00+00:00",
        "profile": {
            "embed_provider": "local", "embed_model": model, "embed_prefix_scheme": scheme,
            "embed_normalize": "true", "embed_distance": "cosine", "embedding_dim": 512, "embed_model_revision": None,
        },
        "point_count": 3, "keyword_rows": 3, "verification": {"status": "verified", "checked_at": None},
    }


@pytest.fixture
def generations(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "INDEX_DIR", str(tmp_path / "index"))
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("", encoding="utf-8")
    index_manifest.write_manifest(_manifest("g1", corpus))
    return corpus


def test_a_generation_supplies_corpus_and_embedding(generations):
    cfg = {"generation": "g1", "eval_set": "x"}
    resolved, info = conditions.apply_generation(cfg)
    assert resolved["corpus"]["chunks_jsonl"] == str(generations)
    assert resolved["embedding"] == [{"model": "cl-nagoya/ruri-v3-130m", "prefix": "auto"}]
    assert info["generation_id"] == "gid-g1" and info["verification_status"] == "verified"
    assert "corpus" not in cfg  # the caller's config is not modified
    again, _ = conditions.apply_generation(resolved)  # idempotent: plan() and run() both resolve
    assert again["corpus"] == resolved["corpus"] and again["embedding"] == resolved["embedding"]


def test_without_a_generation_the_config_is_untouched():
    cfg = {"eval_set": "x"}
    assert conditions.apply_generation(cfg) == (cfg, None)


def test_a_run_about_a_generation_must_use_its_profile_and_corpus(generations, tmp_path):
    with pytest.raises(ValueError, match="no manifest"):
        conditions.apply_generation({"generation": "unregistered"})
    with pytest.raises(ValueError, match="is not the profile"):
        conditions.apply_generation({"generation": "g1", "embedding": [{"model": "another/model"}]})
    with pytest.raises(ValueError, match="is not the profile"):
        conditions.apply_generation({"generation": "g1", "embedding": [{"model": "cl-nagoya/ruri-v3-130m", "prefix": "none"}]})
    with pytest.raises(ValueError, match="not the corpus"):
        conditions.apply_generation({"generation": "g1", "corpus": {"chunks_jsonl": str(tmp_path / "other.jsonl")}})
    ok, _ = conditions.apply_generation(
        {"generation": "g1", "corpus": {"chunks_jsonl": str(generations)}, "embedding": [{"model": "cl-nagoya/ruri-v3-130m"}]}
    )
    assert ok["corpus"]["chunks_jsonl"] == str(generations)


def _write_run(path, *, recalls, eval_sha="e1", generation=None, corpus="c1", record_conditions=True):
    (path / "per_query").mkdir(parents=True)
    meta = {"eval_set_sha256": eval_sha, "split": "dev", "queries": len(recalls), "config": {"match_mode": "chunk"}}
    summary = {"config_id": "cfg1"}
    if record_conditions:
        meta.update(tie_break="chunk_id", generation=generation)
        summary["index"] = {"corpus_sha256": corpus, "embedding_profile": {"embed_model": "m"}}
    (path / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    (path / "summary.jsonl").write_text(json.dumps(summary) + "\n", encoding="utf-8")
    rows = [
        {"query_id": f"q{i}", "group": f"g{i}", "answerable": True, "slices": [],
         "metrics": {"recall@10": value, "hit@10": 1.0 if value > 0 else 0.0}}
        for i, value in enumerate(recalls, start=1)
    ]
    (path / "per_query" / "cfg1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _compare(base, cand, **kwargs):
    return compare_runs(base, "cfg1", cand, "cfg1", resamples=50, **kwargs)


def test_compare_runs_reports_conditions_and_changed_questions(tmp_path):
    _write_run(tmp_path / "a", recalls=[0.0, 1.0, 0.5])
    _write_run(tmp_path / "b", recalls=[1.0, 1.0, 0.0])
    report = _compare(tmp_path / "a", tmp_path / "b", require_same_conditions=True)
    assert report["conditions"]["comparable"] is True and report["conditions"]["same_corpus"] is True
    changes = report["paired_changes"]
    assert changes["improved"] == {"count": 1, "query_ids": ["q1"]}
    assert changes["worsened"] == {"count": 1, "query_ids": ["q3"]}
    assert changes["unchanged"] == 1
    markdown = to_markdown(report)
    assert "same conditions: True" in markdown and "improved: 1  worsened: 1  unchanged: 1" in markdown


def test_runs_under_different_conditions_are_flagged_or_refused(tmp_path):
    _write_run(tmp_path / "a", recalls=[0.0, 1.0, 0.5])
    _write_run(tmp_path / "b", recalls=[1.0, 1.0, 0.0], eval_sha="e2", corpus="c2")
    report = _compare(tmp_path / "a", tmp_path / "b")
    assert report["conditions"]["same_eval_set"] is False and report["conditions"]["same_corpus"] is False
    assert set(report["conditions"]["differences"]) == {"eval_set_sha256", "corpus_sha256"}
    with pytest.raises(ValueError, match="not measured under the same conditions"):
        _compare(tmp_path / "a", tmp_path / "b", require_same_conditions=True)


def test_runs_that_did_not_record_their_conditions_cannot_be_proven_comparable(tmp_path):
    _write_run(tmp_path / "a", recalls=[0.0, 1.0, 0.5], record_conditions=False)
    _write_run(tmp_path / "b", recalls=[1.0, 1.0, 0.0])
    report = _compare(tmp_path / "a", tmp_path / "b")
    assert report["conditions"]["same_corpus"] is None and "corpus_sha256" in report["conditions"]["unknown"]
    with pytest.raises(ValueError, match="unknown"):
        _compare(tmp_path / "a", tmp_path / "b", require_same_conditions=True)


def test_generation_ids_are_part_of_the_conditions(tmp_path):
    _write_run(tmp_path / "a", recalls=[0.5, 0.5, 0.5], generation={"generation_id": "gid-1"})
    _write_run(tmp_path / "b", recalls=[0.5, 0.5, 0.5], generation={"generation_id": "gid-2"})
    _write_run(tmp_path / "c", recalls=[0.5, 0.5, 0.5], generation=None)
    assert run_conditions(tmp_path / "a", "cfg1")["generation_id"] == "gid-1"
    assert run_conditions(tmp_path / "c", "cfg1")["generation_id"] == "(none)"
    assert compare_conditions(run_conditions(tmp_path / "a", "cfg1"), run_conditions(tmp_path / "b", "cfg1"))[
        "same_generation"
    ] is False
