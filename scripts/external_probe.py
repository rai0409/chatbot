#!/usr/bin/env python3
"""External probe: asks the service whether it is up, from ANOTHER host, and tells people when it is not (S7).

The service cannot report its own death. Run this on a different machine (a small server, a monitoring host) at a fixed interval:

  python3 scripts/external_probe.py probe  --url https://chat.example.co.jp --log /var/lib/kuraden-probe/probe.jsonl \\
        --slack-webhook-env PROBE_SLACK_WEBHOOK --heartbeat-url https://heartbeat.example.co.jp/ping/abc
  python3 scripts/external_probe.py watch  ...same options... --interval 60        # a loop, for a service manager
  python3 scripts/external_probe.py report --log /var/lib/kuraden-probe/probe.jsonl --days 30 --target 99.0

`probe` asks `GET <url>/ready` once (up: HTTP 200; down: 503, any other answer, no answer), appends a line to the log, and decides whether
to tell anyone: after --fail-after answers in a row that are down it sends "down" once, then a reminder every --remind-minutes, and
"recovered" with the length of the outage when it is up again. The state between runs is kept in a small file next to the log, so `probe` can
be run by cron every minute. `watch` is `probe` in a loop. With --api-key-env it also reads `GET <url>/system/status` to note
whether the service is "degraded" (the LLM is unavailable and answers are excerpts): that is still up, and is only recorded.
Where to tell: a Slack-style webhook (--slack-webhook-env), e-mail through an SMTP relay (PROBE_SMTP_* variables), and/or a command
(--on-event-command; the event is in the environment variables PROBE_EVENT, PROBE_MESSAGE). --heartbeat-url is asked after every probe:
a "dead man's switch" service alerts when the probe host itself stops asking.
`report` turns the log into the availability in business hours (rag_core/availability.py), the outages, and the gaps in which the probe
did not run. The service shows the same figures on the admin console when PROBE_LOG_FILE points at the log.

Standard library only. No credentials in the log. Exit code of `probe`: 0 up, 1 up but degraded, 2 down.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import smtplib
import subprocess  # nosec B404 - runs the operator's own --on-event-command, without a shell
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from rag_core import availability  # noqa: E402  (standard library only; the package __init__ imports nothing)

DEFAULT_TIMEOUT = 10.0
MAX_LOG_BYTES = 10 * 1024 * 1024


# --- one probe --------------------------------------------------------------------------------------------------------------------------------


def _get(url: str, timeout: float, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310  # nosec B310 - the operator's own URL
            body = response.read(65536)
            return {"status": response.status, "body": body, "ms": int((time.monotonic() - started) * 1000), "error": ""}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "body": b"", "ms": int((time.monotonic() - started) * 1000), "error": ""}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return {"status": None, "body": b"", "ms": int((time.monotonic() - started) * 1000), "error": type(reason).__name__}


def probe_once(url: str, *, timeout: float = DEFAULT_TIMEOUT, api_key: str = "", tenant: str = "default", now: Optional[datetime] = None) -> Dict[str, Any]:
    """Ask the service. {"t", "ok", "status", "ms", "error", "degraded", "host"}: no credential, no body."""
    base = url.rstrip("/")
    answer = _get(base + "/ready", timeout)
    ok = answer["status"] == 200
    degraded: Optional[bool] = None
    if ok and api_key:
        extra = _get(f"{base}/system/status?tenant_id={urllib.parse.quote(tenant)}", timeout, {"X-Api-Key": api_key})
        if extra["status"] == 200:
            try:
                degraded = json.loads(extra["body"].decode("utf-8")).get("service") == "degraded"
            except ValueError:
                degraded = None
    return {"t": (now or datetime.now(timezone.utc)).isoformat(), "ok": ok, "status": answer["status"], "ms": answer["ms"], "error": answer["error"],
            "degraded": degraded, "host": urllib.parse.urlparse(base).hostname or ""}


def append_log(path: Path, row: Dict[str, Any], max_bytes: int = MAX_LOG_BYTES) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size >= max_bytes:
        os.replace(path, Path(str(path) + ".1"))  # one rotated copy; the report reads both
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


# --- when to tell people ------------------------------------------------------------------------------------------------------------------------


def next_event(state: Dict[str, Any], row: Dict[str, Any], *, fail_after: int, remind_minutes: float) -> Optional[str]:
    """Update `state` with a probe answer and return the event to announce: "down", "still_down", "recovered", or None."""
    when = datetime.fromisoformat(row["t"])
    if row["ok"]:
        was_down = bool(state.get("announced_down"))
        down_since = state.get("down_since")
        state.update(failures=0, announced_down=False, down_since=None, last_announced=None)
        if was_down:
            state["recovered_after_minutes"] = round((when - datetime.fromisoformat(down_since)).total_seconds() / 60.0, 1) if down_since else None
            return "recovered"
        return None
    state["failures"] = int(state.get("failures") or 0) + 1
    state.setdefault("down_since", row["t"])
    if state["down_since"] is None:
        state["down_since"] = row["t"]
    if state["failures"] < fail_after:
        return None
    if not state.get("announced_down"):
        state.update(announced_down=True, last_announced=row["t"])
        return "down"
    last = datetime.fromisoformat(state["last_announced"])
    if remind_minutes > 0 and when - last >= timedelta(minutes=remind_minutes):
        state["last_announced"] = row["t"]
        return "still_down"
    return None


def message_for(event: str, row: Dict[str, Any], state: Dict[str, Any], url: str) -> str:
    host = urllib.parse.urlparse(url).hostname or url
    since = state.get("down_since") or row["t"]
    if event == "down":
        what = f"HTTP {row['status']}" if row.get("status") else (row.get("error") or "応答なし")
        return f"【停止】{host} が応答していません（{what}）。{state.get('failures')} 回連続。最初の失敗: {since}"
    if event == "still_down":
        return f"【停止が継続】{host} は {since} から応答していません。"
    minutes = state.get("recovered_after_minutes")
    return f"【復旧】{host} が応答しました。" + (f"停止時間: 約 {minutes} 分。" if minutes is not None else "")


class Notifier:
    """Tells people through the channels the operator configured. A failure of one channel never stops the others."""

    def __init__(self, slack_webhook_env: str = "", smtp: bool = False, command: str = "", environ: Optional[Dict[str, str]] = None,
                 opener: Callable[..., Any] = urllib.request.urlopen, smtp_factory: Callable[..., Any] = smtplib.SMTP):
        self.environ = dict(os.environ) if environ is None else environ
        self.slack_webhook_env, self.smtp, self.command = slack_webhook_env, smtp, command
        self._open, self._smtp = opener, smtp_factory

    def send(self, event: str, text: str) -> Dict[str, str]:
        results: Dict[str, str] = {}
        webhook = self.environ.get(self.slack_webhook_env, "").strip() if self.slack_webhook_env else ""
        if webhook:
            try:
                request = urllib.request.Request(webhook, data=json.dumps({"text": text}).encode("utf-8"), headers={"Content-Type": "application/json"})
                with self._open(request, timeout=10) as response:  # noqa: S310  # nosec B310 - the operator's own webhook
                    results["slack"] = "ok" if response.status < 300 else f"http {response.status}"
            except Exception as exc:  # noqa: BLE001
                results["slack"] = type(exc).__name__
        if self.smtp and self.environ.get("PROBE_SMTP_HOST") and self.environ.get("PROBE_MAIL_TO") and self.environ.get("PROBE_MAIL_FROM"):
            try:
                message = EmailMessage()
                message["Subject"] = f"[死活監視] {event}"
                message["From"], message["To"] = self.environ["PROBE_MAIL_FROM"], self.environ["PROBE_MAIL_TO"]
                message.set_content(text)
                with self._smtp(self.environ["PROBE_SMTP_HOST"], int(self.environ.get("PROBE_SMTP_PORT") or 587), timeout=15) as server:
                    if (self.environ.get("PROBE_SMTP_STARTTLS") or "true").lower() in ("1", "true", "yes", "on"):
                        server.starttls()
                    if self.environ.get("PROBE_SMTP_USER") and self.environ.get("PROBE_SMTP_PASSWORD"):
                        server.login(self.environ["PROBE_SMTP_USER"], self.environ["PROBE_SMTP_PASSWORD"])
                    server.send_message(message)
                results["smtp"] = "ok"
            except Exception as exc:  # noqa: BLE001
                results["smtp"] = type(exc).__name__
        if self.command:
            try:
                completed = subprocess.run(shlex.split(self.command), env={**self.environ, "PROBE_EVENT": event, "PROBE_MESSAGE": text},  # nosec B603
                                           timeout=30, check=False, capture_output=True)
                results["command"] = "ok" if completed.returncode == 0 else f"exit {completed.returncode}"
            except Exception as exc:  # noqa: BLE001
                results["command"] = type(exc).__name__
        return results


def run_probe(args: argparse.Namespace, notifier: Notifier, *, now: Optional[datetime] = None) -> int:
    log = Path(args.log)
    state_file = Path(args.state_file) if args.state_file else Path(str(log) + ".state.json")
    api_key = os.environ.get(args.api_key_env, "") if args.api_key_env else ""
    row = probe_once(args.url, timeout=args.timeout, api_key=api_key, tenant=args.tenant, now=now)
    append_log(log, row)
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    event = next_event(state, row, fail_after=args.fail_after, remind_minutes=args.remind_minutes)
    sent: Dict[str, str] = {}
    if event:
        sent = notifier.send(event, message_for(event, row, state, args.url))
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    if args.heartbeat_url:
        _get(args.heartbeat_url, 10)  # best effort: the answer does not matter, the request does
    summary = {"t": row["t"], "ok": row["ok"], "status": row["status"], "ms": row["ms"], "error": row["error"], "degraded": row["degraded"],
               "event": event, "notified": sent}
    print(json.dumps(summary, ensure_ascii=False))
    return 2 if not row["ok"] else (1 if row["degraded"] else 0)


# --- the report ---------------------------------------------------------------------------------------------------------------------------------------


def run_report(args: argparse.Namespace, *, now: Optional[datetime] = None) -> int:
    result = availability.report_for_days(
        args.log, args.days, now=now, max_gap_seconds=args.max_gap, utc_offset_hours=args.utc_offset, first_hour=args.business_start,
        last_hour=args.business_end, target_percent=args.target)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        minutes = result["business_minutes"]
        percent = result["availability_percent"]
        print(f"期間: {result['window']['start']} 〜 {result['window']['end']}（平日 {args.business_start}〜{args.business_end} 時のみ）")
        print(f"稼働率: {'—' if percent is None else str(percent) + '%'}（目標 {args.target}% → "
              f"{'未判定' if result['meets_target'] is None else ('達成' if result['meets_target'] else '未達')}）")
        print(f"営業時間内: 稼働 {minutes['up']} 分 / 停止 {minutes['down']} 分 / 監視できなかった {minutes['unmonitored']} 分"
              f"（縮退しながら稼働 {minutes['degraded_but_up']} 分）。監視の網羅率: {result['coverage_percent']}%")
        for outage in result["outages"]:
            print(f"  停止: {outage['start']} 〜 {outage['end'] or '継続中'}（営業時間内 {outage['business_minutes']} 分）{outage['error']}")
        for gap in result["unmonitored_gaps"]:
            print(f"  監視の空白: {gap['start']} 〜 {gap['end']}（営業時間内 {gap['business_minutes']} 分）")
    return 0 if result["meets_target"] in (True, None) else 1


# --- command line ---------------------------------------------------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("probe", "watch"):
        p = sub.add_parser(name)
        p.add_argument("--url", required=True, help="the base URL of the service, e.g. https://chat.example.co.jp")
        p.add_argument("--log", required=True, help="the probe log (JSON lines)")
        p.add_argument("--state-file", default="", help="default: <log>.state.json")
        p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
        p.add_argument("--fail-after", type=int, default=3, help="answers in a row that are down before telling anyone")
        p.add_argument("--remind-minutes", type=float, default=60.0, help="0: no reminders while it stays down")
        p.add_argument("--api-key-env", default="", help="name of the environment variable with an API key, to also read /system/status")
        p.add_argument("--tenant", default="default")
        p.add_argument("--slack-webhook-env", default="", help="name of the environment variable with a Slack-style webhook URL")
        p.add_argument("--smtp", action="store_true", help="e-mail through PROBE_SMTP_HOST / _PORT / _USER / _PASSWORD, PROBE_MAIL_FROM / _TO")
        p.add_argument("--on-event-command", default="", help="a command to run on an event (PROBE_EVENT, PROBE_MESSAGE in its environment)")
        p.add_argument("--heartbeat-url", default="", help="asked after every probe (a dead man's switch service)")
        if name == "watch":
            p.add_argument("--interval", type=float, default=60.0)
    r = sub.add_parser("report")
    r.add_argument("--log", required=True)
    r.add_argument("--days", type=int, default=30)
    r.add_argument("--target", type=float, default=99.0)
    r.add_argument("--max-gap", type=float, default=300.0, help="seconds a probe answer holds; more is a gap in the monitoring")
    r.add_argument("--utc-offset", type=float, default=9.0)
    r.add_argument("--business-start", type=int, default=8)
    r.add_argument("--business-end", type=int, default=21)
    r.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "report":
        return run_report(args)
    notifier = Notifier(args.slack_webhook_env, args.smtp, args.on_event_command)
    if args.command == "probe":
        return run_probe(args, notifier)
    code = 0
    while True:  # watch
        code = run_probe(args, notifier)
        time.sleep(max(1.0, args.interval))
    return code  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
