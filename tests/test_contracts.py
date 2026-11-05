import json
from datetime import date, datetime, timezone

import pytest

import config
from rag_core import contracts as c

T0 = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 27)
TENANT = "tenant_a"


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTRACTS_DIR", str(tmp_path / "contracts"))
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)


def _extract(**fields):
    base = {"counterparty": {"value": "株式会社サンプル", "confidence": 0.9, "page": 1, "text": "甲 株式会社サンプル"},
            "end_date": {"value": "2027-03-31", "confidence": 0.95, "page": 3, "text": "令和9年3月31日まで"}}
    base.update({k: v if isinstance(v, dict) else {"value": v} for k, v in fields.items()})
    return {"schema": c.EXTRACT_SCHEMA, "fields": base}


def _import(contract_id="c1", **fields):
    return c.import_extracted(TENANT, contract_id, _extract(**fields), source_doc="業務委託契約.pdf", created_by="山田太郎", now=T0)


@pytest.mark.parametrize("text, expected", [
    ("2026-10-27", date(2026, 10, 27)), ("2026/1/5", date(2026, 1, 5)), ("2026.10.27", date(2026, 10, 27)),
    ("2026年10月27日", date(2026, 10, 27)), ("２０２６年１０月２７日", date(2026, 10, 27)), ("令和8年10月27日", date(2026, 10, 27)),
    ("令和元年5月1日", date(2019, 5, 1)), ("平成31年4月30日", date(2019, 4, 30)), ("昭和64年1月7日", date(1989, 1, 7)),
    (date(2026, 1, 2), date(2026, 1, 2)), (datetime(2026, 1, 2, 3), date(2026, 1, 2)),
])
def test_dates_are_read_in_the_forms_contracts_use(text, expected):
    assert c.parse_date(text) == expected


@pytest.mark.parametrize("text", ["", None, "2026-13-01", "2026-02-30", "来月", "2026年10月", "令和8年13月1日", "R8.10.27", "27/10/2026x"])
def test_something_that_is_not_a_date_is_refused(text):
    with pytest.raises(ValueError):
        c.parse_date(text)


@pytest.mark.parametrize("name, value, expected", [
    ("auto_renewal", True, True), ("auto_renewal", "はい", True), ("auto_renewal", "なし", False), ("auto_renewal", "false", False),
    ("renewal_notice_days", "９０", 90), ("renewal_notice_days", 30.0, 30), ("renewal_notice_days", 0, 0),
    ("amount", "1,200,000", 1200000.0), ("amount", 0, 0.0), ("contact_email", " Taro@Example.co.jp ", "Taro@Example.co.jp"),
    ("counterparty", "  株式会社サンプル ", "株式会社サンプル"), ("end_date", "令和9年3月31日", "2027-03-31"),
])
def test_values_are_stored_in_their_normal_form(name, value, expected):
    assert c.normalize(name, value) == expected


@pytest.mark.parametrize("name, value", [
    ("auto_renewal", "多分"), ("renewal_notice_days", -1), ("renewal_notice_days", 3651), ("renewal_notice_days", 1.5),
    ("renewal_notice_days", True), ("renewal_notice_days", "abc"), ("amount", -5), ("amount", "abc"), ("amount", True),
    ("amount", float("inf")), ("contact_email", "not-an-address"), ("counterparty", ""), ("counterparty", "x" * 501),
    ("counterparty", "a\x00b"), ("end_date", "2026-02-30"),
])
def test_an_invalid_value_is_refused(name, value):
    with pytest.raises((ValueError, TypeError)):
        c.normalize(name, value)


def test_an_extraction_is_stored_as_extracted_never_as_confirmed_and_bad_values_are_not_stored():
    out = _import(auto_renewal=True, renewal_notice_days={"value": -30, "confidence": 0.99}, termination_notice_days="90",
                  governing_law=None, amount="abc", bogus="x", title={"value": "業務委託契約", "page": 1})
    contract = out["contract"]
    assert out["rejected"] == {"renewal_notice_days": "not stored: outside 0..3650", "amount": "not stored: not a number", "bogus": "unknown field"}
    assert out["kept"] == [] and contract["rev"] == 0 and contract["source_doc"] == "業務委託契約.pdf"
    fields = contract["fields"]
    assert fields["counterparty"] == {"value": "株式会社サンプル", "state": "extracted", "confidence": 0.9,
                                      "evidence": {"page": 1, "text": "甲 株式会社サンプル"}, "updated_by": "山田太郎", "updated_at": T0.isoformat()}
    assert fields["end_date"]["value"] == "2027-03-31" and fields["end_date"]["state"] == "extracted"
    assert fields["termination_notice_days"]["value"] == 90 and fields["auto_renewal"]["value"] is True
    assert fields["renewal_notice_days"]["state"] == "missing" and fields["amount"]["state"] == "missing"
    assert fields["governing_law"]["state"] == "missing" and fields["title"]["evidence"] == {"page": 1, "text": ""}
    assert all(f["state"] != "confirmed" for f in fields.values())
    assert c.status_of(contract, TODAY) == "needs_review"


