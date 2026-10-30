import json

import pytest
from fastapi.testclient import TestClient

import config
from rag_core import suggest
from rag_core.approved_qa import load_approved_qa
from webapi import main
from webapi.routers import chat as chat_router


def _record(qa_id, question, tenant="default", aliases=None):
    record = {"qa_id": qa_id, "question": question, "approved_answer": "承認済み回答です。", "language": "ja",
              "approved_citations": [{"source_doc": "faq.pdf", "source_pages": [1], "chunk_id": f"c-{qa_id}"}],
              "tenant_id": tenant, "status": "approved"}
    if aliases is not None:
        record["approved_aliases"] = aliases
    return record


RECORDS = [
    _record("q1", "パスワード再設定の方法は？", aliases=["パスワードを忘れた", "パスワードリセット"]),
    _record("q2", "パスワードの有効期限は？"),
    _record("q3", "経費精算の締め日は？"),
    _record("q4", "手順パスワードの確認"),
    _record("t2-1", "パスワード再設定の方法は？", tenant="tenant_b"),
]


@pytest.fixture
def path(tmp_path):
    file = tmp_path / "approved.jsonl"
    file.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in RECORDS) + "\n", encoding="utf-8")
    return file


@pytest.fixture
def index(path):
    return load_approved_qa(path, tenant_id="default")


def _texts(result):
    return [item["qa_id"] for item in result]


def test_prefix_matches_come_before_matches_that_only_contain_the_text(index):
    result = suggest.suggest("パスワード", index=index)
    assert _texts(result) == ["q1", "q2", "q4"]  # q4 only contains it, although it is later in the file anyway
    assert [r["match"] for r in result] == ["prefix", "prefix", "contains"]
    reordered = suggest.suggest("パスワード", index=index, limit=10)
    assert reordered == result
    assert all(set(item) == {"text", "qa_id", "match", "via"} for item in result)  # no answer is returned
    assert result[0]["text"] == "パスワード再設定の方法は？"


def test_an_alias_match_shows_the_canonical_question_once(index):
    result = suggest.suggest("忘れた", index=index)
    assert result == [{"text": "パスワード再設定の方法は？", "qa_id": "q1", "match": "contains", "via": "alias"}]
    both = suggest.suggest("パスワードリセット", index=index)
    assert both == [{"text": "パスワード再設定の方法は？", "qa_id": "q1", "match": "prefix", "via": "alias"}]
    once = suggest.suggest("パスワード再設定", index=index)
    assert _texts(once) == ["q1"] and once[0]["via"] == "question"


def test_what_is_typed_is_normalized_like_the_lookup(index):
    assert _texts(suggest.suggest("ＰＡＳＳ", index=index)) == []
    assert _texts(suggest.suggest("パスワード 再設定 の 方法", index=index)) == ["q1"]
    assert _texts(suggest.suggest("パスワード再設定の方法？？", index=index)) == ["q1"]


def test_short_or_empty_input_and_missing_index_give_nothing(index):
    assert suggest.suggest("パ", index=index) == [] and suggest.suggest("", index=index) == []
    assert suggest.suggest("？？", index=index) == [] and suggest.suggest("パスワード", index=None) == []
    assert suggest.suggest("存在しない質問", index=index) == []


def test_the_limit_is_applied_and_bounded(index):
    assert _texts(suggest.suggest("パスワード", index=index, limit=1)) == ["q1"]
    assert len(suggest.suggest("パスワード", index=index, limit=0)) == 1  # at least one
    assert len(suggest.suggest("パスワード", index=index, limit=999)) == 3


def test_another_tenant_never_gets_a_question_of_this_index(index, path):
    assert suggest.suggest("パスワード", index=index, tenant_id="tenant_b") == []
    other = load_approved_qa(path, tenant_id="tenant_b")
    assert _texts(suggest.suggest("パスワード", index=other, tenant_id="tenant_b")) == ["t2-1"]


# --- the endpoint -----------------------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch, path, tmp_path):
    for name in ("API_AUTH_ENABLED", "API_AUTH_KEYS", "API_AUTH_TENANT_MAP", "RATE_LIMIT_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", True)
    monkeypatch.setattr(config, "APPROVED_QA_PATH", str(path))
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(chat_router, "_approved_qa_index", None)
    monkeypatch.setattr(chat_router, "_approved_qa_index_path", None)
    audit = []
    monkeypatch.setattr(chat_router, "append_audit_event", lambda kind, event: audit.append((kind, event)))
    return TestClient(main.app), audit


def test_the_endpoint_returns_questions_for_the_tenant_and_audits_nothing(api):
    client, audit = api
    resp = client.get("/chat/suggest", params={"q": "パスワード"})
    assert resp.status_code == 200
    assert [s["qa_id"] for s in resp.json()["suggestions"]] == ["q1", "q2", "q4"]
    assert "承認済み回答です" not in resp.text and audit == []
    other = client.get("/chat/suggest", params={"q": "パスワード", "tenant_id": "tenant_b"})
    assert [s["qa_id"] for s in other.json()["suggestions"]] == ["t2-1"]
    assert [s["qa_id"] for s in client.get("/chat/suggest", params={"q": "パスワード", "limit": 1}).json()["suggestions"]] == ["q1"]


def test_the_endpoint_validates_its_input(api):
    client, _audit = api
    assert client.get("/chat/suggest", params={"q": "a" * 201}).status_code == 422
    assert client.get("/chat/suggest", params={"q": "パスワード", "limit": 11}).status_code == 422
    assert client.get("/chat/suggest", params={"q": "パスワード", "limit": 0}).status_code == 422
    assert client.get("/chat/suggest").json() == {"suggestions": []}  # no text yet


def test_without_approved_qa_there_are_no_suggestions(api, monkeypatch):
    client, _audit = api
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    assert client.get("/chat/suggest", params={"q": "パスワード"}).json() == {"suggestions": []}


def test_the_endpoint_needs_the_same_authorization_as_the_other_chat_endpoints(api, monkeypatch):
    client, _audit = api
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "key-a")
    monkeypatch.setenv("API_AUTH_TENANT_MAP", "key-a=tenant_a")
    assert client.get("/chat/suggest", params={"q": "パスワード", "tenant_id": "tenant_a"}).status_code == 401
    forbidden = client.get("/chat/suggest", params={"q": "パスワード", "tenant_id": "tenant_b"}, headers={"X-Api-Key": "key-a"})
    assert forbidden.status_code == 403
    allowed = client.get("/chat/suggest", params={"q": "パスワード", "tenant_id": "tenant_a"}, headers={"X-Api-Key": "key-a"})
    assert allowed.status_code == 200 and allowed.json() == {"suggestions": []}  # no approved QA for tenant_a here


def test_the_shared_loader_still_serves_the_exact_lookup(api):
    from rag_core.approved_qa import ApprovedAnswer

    found = main._approved_qa_lookup("パスワード再設定の方法は？", "default")
    assert isinstance(found, ApprovedAnswer) and found.qa_id == "q1"
    assert main._approved_qa_lookup("存在しない質問", "default") is None
    alias = main._approved_qa_lookup("パスワードを忘れた", "default")
    assert alias.match_type == "alias" and alias.qa_id == "q1"
    assert main._approved_qa_index_for("default") is main._approved_qa_index  # one cached index
