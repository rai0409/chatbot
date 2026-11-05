import json
from datetime import date, datetime, timezone

import pytest

import config
from rag_core import contract_notify as notify, contracts
from rag_core.connectors import outlook
from rag_core.connectors import sharepoint as sp

T0 = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
TENANT = "tenant_a"


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTRACTS_DIR", str(tmp_path / "contracts"))
    monkeypatch.delenv("CONTRACT_REMINDER_DAYS", raising=False)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)


def _contract(contract_id="c1", end="2027-03-31", decided=True, **extra):
    fields = {"counterparty": {"value": "株式会社サンプル"}, "title": {"value": "業務委託契約"}, "end_date": {"value": end}}
    fields.update({k: {"value": v} for k, v in extra.items()})
    contracts.import_extracted(TENANT, contract_id, {"schema": contracts.EXTRACT_SCHEMA, "fields": fields}, created_by="山田太郎", now=T0)
    if decided:
        for name in fields:
            contracts.confirm_field(TENANT, contract_id, name, by="佐藤次郎")


class Sink:
    def __init__(self, ok=True):
        self.ok, self.sent = ok, []

    def __call__(self, subject, body):
        self.sent.append((subject, body))
        return {"ok": self.ok, "reason": "smtp_down"} if not self.ok else {"ok": True}


def _keys(today, tenant=TENANT):
    return [(r["contract_id"], r["kind"], r["offset"]) for r in notify.due_reminders(tenant, today)]


def test_the_reminder_days_are_a_setting_with_a_safe_default(monkeypatch):
    assert notify.reminder_days() == [90, 60, 30, 14, 7, 1, 0]
    monkeypatch.setenv("CONTRACT_REMINDER_DAYS", "7, 30,30, 0")
    assert notify.reminder_days() == [30, 7, 0]
    for bad in ("abc", "30,x", "-5", "400", " , "):
        monkeypatch.setenv("CONTRACT_REMINDER_DAYS", bad)
        assert notify.reminder_days() == [90, 60, 30, 14, 7, 1, 0]


def test_the_most_urgent_threshold_already_crossed_is_the_one_due():
    _contract()  # the end date: 2027-03-31
    assert _keys(date(2026, 10, 27)) == []  # 155 days away
    assert _keys(date(2026, 12, 31)) == [("c1", "end", 90)]  # 90 days left
    assert _keys(date(2027, 3, 11)) == [("c1", "end", 30)]  # 20 days left: the 30-day reminder
    assert _keys(date(2027, 3, 30)) == [("c1", "end", 1)] and _keys(date(2027, 3, 31)) == [("c1", "end", 0)]
    assert _keys(date(2027, 4, 5)) == [("c1", "end", -1)]  # overdue: reminded once
    assert _keys(date(2027, 5, 1)) == []  # more than 30 days past: not reminded any more


def test_every_kind_of_deadline_is_reminded_and_the_unverified_ones_are_marked():
    _contract(auto_renewal=True, renewal_notice_days=60, decided=False)  # the notice deadline is 2027-01-30
    contracts.confirm_field(TENANT, "c1", "end_date", by="佐藤次郎")
    rows = {r["kind"]: r for r in notify.due_reminders(TENANT, date(2027, 1, 20))}
    assert sorted(rows) == ["end", "non_renewal_notice"]
    assert rows["end"]["verified"] is True and rows["non_renewal_notice"]["verified"] is False and rows["non_renewal_notice"]["offset"] == 14
    assert notify.due_reminders(TENANT, date(2026, 10, 27)) == []
    assert notify.due_reminders("tenant_b", date(2027, 1, 20)) == []


def test_a_contract_without_an_end_date_has_nothing_to_remind():
    contracts.create(TENANT, "blank", created_by="山田太郎")
    assert notify.due_reminders(TENANT, date(2027, 1, 1)) == []


