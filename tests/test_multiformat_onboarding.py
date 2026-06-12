from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.runner import load_cases
from scripts import onboard_documents_dry_run
from scripts.generate_sample_docs import main as generate_samples
from scripts.import_manifest import build_manifest


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def _row(chunk_id, text="本文", source_doc="a.csv", tenant="default", source_type="csv"):
    return {
        "id": chunk_id,
        "text": text,
        "source_doc": source_doc,
        "tenant_id": tenant,
        "source_type": source_type,
        "type": source_type,
    }


# --- manifest ----------------------------------------------------------------


def test_manifest_counts_and_hash_are_stable(tmp_path):
    path = tmp_path / "a.jsonl"
    _write_jsonl(path, [_row("c1", "一"), _row("c2", "二")])

    first = build_manifest([path])
    second = build_manifest([path])

    assert first["ok"] is True
    entry = first["source_docs"]["a.csv"]
    assert entry["chunk_count"] == 2
    assert entry["tenant_ids"] == ["default"]
    assert entry["chunk_ids_hash"] == second["source_docs"]["a.csv"]["chunk_ids_hash"]


def test_manifest_flags_duplicate_ids_within_and_across_files(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    _write_jsonl(a, [_row("dup", "一"), _row("dup", "二")])
    _write_jsonl(b, [_row("dup", "三", source_doc="b.csv")])

    manifest = build_manifest([a, b])

    assert manifest["ok"] is False
    assert manifest["issues"]["duplicate_ids"] == ["dup"]


def test_manifest_flags_identical_text_under_different_ids(tmp_path):
    path = tmp_path / "a.jsonl"
    _write_jsonl(path, [_row("c1", "同じ本文"), _row("c2", "同じ本文")])

    manifest = build_manifest([path])

    assert manifest["ok"] is False
    assert manifest["issues"]["duplicate_texts"][0]["chunk_ids"] == ["c1", "c2"]


def test_parent_child_identical_text_is_informational_not_blocking(tmp_path):
    path = tmp_path / "a.jsonl"
    _write_jsonl(
        path,
        [
            _row("doc.pdf:p1:parent:1", "同じ本文", source_doc="doc.pdf", source_type="pdf"),
            _row("doc.pdf:p1:parent:1:child:1", "同じ本文", source_doc="doc.pdf", source_type="pdf"),
        ],
    )

    manifest = build_manifest([path])

    assert manifest["ok"] is True
    assert manifest["issues"]["duplicate_texts"] == []
    assert manifest["informational"]["parent_child_duplicates"][0]["chunk_ids"] == [
        "doc.pdf:p1:parent:1",
        "doc.pdf:p1:parent:1:child:1",
    ]


def test_manifest_flags_tenant_mismatch_within_source_doc(tmp_path):
    path = tmp_path / "a.jsonl"
    _write_jsonl(path, [_row("c1", "一", tenant="tenant_a"), _row("c2", "二", tenant="tenant_b")])

    manifest = build_manifest([path], expected_tenant="tenant_a")

    assert manifest["ok"] is False
    assert manifest["issues"]["tenant_mismatches"] == ["a.csv"]
    assert manifest["issues"]["unexpected_tenants"] == [
        {"source_doc": "a.csv", "tenant_ids": ["tenant_b"]}
    ]


def test_manifest_flags_collision_with_existing_manifest(tmp_path):
    old = tmp_path / "old.jsonl"
    _write_jsonl(old, [_row("c1", "旧")])
    existing = build_manifest([old])

    new = tmp_path / "new.jsonl"
    _write_jsonl(new, [_row("c9", "新")])  # same source_doc, different ids

    manifest = build_manifest([new], existing_manifest=existing)

    assert manifest["ok"] is False
    assert manifest["issues"]["collisions"][0]["source_doc"] == "a.csv"


# --- dry-run onboarding --------------------------------------------------------


@pytest.fixture()
def sample_dir(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "faq.csv").write_text(
        "質問,回答\n営業時間は,9時から17時です\n", encoding="utf-8"
    )
    return docs


def test_dry_run_never_calls_ingest(monkeypatch, tmp_path, sample_dir, capsys):
    def _forbidden(merged, collection):
        raise AssertionError("dry-run must never ingest")

    monkeypatch.setattr(onboard_documents_dry_run, "_run_ingest", _forbidden)

    exit_code = onboard_documents_dry_run.main(
        ["--input-dir", str(sample_dir), "--tenant-id", "tenant_x", "--output-dir", str(tmp_path / "work")]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "DRY-RUN: nothing was written" in out
    assert "csv/qa_pair: 1" in out
    manifest = json.loads((tmp_path / "work" / "import_manifest.json").read_text(encoding="utf-8"))
    assert manifest["ok"] is True
    assert manifest["expected_tenant"] == "tenant_x"


def test_execute_requires_explicit_collection(sample_dir, tmp_path, capsys):
    exit_code = onboard_documents_dry_run.main(
        ["--input-dir", str(sample_dir), "--tenant-id", "t", "--output-dir", str(tmp_path / "w"), "--execute"]
    )

    assert exit_code == 2
    assert "requires an explicit non-production --collection" in capsys.readouterr().err


def test_execute_refuses_production_collection(monkeypatch, sample_dir, tmp_path, capsys):
    monkeypatch.setattr(
        onboard_documents_dry_run, "_production_collection_names", lambda: {"chatbot_chunks_v1"}
    )

    exit_code = onboard_documents_dry_run.main(
        [
            "--input-dir", str(sample_dir), "--tenant-id", "t",
            "--output-dir", str(tmp_path / "w"),
            "--execute", "--collection", "chatbot_chunks_v1",
        ]
    )

    assert exit_code == 2
    assert "refusing to ingest into production/default collection" in capsys.readouterr().err


def test_execute_with_nonproduction_collection_calls_ingest(monkeypatch, sample_dir, tmp_path):
    calls = []
    monkeypatch.setattr(
        onboard_documents_dry_run, "_run_ingest", lambda merged, collection: calls.append((merged, collection))
    )

    exit_code = onboard_documents_dry_run.main(
        [
            "--input-dir", str(sample_dir), "--tenant-id", "t",
            "--output-dir", str(tmp_path / "w"),
            "--execute", "--collection", "onboarding_eval_v1",
        ]
    )

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0][1] == "onboarding_eval_v1"


def test_validation_failure_blocks_execute(monkeypatch, tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    # Two distinct rows with identical text -> duplicate_texts issue.
    (docs / "x.csv").write_text("質問,回答\n同じ,内容\n同じ,内容x\n", encoding="utf-8")
    monkeypatch.setattr(
        onboard_documents_dry_run, "_run_ingest",
        lambda merged, collection: pytest.fail("must not ingest on validation failure"),
    )
    # Force a validation failure regardless of converter id behavior.
    monkeypatch.setattr(
        onboard_documents_dry_run, "build_manifest",
        lambda *a, **k: {"ok": False, "issues": {"duplicate_ids": ["x"]}, "source_docs": {}},
    )

    exit_code = onboard_documents_dry_run.main(
        [
            "--input-dir", str(docs), "--tenant-id", "t",
            "--output-dir", str(tmp_path / "w"),
            "--execute", "--collection", "onboarding_eval_v1",
        ]
    )

    assert exit_code == 1
    assert "Validation failed" in capsys.readouterr().out


# --- sample docs + eval corpus -------------------------------------------------


def test_sample_doc_generator_is_deterministic(tmp_path):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    assert generate_samples(["--output-dir", str(dir_a)]) == 0
    assert generate_samples(["--output-dir", str(dir_b)]) == 0

    for name in ("sample_faq.csv", "sample_plans.xlsx", "sample_manual.docx", "sample_deck.pptx"):
        assert (dir_a / name).read_bytes() == (dir_b / name).read_bytes(), name


def test_committed_multiformat_cases_load_via_eval_runner():
    cases = load_cases(Path("eval/cases/multiformat_cases.jsonl"))

    assert len(cases) >= 12
    case_ids = [c.case_id for c in cases]
    assert len(case_ids) == len(set(case_ids))
    assert sum(1 for c in cases if c.expected_abstain) >= 2
    gold = [c for c in cases if c.gold_chunk_ids]
    assert len(gold) >= 10
    chunk_ids = set()
    for line in Path("eval/cases/multiformat_chunks.jsonl").read_text(encoding="utf-8").splitlines():
        chunk_ids.add(json.loads(line)["id"])
    for case in gold:
        for gold_id in case.gold_chunk_ids:
            assert gold_id in chunk_ids, (case.case_id, gold_id)