def test_the_extraction_schema_and_shape_are_checked():
    for bad in ({}, {"schema": "other", "fields": {}}, {"schema": c.EXTRACT_SCHEMA}, {"schema": c.EXTRACT_SCHEMA, "fields": []}, "text"):
        with pytest.raises(c.ContractError):
            c.import_extracted(TENANT, "c1", bad, created_by="山田太郎")
    assert c.list_contracts(TENANT) == []
    with pytest.raises(c.ContractError, match="who made the change"):
        c.import_extracted(TENANT, "c1", _extract(), created_by=" ")


def test_only_a_person_confirms_or_corrects_and_the_status_follows():
    _import()
    with pytest.raises(c.ContractError, match="no value to confirm"):
        c.confirm_field(TENANT, "c1", "title", by="佐藤次郎")
    c.confirm_field(TENANT, "c1", "counterparty", by="佐藤次郎", now=T0)
    assert c.status_of(c.get(TENANT, "c1"), TODAY) == "needs_review"  # the end date is still unconfirmed
    done = c.set_field(TENANT, "c1", "end_date", "令和9年3月31日", by="佐藤次郎", note="原本で確認", now=T0)
    end = done["fields"]["end_date"]
    assert end["state"] == "corrected" and end["value"] == "2027-03-31" and end["note"] == "原本で確認" and end["updated_by"] == "佐藤次郎"
    assert c.status_of(done, TODAY) == "active" and c.status_of(done, date(2027, 4, 1)) == "expired"
    assert [(h["action"], h["field"]) for h in done["history"][-2:]] == [("confirmed", "counterparty"), ("corrected", "end_date")]
    assert done["history"][-1]["old"] == "2027-03-31" and done["history"][-1]["new"] == "2027-03-31"


def test_bad_corrections_and_unknown_fields_are_refused():
    _import()
    for call in (lambda: c.set_field(TENANT, "c1", "end_date", "2026-02-30", by="a"), lambda: c.set_field(TENANT, "c1", "nope", "x", by="a"),
                 lambda: c.confirm_field(TENANT, "c1", "nope", by="a"), lambda: c.set_field(TENANT, "c1", "title", "x", by=""),
                 lambda: c.set_field(TENANT, "missing", "title", "x", by="a")):
        with pytest.raises(c.ContractError):
            call()
    assert c.get(TENANT, "c1")["fields"]["end_date"]["state"] == "extracted"


def test_a_later_extraction_does_not_overwrite_what_a_person_decided():
    _import()
    c.confirm_field(TENANT, "c1", "end_date", by="佐藤次郎")
    c.set_field(TENANT, "c1", "counterparty", "株式会社サンプル 東京", by="佐藤次郎")
    again = c.import_extracted(TENANT, "c1", _extract(end_date="2028-03-31", title="新しい題名"), created_by="山田太郎", now=T0)
    fields = again["contract"]["fields"]
    assert sorted(again["kept"]) == ["counterparty", "end_date"]
    assert fields["end_date"]["value"] == "2027-03-31" and fields["end_date"]["state"] == "confirmed"
    assert fields["counterparty"]["value"] == "株式会社サンプル 東京" and fields["title"]["state"] == "extracted"
    assert [h for h in again["contract"]["history"] if h["action"] == "extraction_differs"] == [
        {"at": T0.isoformat(), "by": "山田太郎", "action": "extraction_differs", "field": "counterparty", "old": "株式会社サンプル 東京", "new": "株式会社サンプル"},
        {"at": T0.isoformat(), "by": "山田太郎", "action": "extraction_differs", "field": "end_date", "old": "2027-03-31", "new": "2028-03-31"}]


def _decided(contract_id="c1", **fields):
    _import(contract_id, **fields)
    for name in ("counterparty", "end_date", *fields):
        c.confirm_field(TENANT, contract_id, name, by="佐藤次郎")