def test_a_reminder_is_sent_once_and_the_larger_thresholds_are_covered():
    _contract()
    sink = Sink()
    first = notify.run(TENANT, today=date(2027, 3, 11), send=sink, now=T0)  # 20 days left
    assert first["due"] == 1 and first["sent"] == 1 and first["failed"] == 0 and len(sink.sent) == 1
    assert notify.run(TENANT, today=date(2027, 3, 11), send=sink)["due"] == 0  # nothing again the same day
    assert notify.run(TENANT, today=date(2027, 3, 1), send=sink)["due"] == 0  # 30 days: already covered by the later one
    sent = notify.load_outbox(TENANT)["sent"]
    assert sent["c1|end|2027-03-31|30"] == {"at": T0.isoformat()} and sent["c1|end|2027-03-31|60"]["covered_by"] == "c1|end|2027-03-31|30"
    assert "c1|end|2027-03-31|14" not in sent
    later = notify.run(TENANT, today=date(2027, 3, 24), send=sink, now=T0)  # 7 days left: a new threshold
    assert later["sent"] == 1 and len(sink.sent) == 2


def test_after_a_long_pause_only_the_most_urgent_reminder_goes_out():
    _contract()
    sink = Sink()
    assert notify.run(TENANT, today=date(2027, 3, 30), send=sink)["sent"] == 1  # the 1-day reminder, not seven
    assert len(sink.sent) == 1 and notify.run(TENANT, today=date(2027, 3, 31), send=sink)["due"] == 1  # then the 0-day one
    assert notify.run(TENANT, today=date(2027, 4, 2), send=sink)["due"] == 1  # then "overdue"
    assert notify.run(TENANT, today=date(2027, 4, 3), send=sink)["due"] == 0


def test_a_failed_send_is_not_remembered_and_is_tried_again():
    _contract()
    down = Sink(ok=False)
    failed = notify.run(TENANT, today=date(2027, 3, 11), send=down)
    assert failed["sent"] == 0 and failed["failed"] == 1 and failed["results"][0] == {
        "key": "c1|end|2027-03-31|30", "contract_id": "c1", "kind": "end", "date": "2027-03-31", "offset": 30, "verified": True,
        "status": "failed", "reason": "smtp_down"}
    assert notify.load_outbox(TENANT) == {"sent": {}}
    ok = Sink()
    assert notify.run(TENANT, today=date(2027, 3, 12), send=ok)["sent"] == 1


def test_a_dry_run_lists_without_sending_or_remembering():
    _contract()
    sink = Sink()
    out = notify.run(TENANT, today=date(2027, 3, 11), send=sink, dry_run=True)
    assert out["dry_run"] is True and out["results"][0]["status"] == "due" and sink.sent == []
    assert notify.load_outbox(TENANT) == {"sent": {}} and notify.run(TENANT, today=date(2027, 3, 11), send=sink)["sent"] == 1


def test_the_message_names_the_deadline_marks_unconfirmed_values_and_carries_no_amount_or_address():
    _contract(amount=1200000, contact_email="taro@example.co.jp", auto_renewal=True, renewal_notice_days=60, decided=False)
    contracts.confirm_field(TENANT, "c1", "end_date", by="佐藤次郎")
    sink = Sink()
    notify.run(TENANT, today=date(2027, 1, 20), send=sink)
    assert len(sink.sent) == 2
    texts = "\n".join(s + "\n" + b for s, b in sink.sent)
    assert "契約ID: c1" in texts and "株式会社サンプル" in texts and "業務委託契約" in texts and "2027-03-31" in texts
    assert "更新しない旨の通知期限" in texts and "人が確認していない項目に基づいています" in texts and "契約書の文言が優先します" in texts
    assert "1200000" not in texts and "example.co.jp" not in texts
    subject, body = notify.build_message({"days_left": -3, "label": "契約の終了日", "contract_id": "c9", "date": "2027-01-01",
                                          "verified": True, "title": None, "counterparty": None})
    assert "期限を 3 日過ぎています" in subject and "人が確認していない" not in body and "（未確認）" in body
    assert "本日が期限です" in notify.build_message({"days_left": 0, "label": "x", "contract_id": "c", "date": "d", "verified": True})[0]


