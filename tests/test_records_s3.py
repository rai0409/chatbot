"""S3: the record level, what each level keeps, the judgements, the join with feedback, and the retention."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from rag_core import answer_cache, audit_log, llm_usage, records, runtime_settings
from webapi import main, metrics_registry
from webapi.routers import chat as chat_router

NOW = datetime(2026, 10, 29, 3, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_DIR", "")
    monkeypatch.setattr(config, "RECORDS_DIR", "")
    monkeypatch.setattr(config, "RECORDS_HASH_SALT", "salt-not-a-secret")
    monkeypatch.setattr(config, "RECORD_LEVEL_DEFAULT", "off")
    monkeypatch.setattr(config, "RECORD_LEVEL_MAX", "detailed")
    return tmp_path


def _keep(**kw):
    base = dict(tenant_id="tenant_a", request_id="req-1", trace_id="tr-1", user="fp-person-1", question="有給は何日前までに申請しますか。連絡先は taro@example.co.jp です",
                answer_text="3営業日前までです。", answer_mode="grounded", citations=[{"source_doc": "rules.pdf", "page": 12, "chunk_id": "c1", "extra": "x"}],
                latency_ms=420, generation_mode="llm", now=NOW)
    base.update(kw)
    return records.record_interaction(**base)


# --- the level -----------------------------------------------------------------------------------------------------------------------


def test_nothing_is_kept_by_default(store):
    assert records.effective_level("tenant_a") == "off"
    assert _keep() is None and list(records.iter_records(2, today=NOW.date())) == []


def test_the_level_is_set_per_tenant_with_a_reason_and_falls_back_to_the_global_one(store):
    records.set_level("standard", by="山田太郎", reason="pilot")
    records.set_level("anonymous", by="山田太郎", reason="tenant b is more careful", tenant_id="tenant_b")
    assert records.effective_level("tenant_a") == "standard" and records.effective_level("tenant_b") == "anonymous"
    records.set_level(None, by="山田太郎", reason="back to the global level", tenant_id="tenant_b")
    assert records.effective_level("tenant_b") == "standard"
    with pytest.raises(runtime_settings.SettingsError):
        records.set_level("standard", by="山田太郎", reason=" ")


def test_the_environment_ceiling_cannot_be_passed_from_the_console(store, monkeypatch):
    monkeypatch.setattr(config, "RECORD_LEVEL_MAX", "standard")
    with pytest.raises(records.RecordError, match="RECORD_LEVEL_MAX"):
        records.set_level("detailed", by="山田太郎", reason="more")
    monkeypatch.setattr(config, "RECORD_LEVEL_MAX", "detailed")
    records.set_level("detailed", by="山田太郎", reason="a week of evaluation")
    monkeypatch.setattr(config, "RECORD_LEVEL_MAX", "anonymous")  # the ceiling is lowered later: it wins at once
    assert records.effective_level("tenant_a") == "anonymous"
    with pytest.raises(records.RecordError):
        records.set_level("loud", by="山田太郎", reason="x")


# --- what each level keeps ---------------------------------------------------------------------------------------------------------------------


def test_anonymous_keeps_the_shape_only_and_a_stable_pseudonym(store):
    records.set_level("anonymous", by="a", reason="r")
    first, second = _keep(), _keep(user="fp-person-2"),
    again = _keep()
    assert "question" not in first and "answer_text" not in first and "citations" not in first and "ip" not in first
    assert first["answer_mode"] == "grounded" and first["citations_count"] == 1 and first["latency_ms"] == 420
    assert first["user"].startswith("h:") and first["user"] == again["user"] != second["user"] and "fp-person-1" not in json.dumps(first)


def test_standard_keeps_the_text_masks_addresses_and_keeps_the_digits(store):
    records.set_level("standard", by="a", reason="r")
    rec = _keep()
    assert rec["question"] == "有給は何日前までに申請しますか。連絡先は [email] です" and rec["answer_text"] == "3営業日前までです。"
    assert rec["user"] == "fp-person-1" and rec["citations"] == [{"source_doc": "rules.pdf", "page": 12, "chunk_id": "c1"}]
    assert "ip" not in rec and "user_agent" not in rec


def test_detailed_adds_the_device_and_a_long_text_is_cut(store):
    records.set_level("detailed", by="a", reason="r")
    rec = _keep(client_ip="203.0.113.7", user_agent="Browser/1.0", question="あ" * 9000)
    assert rec["ip"] == "203.0.113.7" and rec["user_agent"] == "Browser/1.0" and len(rec["question"]) == records.MAX_TEXT


def test_the_pseudonym_salt_is_made_once_when_none_is_configured(store, monkeypatch):
    monkeypatch.setattr(config, "RECORDS_HASH_SALT", "")
    one, two = records.pseudonym("fp"), records.pseudonym("fp")
    assert one == two and (store / "runs" / "records" / ".salt").exists() and records.pseudonym(None) is None


def test_a_day_is_a_file_and_a_failure_of_the_store_is_not_the_callers_problem(store):
    records.set_level("standard", by="a", reason="r")
    _keep()
    assert (store / "runs" / "records" / "records-2026-10-29.jsonl").exists()


# --- judgements and the join with feedback ---------------------------------------------------------------------------------------------------


def test_a_judgement_needs_a_reason_unless_the_answer_was_right_and_the_record_must_exist(store):
    records.set_level("standard", by="a", reason="r")
    rec = _keep(now=datetime.now(timezone.utc))
    with pytest.raises(records.RecordError):
        records.review(rec["id"], verdict="incorrect", reviewer="佐藤次郎")
    with pytest.raises(records.RecordError):
        records.review(rec["id"], verdict="maybe", reviewer="佐藤次郎")
    with pytest.raises(records.RecordError):
        records.review(rec["id"], verdict="incorrect", reviewer="佐藤次郎", reasons=["made_up"])
    with pytest.raises(records.RecordError, match="does not exist"):
        records.review("rec-nope", verdict="correct", reviewer="佐藤次郎")
    records.review(rec["id"], verdict="incorrect", reviewer="佐藤次郎", reasons=["outdated"], expected_answer="5営業日前までです。")
    records.review(rec["id"], verdict="partial", reviewer="田中太郎", note="条件の記載がない")  # the newest judgement counts
    got = records.get_record(rec["id"])
    assert got["review"]["verdict"] == "partial" and got["review"]["reviewer"] == "田中太郎"


def test_feedback_from_the_screen_is_joined_by_the_request_or_trace_id(store):
    records.set_level("standard", by="a", reason="r")
    rec = _keep(now=datetime.now(timezone.utc))
    audit_log.append_feedback_audit_event({"feedback_token": "tr-1", "feedback_type": "bad", "bad_reason": "wrong_doc",
                                           "comment": "古い", "tenant_id": "tenant_a", "request_id": "req-1", "trace_id": "tr-1"})
    got = records.get_record(rec["id"])
    assert got["feedback"]["type"] == "bad" and got["feedback"]["bad_reason"] == "wrong_doc"
    found = records.list_records(days=2, feedback="bad")
    assert found["total"] == 1 and records.list_records(days=2, feedback="good")["total"] == 0


def test_the_list_filters_and_pages_and_stays_inside_the_tenants_given(store):
    records.set_level("standard", by="a", reason="r")
    now = datetime.now(timezone.utc)
    for i, (tenant, mode, guard) in enumerate([("tenant_a", "grounded", None), ("tenant_a", "fallback", "no_results"), ("tenant_b", "grounded", None)]):
        _keep(tenant_id=tenant, answer_mode=mode, guard_reason=guard, request_id=f"r{i}", trace_id=f"t{i}", question=f"質問{i}", now=now + timedelta(seconds=i))
    everything = records.list_records(days=2)
    assert everything["total"] == 3 and [r["question"] for r in everything["records"]] == ["質問2", "質問1", "質問0"]  # newest first
    assert records.list_records(days=2, tenants=["tenant_a"])["total"] == 2
    assert records.list_records(days=2, tenants=[])["total"] == 0
    assert records.list_records(days=2, answer_mode="fallback")["total"] == 1 and records.list_records(days=2, abstained=True)["total"] == 1
    assert records.list_records(days=2, text="質問1")["total"] == 1
    page = records.list_records(days=2, limit=2, offset=2)
    assert page["total"] == 3 and len(page["records"]) == 1
    stats = records.stats(2)
    assert stats["total"] == 3 and stats["abstained"] == 1 and stats["answer_modes"] == {"grounded": 2, "fallback": 1}


# --- retention -------------------------------------------------------------------------------------------------------------------------------------


def _old_records(store, monkeypatch):
    monkeypatch.setattr(config, "RECORDS_RETENTION_DAYS", 100)
    monkeypatch.setattr(config, "RECORDS_DETAILED_RETENTION_DAYS", 30)
    records.set_level("detailed", by="a", reason="r")
    old = _keep(now=NOW - timedelta(days=150), client_ip="203.0.113.7", user_agent="B/1", request_id="old", trace_id="old")
    mid = _keep(now=NOW - timedelta(days=60), client_ip="203.0.113.8", user_agent="B/2", request_id="mid", trace_id="mid")
    new = _keep(now=NOW - timedelta(days=1), client_ip="203.0.113.9", user_agent="B/3", request_id="new", trace_id="new")
    return old, mid, new


def test_a_dry_run_only_reports(store, monkeypatch):
    _old_records(store, monkeypatch)
    result = records.purge(now=NOW, dry_run=True)
    assert result["records_anonymized"] == 1 and result["device_info_removed"] == 1 and result["files_deleted"] == 0
    assert all(r.get("question") for r in records.iter_records(500, today=NOW.date()))  # nothing was changed


def test_anonymize_drops_the_text_and_the_identity_and_keeps_the_counts_and_the_judgement(store, monkeypatch):
    old, mid, new = _old_records(store, monkeypatch)
    records.review(old["id"], verdict="correct", reviewer="佐藤次郎")
    records.purge(now=NOW, dry_run=False)
    rows = {r["request_id"]: r for r in records.iter_records(500, today=NOW.date())}
    assert rows["old"]["anonymized"] is True and all(f not in rows["old"] for f in ("question", "answer_text", "citations", "user", "ip", "user_agent"))
    assert rows["old"]["answer_mode"] == "grounded" and rows["old"]["citations_count"] == 1
    assert "ip" not in rows["mid"] and "user_agent" not in rows["mid"] and rows["mid"]["question"]  # device information expired, the text did not
    assert rows["new"]["ip"] == "203.0.113.9" and rows["new"]["question"]
    assert records.get_record(old["id"], days=500, today=NOW.date())["review"]["verdict"] == "correct"
    assert records.purge(now=NOW, dry_run=False)["records_anonymized"] == 0  # a second run changes nothing more


def test_delete_removes_an_expired_day_and_an_unknown_action_is_refused(store, monkeypatch):
    _old_records(store, monkeypatch)
    result = records.purge(now=NOW, dry_run=False, action="delete")
    assert result["files_deleted"] == 1
    assert not (store / "runs" / "records" / f"records-{(NOW - timedelta(days=150)).date().isoformat()}.jsonl").exists()
    with pytest.raises(records.RecordError):
        records.purge(now=NOW, action="shred")


def test_usage_files_expire_too(store, monkeypatch):
    monkeypatch.setattr(config, "LLM_USAGE_ENABLED", True)
    old = NOW - timedelta(days=500)
    llm_usage.record_call(feature="generate", tenant_id="t", profile="p", model="m", host="h", location="local", outcome="ok", now=old)
    llm_usage.record_call(feature="generate", tenant_id="t", profile="p", model="m", host="h", location="local", outcome="ok", now=NOW)
    assert llm_usage.purge(now=NOW, dry_run=True)["files_deleted"] == 1
    assert llm_usage.purge(now=NOW, dry_run=False)["files_deleted"] == 1
    assert not (store / "runs" / "usage" / f"llm_usage-{old.date().isoformat()}.jsonl").exists()
    assert (store / "runs" / "usage" / f"llm_usage-{NOW.date().isoformat()}.jsonl").exists()


# --- from the chat ---------------------------------------------------------------------------------------------------------------------------------------


def _fake_answer(question="質問です"):
    ans = SimpleNamespace(intent="faq", guard_reason=None, used_fallback=False, citations=[],
                          to_dict=lambda: {"answer_text": "回答です", "citations": [{"source_doc": "a.pdf", "page": 3, "chunk_id": "c9"}], "retrieved": [],
                                           "citations_count": 1})
    trace = {"request_id": "req-chat-1", "normalized_query": question, "intent": "faq", "final_guard_reason": None,
             "final_used_fallback": False, "citations_count": 1, "latency_ms": 7, "after_rerank": [], "answer_mode": "grounded"}
    return ans, trace


@pytest.fixture
def chat(store, monkeypatch):
    for name in ("API_AUTH_ENABLED", "API_AUTH_KEYS", "API_AUTH_TENANT_MAP", "RATE_LIMIT_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "CHAT_USE_TENANT_PROFILE", False)
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", False)
    answer_cache.clear()
    metrics_registry.reset()
    monkeypatch.setattr(chat_router, "ensure_openai_client", lambda base_url=None: object())
    monkeypatch.setattr(chat_router, "answer_query_with_trace", lambda *a, **k: _fake_answer())
    monkeypatch.setattr(chat_router, "_approved_qa_lookup", lambda *a, **k: None)
    return TestClient(main.app)


def test_a_chat_is_recorded_as_the_level_says_and_the_answer_is_not_changed(chat):
    body = {"question": "有給は何日前までですか", "tenant_id": "default"}
    off = chat.post("/chat", json=body)
    assert off.status_code == 200 and list(records.iter_records(2)) == []
    records.set_level("detailed", by="a", reason="r")
    on = chat.post("/chat", json=body, headers={"user-agent": "TestBrowser/1.0"})
    assert on.status_code == 200 and on.json()["answer_text"] == off.json()["answer_text"]
    [rec] = list(records.iter_records(2))
    assert rec["question"] == "有給は何日前までですか" and rec["answer_text"] == "回答です" and rec["request_id"] == "req-chat-1"
    assert rec["answer_mode"] == "grounded" and rec["citations"] == [{"source_doc": "a.pdf", "page": 3, "chunk_id": "c9"}]
    assert rec["user_agent"] == "TestBrowser/1.0" and rec["ip"] and rec["source"] == "chat" and rec["tenant_id"] == "default"


def test_a_failure_to_record_never_breaks_the_answer(chat, monkeypatch):
    records.set_level("standard", by="a", reason="r")
    monkeypatch.setattr(records, "record_interaction", lambda **k: 1 / 0)
    assert chat.post("/chat", json={"question": "質問", "tenant_id": "default"}).status_code == 200


def test_the_answer_says_when_the_ai_was_unavailable(chat, monkeypatch):
    ok = chat.post("/chat", json={"question": "質問", "tenant_id": "default"}).json()
    assert ok["service_notice"] == ""

    def degraded(*a, **k):
        ans, trace = _fake_answer()
        trace["generation_error_type"] = "ConnectionError"
        return ans, trace

    monkeypatch.setattr(chat_router, "answer_query_with_trace", degraded)
    records.set_level("standard", by="a", reason="r")
    answer = chat.post("/chat", json={"question": "質問", "tenant_id": "default"}).json()
    assert answer["service_notice"] == "llm_unavailable"
    assert list(records.iter_records(2))[-1]["degraded"] is True


def test_a_reached_limit_is_told_apart_from_a_failing_endpoint(chat, monkeypatch):
    def limited(*a, **k):
        ans, trace = _fake_answer()
        trace["generation_error_type"] = "QuotaExceeded"
        return ans, trace

    monkeypatch.setattr(chat_router, "answer_query_with_trace", limited)
    assert chat.post("/chat", json={"question": "質問", "tenant_id": "default"}).json()["service_notice"] == "quota_exceeded"
    html = chat.get("/chat-ui").text
    assert "quota_notice" in html and "本日の AI の利用上限に達した" in html
