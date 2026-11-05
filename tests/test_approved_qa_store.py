"""Managing the approved-QA file from the console: create, edit, approve, import, history. Synthetic data only."""
from __future__ import annotations

import json

import pytest

import config
from rag_core import approved_qa, approved_qa_store as store

TENANT = "tenant_a"
ALICE = {"name": "山田太郎", "id": "fp-alice", "verified": True}
BOB = {"name": "佐藤次郎", "id": "fp-bob", "verified": True}
CITE = [{"source_doc": "manual.pdf", "source_pages": [2], "title": "利用案内"}]


@pytest.fixture(autouse=True)
def live(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APPROVED_QA_PATH", str(tmp_path / "approved" / "live.jsonl"))
    monkeypatch.delenv("APPROVED_QA_REQUIRE_APPROVER", raising=False)
    return tmp_path / "approved" / "live.jsonl"


def _new(question="申請期限はいつですか？", answer="毎月末日です。", **kw):
    return store.save({"tenant_id": TENANT, "question": question, "approved_answer": answer, "citations": CITE, **kw},
                      by=ALICE, reason="新規")


def test_an_empty_store_lists_nothing_and_is_healthy():
    listing = store.list_records()
    assert listing["total"] == 0 and listing["counts"] == {"draft": 0, "approved": 0, "rejected": 0}
    assert store.health()["errors"] == []


def test_a_new_record_is_a_draft_with_an_id_and_an_author(live):
    record = _new()
    assert record["status"] == "draft" and record["qa_id"].startswith("qa-") and record["created_by"] == "山田太郎"
    assert record["normalized_question"] and live.exists()
    assert store.list_records()["counts"]["draft"] == 1


def test_a_draft_is_not_served_to_chat(live):
    _new()
    index = approved_qa.load_approved_qa(str(live), tenant_id=TENANT)
    assert index is None or not index.records


def test_approval_needs_a_valid_citation(live):
    record = store.save({"tenant_id": TENANT, "question": "質問です", "approved_answer": "回答です"}, by=ALICE, reason="新規")
    with pytest.raises(store.StoreError, match="citation"):
        store.set_status(TENANT, record["qa_id"], "approved", by=BOB, reason="承認")


def test_an_approved_record_is_served_to_chat_with_its_aliases(live):
    record = _new(aliases=["締め切りはいつ？"])
    approved = store.set_status(TENANT, record["qa_id"], "approved", by=BOB, reason="内容を確認した")
    assert approved["status"] == "approved" and approved["approved_by"] == "佐藤次郎" and approved["approved_aliases"] == ["締め切りはいつ？"]
    index = approved_qa.load_approved_qa(str(live), tenant_id=TENANT)
    found = approved_qa.lookup_approved_answer(index, "締め切りはいつ？", tenant_id=TENANT)
    assert found is not None and found.approved_answer == "毎月末日です。" and found.match_type == "alias"


def test_a_draft_keeps_aliases_as_proposed_because_the_file_allows_them_only_on_approved_records(live):
    record = _new(aliases=["期限は？"])
    assert record["status"] == "draft" and record.get("approved_aliases") in (None, []) and record["proposed_aliases"] == ["期限は？"]
    assert store.health()["errors"] == []


def test_changing_an_approved_answer_takes_it_back_to_draft(live):
    record = _new()
    store.set_status(TENANT, record["qa_id"], "approved", by=BOB, reason="確認")
    edited = store.save({"tenant_id": TENANT, "qa_id": record["qa_id"], "question": record["question"],
                         "approved_answer": "毎月25日です。", "citations": CITE}, by=ALICE, reason="期限が変わった")
    assert edited["status"] == "draft" and edited["reverted_to_draft"] is True
    index = approved_qa.load_approved_qa(str(live), tenant_id=TENANT)
    assert index is None or not index.records  # no longer served until approved again


def test_changing_only_tags_keeps_the_approval(live):
    record = _new()
    store.set_status(TENANT, record["qa_id"], "approved", by=BOB, reason="確認")
    edited = store.save({"tenant_id": TENANT, "qa_id": record["qa_id"], "question": record["question"],
                         "approved_answer": record["approved_answer"], "citations": CITE, "tags": ["手続き"]}, by=ALICE, reason="分類")
    assert edited["status"] == "approved" and edited["tags"] == ["手続き"] and not edited.get("reverted_to_draft")


def test_a_second_person_can_be_required(live, monkeypatch):
    monkeypatch.setenv("APPROVED_QA_REQUIRE_APPROVER", "true")
    record = _new()
    with pytest.raises(store.StoreError, match="own"):
        store.set_status(TENANT, record["qa_id"], "approved", by=ALICE, reason="自分で承認")
    assert store.set_status(TENANT, record["qa_id"], "approved", by=BOB, reason="確認")["status"] == "approved"


def test_the_same_person_may_approve_when_it_is_not_required(live):
    record = _new()
    assert store.set_status(TENANT, record["qa_id"], "approved", by=ALICE, reason="確認")["status"] == "approved"


def test_two_approved_records_cannot_share_a_question(live):
    first = _new()
    store.set_status(TENANT, first["qa_id"], "approved", by=BOB, reason="確認")
    second = _new(answer="別の回答です。")
    with pytest.raises(store.StoreError, match="duplicate"):
        store.set_status(TENANT, second["qa_id"], "approved", by=BOB, reason="確認")
    assert store.get(TENANT, second["qa_id"])["status"] == "draft"


def test_the_same_question_may_exist_in_two_tenants(live):
    store.save({"tenant_id": "tenant_b", "question": "申請期限はいつですか？", "approved_answer": "別テナントの回答", "citations": CITE}, by=ALICE, reason="x")
    first = _new()
    store.set_status(TENANT, first["qa_id"], "approved", by=BOB, reason="確認")


def test_an_alias_that_collides_with_another_question_is_refused(live):
    first = _new()
    store.set_status(TENANT, first["qa_id"], "approved", by=BOB, reason="確認")
    other = _new(question="解約はいつまで？", answer="月末です。", aliases=["申請期限はいつですか？"])
    with pytest.raises(store.StoreError):
        store.set_status(TENANT, other["qa_id"], "approved", by=BOB, reason="確認")


@pytest.mark.parametrize("payload", [
    {"question": "", "approved_answer": "a"}, {"question": "q", "approved_answer": ""}, {"question": "q" * 501, "approved_answer": "a"},
    {"question": "q", "approved_answer": "a" * 5001}, {"question": "q\x00", "approved_answer": "a"},
    {"question": "q", "approved_answer": "a", "citations": [{"source_doc": "/etc/passwd"}]},
    {"question": "q", "approved_answer": "a", "citations": [{"source_doc": "../x.pdf"}]},
    {"question": "q", "approved_answer": "a", "citations": [{"source_doc": "https://x.example/a.pdf"}]},
    {"question": "q", "approved_answer": "a", "citations": [{"source_doc": "a.pdf", "source_pages": [0]}]},
    {"question": "q", "approved_answer": "a", "aliases": [f"x{i}" for i in range(21)]},
    {"question": "q", "approved_answer": "a", "tags": [f"t{i}" for i in range(21)]},
])
def test_invalid_input_is_refused(payload):
    with pytest.raises(store.StoreError):
        store.save({"tenant_id": TENANT, **payload}, by=ALICE, reason="試験")
    assert store.list_records()["total"] == 0


def test_a_reason_and_a_name_are_required():
    with pytest.raises(store.StoreError):
        store.save({"tenant_id": TENANT, "question": "q", "approved_answer": "a"}, by=ALICE, reason="")
    with pytest.raises(store.StoreError):
        store.save({"tenant_id": TENANT, "question": "q", "approved_answer": "a"}, by={"name": "", "id": None, "verified": False}, reason="x")


def test_a_stale_edit_is_a_conflict(live):
    record = _new()
    store.save({"tenant_id": TENANT, "qa_id": record["qa_id"], "question": record["question"], "approved_answer": "更新1", "citations": CITE},
               by=ALICE, reason="a", expected_updated_at=record["updated_at"])
    with pytest.raises(store.Conflict):
        store.save({"tenant_id": TENANT, "qa_id": record["qa_id"], "question": record["question"], "approved_answer": "更新2", "citations": CITE},
                   by=BOB, reason="b", expected_updated_at=record["updated_at"])


def test_reject_and_delete(live):
    record = _new()
    rejected = store.set_status(TENANT, record["qa_id"], "rejected", by=BOB, reason="不要")
    assert rejected["status"] == "rejected"
    approved = _new(question="別の質問です")
    store.set_status(TENANT, approved["qa_id"], "approved", by=BOB, reason="確認")
    with pytest.raises(store.StoreError, match="approved"):
        store.delete(TENANT, approved["qa_id"], by=ALICE, reason="削除")  # an approved record is first taken back
    store.delete(TENANT, record["qa_id"], by=ALICE, reason="不要なため削除")
    assert store.get(TENANT, record["qa_id"]) is None


def test_every_change_is_in_the_history_with_before_and_after(live):
    record = _new()
    store.set_status(TENANT, record["qa_id"], "approved", by=BOB, reason="確認した")
    rows = store.history(TENANT, record["qa_id"])
    assert [r["action"] for r in rows] == ["status", "create"]
    assert rows[0]["by"] == "佐藤次郎" and rows[0]["reason"] == "確認した" and rows[0]["before"]["status"] == "draft" and rows[0]["after"]["status"] == "approved"


def test_the_previous_version_of_the_file_is_kept(live):
    _new()
    _new(question="別の質問です")
    assert live.with_name(live.name + ".prev").exists()


def test_listing_filters_by_tenant_status_and_text(live):
    a = _new()
    store.set_status(TENANT, a["qa_id"], "approved", by=BOB, reason="確認")
    store.save({"tenant_id": "tenant_b", "question": "解約の方法は？", "approved_answer": "画面から", "citations": CITE}, by=ALICE, reason="x")
    assert store.list_records(tenants={TENANT})["total"] == 1
    assert store.list_records(tenants={"tenant_b"})["rows"][0]["status"] == "draft"
    assert store.list_records(status="approved")["total"] == 1
    assert store.list_records(query="解約")["total"] == 1
    assert store.list_records(tenants={"nobody"})["total"] == 0
    assert store.list_records(limit=1, offset=1)["rows"][0]["qa_id"] in {r["qa_id"] for r in store.list_records()["rows"]}


# --- import ---------------------------------------------------------------------------------------------------------------------------------

HEADER = "質問,正解回答,出典文書,出典ページ,出典タイトル,分類,テナント,文書版,有効,言い換え,除外質問,必須キーワード,備考"


def _csv(*rows):
    return "\n".join([HEADER, *rows])


def test_a_csv_preview_changes_nothing(live):
    text = _csv('申請期限は？,毎月末日です。,manual.pdf,2,利用案内,手続き,tenant_a,v1,有効,"期限はいつ？,締め切りは？",,,')
    result = store.import_rows(text, default_tenant=TENANT, allowed_tenants=None, apply=False, by=ALICE, reason="")
    assert result["counts"]["create"] == 1 and result["counts"]["error"] == 0
    assert store.list_records()["total"] == 0 and not live.exists()


def test_applying_an_import_creates_drafts_never_approved_records(live):
    text = _csv('申請期限は？,毎月末日です。,manual.pdf,"2,3",利用案内,手続き,tenant_a,v1,有効,"期限はいつ？,締め切りは？",,,')
    result = store.import_rows(text, default_tenant=TENANT, allowed_tenants=None, apply=True, by=ALICE, reason="Excel から取り込み")
    assert result["counts"]["create"] == 1
    record = store.list_records()["rows"][0]
    full = store.get(TENANT, record["qa_id"])
    assert full["status"] == "draft" and full["tags"] == ["手続き"] and full["proposed_aliases"] == ["期限はいつ？", "締め切りは？"]
    assert full["approved_citations"][0]["source_doc"] == "manual.pdf" and full["approved_citations"][0]["source_pages"] == [2, 3]
    assert full["doc_version"] == "v1"


def test_rows_with_problems_are_reported_by_line_and_the_rest_is_imported(live):
    text = _csv("良い質問です,良い回答です,a.pdf,1,,,tenant_a,,有効,,,,",
                ",回答だけ,a.pdf,1,,,tenant_a,,有効,,,,",
                "質問だけ,,a.pdf,1,,,tenant_a,,有効,,,,",
                "別の質問です,回答,/abs/a.pdf,1,,,tenant_a,,有効,,,,",
                "無効な行です,回答,a.pdf,1,,,tenant_a,,無効,,,,")
    result = store.import_rows(text, default_tenant=TENANT, allowed_tenants=None, apply=True, by=ALICE, reason="取り込み")
    assert result["counts"] == {"create": 1, "update": 0, "skip": 1, "error": 3}
    errors = [r for r in result["rows"] if r["action"] == "error"]
    assert [r["line"] for r in errors] == [3, 4, 5]
    assert store.list_records()["total"] == 1


def test_an_existing_question_is_skipped_or_updated(live):
    _new()
    text = _csv("申請期限はいつですか？,新しい回答です。,manual.pdf,2,,,tenant_a,,有効,,,,")
    skipped = store.import_rows(text, default_tenant=TENANT, allowed_tenants=None, apply=True, by=ALICE, reason="取り込み")
    assert skipped["counts"]["skip"] == 1 and store.list_records()["rows"][0]["approved_answer"] == "毎月末日です。"
    updated = store.import_rows(text, default_tenant=TENANT, allowed_tenants=None, apply=True, by=ALICE, reason="取り込み", on_conflict="update")
    assert updated["counts"]["update"] == 1 and store.list_records()["rows"][0]["approved_answer"] == "新しい回答です。"


def test_an_import_cannot_write_to_a_tenant_the_administrator_does_not_reach(live):
    text = _csv("質問です,回答です,a.pdf,1,,,tenant_b,,有効,,,,")
    result = store.import_rows(text, default_tenant=TENANT, allowed_tenants={TENANT}, apply=True, by=ALICE, reason="取り込み")
    assert result["counts"]["error"] == 1 and "tenant" in result["rows"][0]["message"]
    assert store.list_records()["total"] == 0


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp932"])
def test_csv_encodings_of_japanese_offices_are_read(live, encoding):
    raw = _csv("質問です,回答です,a.pdf,1,,,tenant_a,,有効,,,,").encode(encoding)
    result = store.import_rows(raw, default_tenant=TENANT, allowed_tenants=None, apply=False, by=ALICE, reason="")
    assert result["counts"]["create"] == 1


def test_a_file_that_is_not_the_template_is_refused():
    with pytest.raises(store.StoreError, match="header"):
        store.import_rows("a,b\n1,2", default_tenant=TENANT, allowed_tenants=None, apply=False, by=ALICE, reason="")


def test_import_limits():
    with pytest.raises(store.StoreError, match="too many"):
        store.import_rows(_csv(*["q{0},a,a.pdf,1,,,tenant_a,,有効,,,,".format(i) for i in range(store.MAX_IMPORT_ROWS + 1)]),
                          default_tenant=TENANT, allowed_tenants=None, apply=False, by=ALICE, reason="")
    with pytest.raises(store.StoreError, match="large"):
        store.import_rows("x" * (store.MAX_IMPORT_BYTES + 1), default_tenant=TENANT, allowed_tenants=None, apply=False, by=ALICE, reason="")


def test_an_apply_needs_a_reason(live):
    with pytest.raises(store.StoreError):
        store.import_rows(_csv("質問です,回答です,a.pdf,1,,,tenant_a,,有効,,,,"), default_tenant=TENANT, allowed_tenants=None, apply=True, by=ALICE, reason="")


def test_export_is_jsonl_of_the_chosen_tenants(live):
    a = _new()
    store.save({"tenant_id": "tenant_b", "question": "他社の質問", "approved_answer": "他社の回答", "citations": CITE}, by=ALICE, reason="x")
    lines = store.export_jsonl(tenants={TENANT}).strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["qa_id"] == a["qa_id"]


def test_the_template_has_the_columns_of_the_excel_template():
    assert store.template_csv().splitlines()[0] == HEADER


def test_a_broken_file_is_reported_not_overwritten(live):
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text("{not json\n" + json.dumps({"qa_id": "x"}) + "\n", encoding="utf-8")
    assert store.health()["unreadable_lines"] == 1
    with pytest.raises(store.StoreError, match="unreadable"):
        _new()  # saving would drop the unreadable line: refuse and say so
    assert "{not json" in live.read_text(encoding="utf-8")


def test_writing_to_the_default_sample_path_is_refused_with_a_clear_reason(monkeypatch, tmp_path):
    # APPROVED_QA_PATH not set: the default points into eval/cases of the code tree, which is not a place for live data
    monkeypatch.setattr(config, "APPROVED_QA_PATH", str(tmp_path / "eval" / "cases" / "approved_qa_sample.jsonl"))
    with pytest.raises(store.StoreError, match="APPROVED_QA_PATH"):
        _new()
    assert not (tmp_path / "eval").exists()
    assert store.list_records()["total"] == 0  # reading is still fine


def test_the_health_says_when_the_path_is_the_default(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APPROVED_QA_PATH", str(tmp_path / "eval" / "cases" / "approved_qa_sample.jsonl"))
    assert store.health()["path_is_default"] is True