def test_a_result_never_holds_the_message_text():
    _contract()
    out = notify.run(TENANT, today=date(2027, 3, 11), send=Sink())
    assert "株式会社サンプル" not in json.dumps(out, ensure_ascii=False)


def test_the_default_sender_is_the_existing_email_notification(monkeypatch):
    from webapi import notifications

    sent = []
    monkeypatch.setattr(notifications, "email_configured", lambda: False)
    assert notify.default_send("s", "b") == {"ok": False, "reason": "email_not_configured"}
    monkeypatch.setattr(notifications, "email_configured", lambda: True)
    monkeypatch.setattr(notifications, "_default_email_send", lambda subject, body: sent.append((subject, body)))
    assert notify.default_send("s", "b") == {"ok": True} and sent == [("s", "b")]

    def boom(subject, body):
        raise ConnectionRefusedError("secret host")

    monkeypatch.setattr(notifications, "_default_email_send", boom)
    out = notify.default_send("s", "b")
    assert out == {"ok": False, "reason": "ConnectionRefusedError"}


def test_a_broken_outbox_is_reported_not_ignored(tmp_path):
    _contract()
    notify._outbox_file(TENANT).write_text("{broken", encoding="utf-8")
    with pytest.raises(contracts.ContractError, match="unreadable"):
        notify.due_reminders(TENANT, date(2027, 3, 11))


# --- Outlook drafts ------------------------------------------------------------------------------------------------------------


class Graph:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, method, url, headers, body=None):
        self.calls.append((method, url, dict(headers), body))
        status, payload = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return status, json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload, {}


def _client(graph, sleeps=None):
    return sp.GraphClient(lambda: "tok-123", graph, sleep=(sleeps.append if sleeps is not None else lambda s: None))


def test_a_draft_is_created_through_graph_and_never_sent():
    graph = Graph((201, {"id": "AAMk-1", "webLink": "https://outlook.example/draft/1"}))
    out = outlook.create_draft(_client(graph), "legal@example.co.jp", to=["taro@example.co.jp"], subject="件名", body="本文")
    assert out == {"draft_id": "AAMk-1", "web_link": "https://outlook.example/draft/1"}
    method, url, headers, body = graph.calls[0]
    assert method == "POST" and url == f"{sp.GRAPH}/users/legal%40example.co.jp/messages" and headers["Authorization"] == "Bearer tok-123"
    payload = json.loads(body)
    assert payload == {"subject": "件名", "body": {"contentType": "Text", "content": "本文"},
                       "toRecipients": [{"emailAddress": {"address": "taro@example.co.jp"}}]}
    assert all("send" not in call[1].lower() for call in graph.calls)


@pytest.mark.parametrize("mailbox, to, subject, body", [
    ("not a mailbox", ["a@example.co.jp"], "s", "b"), ("", ["a@example.co.jp"], "s", "b"), ("a@example.co.jp", [], "s", "b"),
    ("a@example.co.jp", ["bad"], "s", "b"), ("a@example.co.jp", ["a@example.co.jp"] * 11, "s", "b"),
    ("a@example.co.jp", ["a@example.co.jp"], " ", "b"), ("a@example.co.jp", ["a@example.co.jp"], "s" * 201, "b"),
    ("a@example.co.jp", ["a@example.co.jp"], "s", "x" * 20001),
])
def test_a_draft_with_bad_input_is_refused_before_any_call(mailbox, to, subject, body):
    graph = Graph((201, {}))
    with pytest.raises(contracts.ContractError):
        outlook.create_draft(_client(graph), mailbox, to=to, subject=subject, body=body)
    assert graph.calls == []


