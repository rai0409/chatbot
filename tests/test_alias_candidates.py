import json

import pytest

import config
from eval.tenant_eval import aliases, perturb
from rag_core.approved_qa import MAX_APPROVED_ALIASES, load_approved_qa, lookup_approved_answer
from scripts import alias_candidates

QUESTION = "パスワード再設定の方法は？"


def _record(qa_id, question, tenant="default", aliases=None):
    record = {"qa_id": qa_id, "question": question, "approved_answer": "承認済み回答です。", "language": "ja",
              "approved_citations": [{"source_doc": "faq.pdf", "source_pages": [1], "chunk_id": f"c-{qa_id}"}],
              "tenant_id": tenant, "status": "approved"}
    if aliases is not None:
        record["approved_aliases"] = aliases
    return record


def _index(tmp_path, records, tenant="default"):
    path = tmp_path / "approved.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    return path, load_approved_qa(path, tenant_id=tenant)


def test_coverage_counts_the_variants_the_lookup_already_reaches_per_kind(tmp_path):
    _path, index = _index(tmp_path, [_record("q1", QUESTION), _record("q2", "経費精算の締め日は？")])
    report = aliases.coverage(index)
    assert report["approved"] == 2 and report["variants"] == sum(s["variants"] for s in report["by_kind"].values())
    assert report["covered"] + len(report["misses"]) == report["variants"]
    assert all(m["qa_id"] in {"q1", "q2"} and m["variant"] != m["question"] for m in report["misses"])
    assert {m["kind"] for m in report["misses"]} >= {"filler", "casual_ending"}  # these never reach the exact lookup
    assert report["covered_rate"] == pytest.approx(report["covered"] / report["variants"])


def test_a_variant_that_is_an_alias_is_covered(tmp_path):
    variant = perturb.perturb_question(QUESTION, kinds=["casual_ending"])[0]["text"]
    _path, plain = _index(tmp_path, [_record("q1", QUESTION)])
    _path2, with_alias = _index(tmp_path, [_record("q1", QUESTION, aliases=[variant])])
    miss = [m for m in aliases.coverage(plain, kinds=["casual_ending"])["misses"]]
    assert [m["variant"] for m in miss] == [variant]
    covered = aliases.coverage(with_alias, kinds=["casual_ending"])
    assert covered["misses"] == [] and covered["covered"] == covered["variants"] == 1


def test_coverage_of_an_empty_index_or_another_tenant_is_empty(tmp_path):
    _path, index = _index(tmp_path, [_record("q1", QUESTION)])
    assert aliases.coverage(index, tenant_id="tenant_b")["variants"] == 0
    assert aliases.coverage(index, tenant_id="tenant_b")["covered_rate"] is None


def test_candidates_come_from_the_plausible_kinds_within_the_room_an_approved_qa_has(tmp_path):
    _path, index = _index(tmp_path, [_record("q1", QUESTION)])
    report = aliases.coverage(index)
    rows = aliases.candidates_from_coverage(index, report)
    assert rows and {r["kind"] for r in rows} <= set(aliases.DEFAULT_ALIAS_KINDS) and len(rows) <= 3
    assert all(r["status"] == "pending" and r["source"] == "perturbation" and r["qa_id"] == "q1" for r in rows)
    assert len({r["candidate_id"] for r in rows}) == len(rows)
    every = aliases.candidates_from_coverage(index, report, kinds=perturb.KINDS, max_per_question=99)
    assert "filler" in {r["kind"] for r in every}
    assert aliases.candidates_from_coverage(index, report, kinds=["fullwidth"]) == []


def test_candidates_never_exceed_the_aliases_an_approved_qa_may_hold_or_repeat_what_it_has(tmp_path):
    existing = [f"別の言い方{i}" for i in range(MAX_APPROVED_ALIASES - 1)]
    _path, index = _index(tmp_path, [_record("q1", QUESTION, aliases=existing)])
    report = aliases.coverage(index)
    rows = aliases.candidates_from_coverage(index, report, kinds=perturb.KINDS, max_per_question=99)
    assert len(rows) == 1  # room for exactly one more
    full = [f"別の言い方{i}" for i in range(MAX_APPROVED_ALIASES)]
    _path2, index2 = _index(tmp_path, [_record("q1", QUESTION, aliases=full)])
    assert aliases.candidates_from_coverage(index2, aliases.coverage(index2), kinds=perturb.KINDS) == []
    forged = {"misses": [{"qa_id": "q1", "question": QUESTION, "kind": "casual_ending", "variant": QUESTION},
                         {"qa_id": "q1", "question": QUESTION, "kind": "casual_ending", "variant": "x" * 501},
                         {"qa_id": "q1", "question": QUESTION, "kind": "casual_ending", "variant": "改行\nあり"},
                         {"qa_id": "nope", "question": "q", "kind": "casual_ending", "variant": "他の質問"}]}
    _path3, index3 = _index(tmp_path, [_record("q1", QUESTION)])
    assert aliases.candidates_from_coverage(index3, forged) == []  # duplicate, too long, control char, unknown qa


