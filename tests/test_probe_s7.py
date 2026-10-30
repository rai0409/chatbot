"""S7: availability measured from outside (rag_core/availability.py, scripts/external_probe.py, GET /admin/availability)."""
import importlib.util
import json
import shlex
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from rag_core import availability

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "external_probe.py"
_spec = importlib.util.spec_from_file_location("external_probe_under_test", _SCRIPT)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

START = datetime(2026, 10, 29, 1, 0, tzinfo=timezone.utc)  # a Thursday, 10:00 local (UTC+9): inside business hours
END = START + timedelta(hours=1)
ADMIN_TOKEN = "installation-admin-token-not-a-secret-0123"


def _rows(minutes_down=(), minutes=range(60), degraded=()):
    return [{"t": (START + timedelta(minutes=m)).isoformat(), "_t": START + timedelta(minutes=m), "ok": m not in minutes_down,
             "status": 503 if m in minutes_down else 200, "error": "", "degraded": m in degraded} for m in minutes]


# --- the arithmetic ---------------------------------------------------------------------------------------------------------------------------


def test_business_seconds_counts_weekday_working_hours_only():
    thursday_night = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)  # 21:00 local: the end of business hours
    assert availability.business_seconds(START, END) == 3600.0
    assert availability.business_seconds(thursday_night, thursday_night + timedelta(hours=3)) == 0.0
    saturday = datetime(2026, 10, 31, 1, 0, tzinfo=timezone.utc)
    assert availability.business_seconds(saturday, saturday + timedelta(hours=1)) == 0.0
    assert availability.business_seconds(END, START) == 0.0


def test_report_counts_an_outage_and_keeps_degraded_as_up():
    result = availability.report(_rows(minutes_down=range(30, 40), degraded=range(0, 10)), start=START, end=END)
    assert result["business_minutes"]["down"] == 10.0
    assert result["business_minutes"]["up"] == 50.0
    assert result["business_minutes"]["degraded_but_up"] == 10.0  # degraded is up, only counted on the side
    assert result["availability_percent"] == pytest.approx(83.333, abs=0.001)
    assert result["meets_target"] is False
    assert [o["business_minutes"] for o in result["outages"]] == [10.0]
    assert result["outages"][0]["end"] is not None


def test_a_dead_probe_is_a_gap_not_availability():
    result = availability.report(_rows(minutes=range(10)), start=START, end=END, max_gap_seconds=300)
    assert result["business_minutes"]["unmonitored"] == 46.0  # the last answer holds for 5 minutes only
    assert result["coverage_percent"] < 25
    assert result["unmonitored_gaps"] and result["availability_percent"] == 100.0


def test_no_probe_at_all_gives_no_figure_and_no_verdict():
    result = availability.report([], start=START, end=END)
    assert result["availability_percent"] is None and result["meets_target"] is None
    assert result["business_minutes"]["unmonitored"] == 60.0


def test_an_outage_that_is_still_going_has_no_end():
    result = availability.report(_rows(minutes_down=range(50, 60)), start=START, end=END)
    assert result["outages"][0]["end"] is None


def test_read_probes_reads_the_rotated_copy_and_skips_broken_lines(tmp_path):
    log = tmp_path / "probe.jsonl"
    old = {"t": START.isoformat(), "ok": True, "status": 200}
    new = {"t": (START + timedelta(minutes=1)).isoformat(), "ok": False, "status": 503}
    Path(str(log) + ".1").write_text(json.dumps(old) + "\n", encoding="utf-8")
    log.write_text("not json\n" + json.dumps({"no": "time"}) + "\n" + json.dumps(new) + "\n", encoding="utf-8")
    assert [r["ok"] for r in availability.read_probes(log)] == [True, False]
    assert availability.read_probes(tmp_path / "missing.jsonl") == []


# --- when to tell people ----------------------------------------------------------------------------------------------------------------------


def _row(minute, ok):
    return {"t": (START + timedelta(minutes=minute)).isoformat(), "ok": ok, "status": 200 if ok else 503, "error": ""}


