import pytest

from tools.evaluate_unknown_abstention import classify_response


@pytest.mark.parametrize("mode,source", [("approved_exact_match", "approved_qa_exact"), ("approved_alias_match", "approved_qa_alias"), ("approved_colloquial_match", "approved_qa_colloquial")])
@pytest.mark.parametrize("source_only", [False, True])
def test_approved_answer_is_not_grounded_even_with_citation(mode, source, source_only):
    payload = {"answer_text": "回答", "citations": [{"source_doc": "doc.pdf"}], "answer_mode": "" if source_only else mode, "retrieval_source": source if source_only else ""}
    result = classify_response({"question": "unknown"}, payload, "")
    assert result["approved_exact_false_positive"] is True
    assert result["grounded_answer"] is False
    assert result["unsupported_answer"] is True and result["classification"] == "unsupported"


def test_summary_csv_json_and_help_contract(tmp_path, capsys, monkeypatch):
    import csv
    import json
    from tools import evaluate_unknown_abstention as evaluate
    result = evaluate.classify_response({"case_id": "x", "question": "q"}, {"answer_mode": "approved_colloquial_match", "answer_text": "a", "citations": [{"source_doc": "faq.pdf"}]}, "")
    summary = evaluate.summarize([result])
    assert summary["approved_exact_false_positive_count"] == 1 and summary["grounded_answer_count"] == 0
    csv_path = tmp_path / "results.csv"
    json_path = tmp_path / "results.jsonl"
    evaluate.write_csv(csv_path, [result])
    evaluate.write_jsonl(json_path, [result])
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        assert next(reader) == ["case_id", "category", "classification", "abstained", "grounded_answer", "unsupported_answer", "approved_exact_false_positive", "citations_count", "guard_reason", "used_fallback", "answer_mode", "retrieval_source", "error", "question", "answer_text"]
    assert json.loads(json_path.read_text(encoding="utf-8"))["approved_exact_false_positive"] is True
    import sys
    monkeypatch.setattr(sys, "argv", ["evaluate_unknown_abstention", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        evaluate.main()
    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert all(flag in help_text for flag in ["--cases", "--chat-url", "--output-dir", "--timeout"])