def test_similarity_is_a_bigram_jaccard_after_the_lookup_normalization():
    assert aliases.similarity("パスワード再設定", "パスワード再設定") == 1.0
    assert aliases.similarity("パスワード再設定", "経費精算") == 0.0
    assert aliases.similarity("パスワード 再設定！", "パスワード再設定") == 1.0
    assert 0.3 < aliases.similarity("パスワードの再設定方法", "パスワード再設定の方法は？") < 1.0
    assert aliases.similarity("", "あ") == 0.0 and aliases.similarity("あ", "あ") == 1.0


def test_mining_maps_unanswered_questions_to_the_nearest_approved_question(tmp_path):
    _path, index = _index(tmp_path, [_record("q1", QUESTION), _record("q2", "経費精算の締め日は？")])
    questions = ["パスワードの再設定方法を教えて", QUESTION, "経費精算の締め日っていつですか", "全く関係のない話題について", "", "  "]
    rows = aliases.mine(index, questions, threshold=0.3)
    assert [(r["qa_id"], r["alias"]) for r in rows] == [("q1", "パスワードの再設定方法を教えて"), ("q2", "経費精算の締め日っていつですか")]
    assert all(r["source"] == "mined" and 0.3 <= r["similarity"] <= 1 and r["status"] == "pending" for r in rows)
    assert aliases.mine(index, questions, threshold=0.99) == []  # the exact question reaches an answer, the rest are not that close
    assert aliases.mine(index, ["パスワードの再設定方法を教えて"] * 3, threshold=0.3)[0]["qa_id"] == "q1"
    assert len(aliases.mine(index, ["パスワードの再設定方法を教えて"] * 3, threshold=0.3)) == 1  # no duplicate candidates


def test_mining_leaves_out_what_already_answers_and_other_tenants(tmp_path):
    _path, index = _index(tmp_path, [_record("q1", QUESTION, aliases=["パスワードを忘れた"])])
    assert lookup_approved_answer(index, "パスワードを忘れた", tenant_id="default") is not None
    assert aliases.mine(index, ["パスワードを忘れた"], threshold=0.1) == []
    assert aliases.mine(index, ["パスワードの再設定方法を教えて"], tenant_id="tenant_b", threshold=0.1) == []


# --- the command line ---------------------------------------------------------------------------------------


def _run(capsys, *argv):
    code = alias_candidates.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_the_coverage_command_writes_pending_candidates_and_never_touches_the_approved_file(tmp_path, capsys):
    path, _index_ = _index(tmp_path, [_record("q1", QUESTION)])
    before = path.read_text(encoding="utf-8")
    out = tmp_path / "out" / "cov.jsonl"
    code, summary = _run(capsys, "coverage", "--approved", str(path), "--out", str(out))
    assert code == 0 and summary["approved"] == 1 and summary["variants"] > 0 and summary["out"] == str(out)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == summary["candidates"] > 0 and {r["status"] for r in rows} == {"pending"}
    assert path.read_text(encoding="utf-8") == before
    code, error = _run(capsys, "coverage", "--approved", str(path), "--out", str(path))
    assert code == 3 and "cannot be the approved QA file" in error["error"] and path.read_text(encoding="utf-8") == before
    assert _run(capsys, "coverage", "--approved", str(path), "--out", str(out), "--kinds", "nope")[0] == 3


def test_the_mine_command_reads_a_questions_file(tmp_path, capsys, monkeypatch):
    path, _index_ = _index(tmp_path, [_record("q1", QUESTION)])
    questions = tmp_path / "questions.txt"
    questions.write_text("パスワードの再設定方法を教えて\n関係のない話\n\n", encoding="utf-8")
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    code, summary = _run(capsys, "mine", "--approved", str(path), "--questions", str(questions), "--threshold", "0.3")
    assert code == 0 and summary["questions"] == 2 and summary["candidates"] == 1
    default_out = tmp_path / "runs" / "alias_candidates" / "default_mine.jsonl"
    assert summary["out"] == str(default_out) and json.loads(default_out.read_text(encoding="utf-8"))["alias"].startswith("パスワード")
    assert _run(capsys, "mine", "--approved", str(path), "--questions", str(tmp_path / "missing.txt"))[0] == 3
    assert _run(capsys, "mine", "--approved", str(path), "--questions", str(questions), "--threshold", "0")[0] == 3