def test_down_is_announced_once_after_n_failures_then_reminded_then_recovery_is_announced():
    state = {}
    events = [probe.next_event(state, _row(m, False), fail_after=3, remind_minutes=60) for m in range(0, 4)]
    assert events == [None, None, "down", None]  # not before the third failure, and only once
    assert probe.next_event(state, _row(30, False), fail_after=3, remind_minutes=60) is None
    assert probe.next_event(state, _row(63, False), fail_after=3, remind_minutes=60) == "still_down"
    assert probe.next_event(state, _row(64, False), fail_after=3, remind_minutes=60) is None
    assert probe.next_event(state, _row(70, True), fail_after=3, remind_minutes=60) == "recovered"
    assert state["recovered_after_minutes"] == 70.0 and state["failures"] == 0
    assert probe.next_event(state, _row(71, True), fail_after=3, remind_minutes=60) is None


def test_a_short_blip_is_never_announced():
    state = {}
    seen = [probe.next_event(state, _row(m, ok), fail_after=3, remind_minutes=60) for m, ok in enumerate([False, False, True, False, False, True])]
    assert seen == [None] * 6


def test_the_notifier_uses_each_channel_and_one_failing_channel_does_not_stop_the_others(tmp_path):
    sent = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def opener(request, timeout):
        sent.append(json.loads(request.data.decode("utf-8")))
        return Response()

    def broken_smtp(*args, **kwargs):
        raise OSError("relay down")

    out = tmp_path / "hook.txt"
    command = f"{shlex.quote(sys.executable)} -c \"import os,sys; open(sys.argv[1],'w').write(os.environ['PROBE_EVENT'])\" {shlex.quote(str(out))}"
    notifier = probe.Notifier("HOOK", True, command, environ={"HOOK": "https://hooks.example.co.jp/x", "PROBE_SMTP_HOST": "smtp.example.co.jp",
                                                              "PROBE_MAIL_FROM": "monitor@example.co.jp", "PROBE_MAIL_TO": "ops@example.co.jp"},
                              opener=opener, smtp_factory=broken_smtp)
    results = notifier.send("down", "停止しました")
    assert results == {"slack": "ok", "smtp": "OSError", "command": "ok"}
    assert sent == [{"text": "停止しました"}] and out.read_text(encoding="utf-8") == "down"
    assert probe.Notifier("", False, "", environ={}).send("down", "x") == {}  # nothing configured, nothing sent


# --- a real probe against a local server --------------------------------------------------------------------------------------------------------


class _Service(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        server = self.server
        server.hits.append(self.path)
        if self.path == "/ready":
            self.send_response(server.ready_status)
            self.end_headers()
            self.wfile.write(b"{}")
        elif self.path.startswith("/system/status"):
            if self.headers.get("X-Api-Key") != "key-not-a-secret":
                self.send_response(401)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"service": "degraded" if server.degraded else "ok"}).encode())
        else:
            self.send_response(204)
            self.end_headers()

    def log_message(self, *args):  # the test output stays quiet
        pass


@pytest.fixture
def service():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Service)
    server.ready_status, server.degraded, server.hits = 200, False, []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _run(argv, capsys):
    code = probe.main(argv)
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    return code, json.loads(lines[-1]) if lines else {}


def test_probe_up_down_and_degraded_set_the_exit_code_and_write_one_line_each(service, tmp_path, capsys, monkeypatch):
    server, url = service
    monkeypatch.setenv("PROBE_TEST_KEY", "key-not-a-secret")
    log = tmp_path / "probe.jsonl"
    base = ["probe", "--url", url, "--log", str(log), "--fail-after", "2"]
    code, summary = _run(base, capsys)
    assert (code, summary["ok"], summary["degraded"]) == (0, True, None)  # without a key /system/status is not read
    server.degraded = True
    code, summary = _run(base + ["--api-key-env", "PROBE_TEST_KEY"], capsys)
    assert (code, summary["ok"], summary["degraded"]) == (1, True, True)  # degraded: up, but exit code 1
    server.ready_status = 503
    code, summary = _run(base, capsys)
    assert (code, summary["ok"], summary["event"]) == (2, False, None)  # the first failure: nobody is told yet
    code, summary = _run(base, capsys)
    assert (code, summary["event"]) == (2, "down")
    server.ready_status = 200
    code, summary = _run(base, capsys)
    assert (code, summary["event"]) == (0, "recovered")
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [r["ok"] for r in rows] == [True, True, False, False, True]
    assert all(set(r) == {"t", "ok", "status", "ms", "error", "degraded", "host"} for r in rows)  # no key, no body, no path
    assert "key-not-a-secret" not in log.read_text(encoding="utf-8")