def test_deadlines_are_computed_from_the_end_date_and_the_notice_periods():
    _decided(auto_renewal=True, renewal_notice_days=90, termination_notice_days=30)
    found = c.deadlines(c.get(TENANT, "c1"), TODAY)
    assert [(d["kind"], d["date"], d["days_left"], d["verified"]) for d in found] == [
        ("end", "2027-03-31", 155, True), ("non_renewal_notice", "2026-12-31", 65, True), ("termination_notice", "2027-03-01", 125, True)]
    assert found[1]["basis"] == ["end_date", "auto_renewal", "renewal_notice_days"] and found[0]["label"] == "契約の終了日"


def test_a_deadline_resting_on_an_unconfirmed_value_says_so():
    _import(auto_renewal=True, renewal_notice_days=90)
    c.confirm_field(TENANT, "c1", "end_date", by="佐藤次郎")
    found = {d["kind"]: d for d in c.deadlines(c.get(TENANT, "c1"), TODAY)}
    assert found["end"]["verified"] is True and found["non_renewal_notice"]["verified"] is False
    c.confirm_field(TENANT, "c1", "auto_renewal", by="佐藤次郎")
    c.set_field(TENANT, "c1", "renewal_notice_days", 60, by="佐藤次郎")
    found = {d["kind"]: d for d in c.deadlines(c.get(TENANT, "c1"), TODAY)}
    assert found["non_renewal_notice"]["verified"] is True and found["non_renewal_notice"]["date"] == "2027-01-30"


def test_a_notice_for_non_renewal_needs_an_auto_renewing_contract_and_no_end_date_means_no_deadline():
    _decided(auto_renewal=False, renewal_notice_days=90)
    assert [d["kind"] for d in c.deadlines(c.get(TENANT, "c1"), TODAY)] == ["end"]
    c.create(TENANT, "blank", created_by="山田太郎")
    assert c.deadlines(c.get(TENANT, "blank"), TODAY) == [] and c.status_of(c.get(TENANT, "blank"), TODAY) == "needs_review"


def test_upcoming_lists_the_window_earliest_first_across_contracts_and_marks_the_status():
    _decided("far", auto_renewal=True, renewal_notice_days=30)
    c.set_field(TENANT, "far", "end_date", "2028-01-01", by="佐藤次郎")
    _decided("soon", termination_notice_days=10)
    c.set_field(TENANT, "soon", "end_date", "2026-11-20", by="佐藤次郎")
    _import("draft", termination_notice_days=5)
    c.set_field(TENANT, "draft", "end_date", "2026-10-20", by="佐藤次郎")  # ended 7 days ago, counterparty unconfirmed
    rows = c.upcoming(TENANT, within_days=60, today=TODAY)
    assert [(r["contract_id"], r["kind"], r["date"], r["status"]) for r in rows] == [
        ("draft", "termination_notice", "2026-10-15", "needs_review"), ("draft", "end", "2026-10-20", "needs_review"),
        ("soon", "termination_notice", "2026-11-10", "active"), ("soon", "end", "2026-11-20", "active")]
    assert all(r["contract_id"] != "far" for r in rows) and rows[0]["counterparty"] == "株式会社サンプル"
    assert c.upcoming(TENANT, within_days=60, today=TODAY, overdue_days=3) == [r for r in rows if r["days_left"] >= -3]
    assert c.upcoming("tenant_b", today=TODAY) == []


def test_ids_tenants_and_the_files_are_checked(tmp_path):
    for bad in ("", "Upper", "a b", "../x", "x" * 65, "日本語"):
        with pytest.raises(c.ContractError, match="invalid contract id"):
            c.get(TENANT, bad)
    for tenant in ("../x", "a/b", "", None):
        with pytest.raises(c.ContractError, match="invalid tenant"):
            c.list_contracts(tenant)
    c.create(TENANT, "c1", created_by="山田太郎")
    with pytest.raises(c.ContractError, match="already exists"):
        c.create(TENANT, "c1", created_by="山田太郎")
    with pytest.raises(c.ContractError, match="not found"):
        c.get("tenant_b", "c1")
    path = c._path(TENANT, "c1")
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(c.ContractError, match="unreadable"):
        c.get(TENANT, "c1")
    assert c.list_contracts(TENANT) == []  # a broken file is skipped, not fatal
    path.write_text(json.dumps({"schema": 9}), encoding="utf-8")
    with pytest.raises(c.ContractError, match="unsupported"):
        c.get(TENANT, "c1")


def test_a_stale_write_is_refused():
    c.create(TENANT, "c1", created_by="山田太郎")
    stale = c.get(TENANT, "c1")
    c.set_field(TENANT, "c1", "title", "題名", by="山田太郎")
    with pytest.raises(c.ContractError, match="changed by someone else"):
        c._save(stale, expected_rev=stale["rev"])
    assert c.get(TENANT, "c1")["fields"]["title"]["value"] == "題名"