def test_a_post_is_retried_only_when_throttled_and_a_failure_hides_the_token():
    sleeps = []
    graph = Graph((429, {}), (201, {"id": "x"}))
    assert outlook.create_draft(_client(graph, sleeps), "a@example.co.jp", to=["b@example.co.jp"], subject="s", body="b")["draft_id"] == "x"
    assert len(graph.calls) == 2 and sleeps == [1.0]
    for status in (500, 503, 401, 400):
        failing = Graph((status, b"secret tok-123"))
        with pytest.raises(sp.GraphError) as info:
            outlook.create_draft(_client(failing), "a@example.co.jp", to=["b@example.co.jp"], subject="s", body="b")
        assert len(failing.calls) == 1 and "tok-123" not in str(info.value) and f"HTTP {status}" in str(info.value)


def test_the_outlook_token_comes_from_the_outlook_settings(monkeypatch):
    for name in ("ACCESS_TOKEN", "TENANT_ID", "CLIENT_ID", "CLIENT_SECRET"):
        monkeypatch.delenv("OUTLOOK_" + name, raising=False)
        monkeypatch.delenv("SHAREPOINT_" + name, raising=False)
    from rag_core.source_registry import SourceError

    with pytest.raises(SourceError, match="not configured"):
        outlook.default_client()
    monkeypatch.setenv("SHAREPOINT_ACCESS_TOKEN", "the-sharepoint-token")
    with pytest.raises(SourceError):
        outlook.default_client()  # the SharePoint settings are not used for mail
    monkeypatch.setenv("OUTLOOK_ACCESS_TOKEN", "the-mail-token")
    assert outlook.default_client()._token() == "the-mail-token"


def _ready(email="taro@example.co.jp", decided=True):
    _contract(auto_renewal=True, renewal_notice_days=90, termination_notice_days=30, contact_email=email, decided=decided)


def test_a_draft_for_a_deadline_needs_a_confirmed_address_and_confirmed_values():
    graph = Graph((201, {"id": "AAMk-9", "webLink": "w"}))
    _ready(decided=False)
    for kind in ("non_renewal_notice",):
        with pytest.raises(contracts.ContractError, match="contact e-mail has not been confirmed"):
            outlook.draft_for_deadline(TENANT, "c1", kind, client=_client(graph), mailbox="legal@example.co.jp", by="山田太郎")
    contracts.confirm_field(TENANT, "c1", "contact_email", by="佐藤次郎")
    with pytest.raises(contracts.ContractError, match="rests on values that have not been confirmed"):
        outlook.draft_for_deadline(TENANT, "c1", "non_renewal_notice", client=_client(graph), mailbox="legal@example.co.jp", by="山田太郎")
    assert graph.calls == []
    with pytest.raises(contracts.ContractError, match="no 'bogus' deadline"):
        outlook.draft_for_deadline(TENANT, "c1", "bogus", client=_client(graph), mailbox="legal@example.co.jp", by="山田太郎")


def test_a_draft_is_made_from_a_template_and_the_history_records_it_without_the_text():
    _ready()
    graph = Graph((201, {"id": "AAMk-9", "webLink": "https://outlook.example/d/9"}))
    out = outlook.draft_for_deadline(TENANT, "c1", "non_renewal_notice", client=_client(graph), mailbox="legal@example.co.jp",
                                     by="山田太郎", now=T0)
    assert out == {"draft_id": "AAMk-9", "web_link": "https://outlook.example/d/9", "kind": "non_renewal_notice", "date": "2026-12-31"}
    payload = json.loads(graph.calls[0][3])
    assert payload["toRecipients"] == [{"emailAddress": {"address": "taro@example.co.jp"}}] and payload["subject"] == "業務委託契約 に関するご連絡"
    assert "株式会社サンプル ご担当者様" in payload["body"]["content"] and "更新しない旨の通知期限（2026-12-31）" in payload["body"]["content"]
    assert "ひな形" in payload["body"]["content"]
    history = contracts.get(TENANT, "c1")["history"][-1]
    assert history == {"at": T0.isoformat(), "by": "山田太郎", "action": "draft_created", "field": "non_renewal_notice", "old": None, "new": "AAMk-9"}
    assert "ご担当者様" not in json.dumps(contracts.get(TENANT, "c1"), ensure_ascii=False)