def test_no_answer_is_down_and_the_heartbeat_is_asked_even_then(service, tmp_path, capsys):
    server, url = service
    dead_url = "http://127.0.0.1:9"  # nothing listens there
    code, summary = _run(["probe", "--url", dead_url, "--log", str(tmp_path / "p.jsonl"), "--timeout", "2",
                          "--heartbeat-url", url + "/ping/abc"], capsys)
    assert code == 2 and summary["ok"] is False and summary["status"] is None and summary["error"]
    assert "/ping/abc" in server.hits  # the dead man's switch hears from the probe host whatever the service does


def test_the_log_rotates_when_it_is_large(tmp_path):
    log = tmp_path / "probe.jsonl"
    probe.append_log(log, {"t": START.isoformat(), "ok": True}, max_bytes=10)
    probe.append_log(log, {"t": END.isoformat(), "ok": False}, max_bytes=10)
    assert Path(str(log) + ".1").exists() and len(log.read_text(encoding="utf-8").splitlines()) == 1
    assert [r["ok"] for r in availability.read_probes(log)] == [True, False]  # the report reads both


def test_report_command_prints_the_figure_and_its_exit_code_follows_the_target(tmp_path, capsys):
    log = tmp_path / "probe.jsonl"
    log.write_text("".join(json.dumps({k: v for k, v in r.items() if k != "_t"}) + "\n" for r in _rows(minutes_down=range(30, 40))), encoding="utf-8")
    args = ["report", "--log", str(log), "--days", "1", "--json"]
    parsed = probe._parser().parse_args(args)
    assert probe.run_report(parsed, now=END) == 1  # 83% against the target 99%
    assert json.loads(capsys.readouterr().out)["availability_percent"] == pytest.approx(83.333, abs=0.001)
    relaxed = probe._parser().parse_args(["report", "--log", str(log), "--days", "1", "--target", "80"])
    assert probe.run_report(relaxed, now=END) == 0
    assert "稼働率" in capsys.readouterr().out


# --- the admin API ------------------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch, tmp_path):
    for name in ("API_AUTH_ENABLED", "APP_ENV", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED", "ADMIN_AUTH_ALLOW_INSECURE_DEV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("ADMIN_AUTH_TENANT_TOKENS", "tenant-a-admin-token-not-a-secret-0123456=tenant_a")
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(config, "PROBE_LOG_FILE", "")
    from webapi import main

    return TestClient(main.app), tmp_path


def test_availability_endpoint_reports_not_configured_then_the_figure(api, monkeypatch):
    client, tmp_path = api
    headers = {"x-admin-token": ADMIN_TOKEN}
    assert client.get("/admin/availability", headers=headers).json()["configured"] is False
    log = tmp_path / "probe.jsonl"
    now = datetime.now(timezone.utc)
    log.write_text(json.dumps({"t": (now - timedelta(minutes=2)).isoformat(), "ok": True, "status": 200}) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "PROBE_LOG_FILE", str(log))
    body = client.get("/admin/availability?days=1", headers=headers).json()
    assert body["configured"] is True and body["log_file_found"] is True and body["probes"] == 1
    monkeypatch.setattr(config, "PROBE_LOG_FILE", str(tmp_path / "missing.jsonl"))
    assert client.get("/admin/availability", headers=headers).json()["log_file_found"] is False


def test_availability_endpoint_is_for_the_installation_administrator_only(api):
    client, _ = api
    assert client.get("/admin/availability", headers={"x-admin-token": "tenant-a-admin-token-not-a-secret-0123456"}).status_code == 403
    assert client.get("/admin/availability").status_code in (401, 403)
