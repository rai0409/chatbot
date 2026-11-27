"""Mail ACL data model, ACL-aware quote dedupe, update safety, identity and isolation.

Synthetic mail only (fixed example names and example.co.jp addresses).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from rag_core import mail_access, mail_ingest
from rag_core.mail_to_canonical import (apply_update_policy, convert_mail_batch, convert_mail_inputs,
                                        plan_mail_deletion)

YAMADA = "山田太郎 <taro.yamada@example.co.jp>"
TANAKA = "田中太郎 <taro.tanaka@example.co.jp>"
SATO = "佐藤次郎 <jiro.sato@example.co.jp>"
BCC_ADDR = "jiro.sato@example.co.jp"
A_BODY = ("来月の定例会議は十二月十日の午後二時から第三会議室で開催します。資料は前日までに共有フォルダへ"
          "格納してください。議題は予算の見直しと新規案件の進捗確認です。")
REPLY = "承知しました。当日は参加します。\n\n2026年11月20日 10:00 " + YAMADA + ":\n> " + A_BODY


def _eml(path: Path, mid: str, frm: str, to: str, date: str, body: str, *, cc: str = "", bcc: str = "",
         irt: str = "") -> Path:
    head = f"From: {frm}\nTo: {to}\n"
    head += f"Cc: {cc}\n" if cc else ""
    head += f"Bcc: {bcc}\n" if bcc else ""
    head += f"Message-ID: <{mid}>\nDate: {date}\nSubject: 定例会議\nContent-Type: text/plain; charset=utf-8\n"
    head += f"In-Reply-To: <{irt}>\n" if irt else ""
    path.write_text(head + "\n" + body + "\n", encoding="utf-8")
    return path


def _original(tmp_path: Path) -> Path:
    return _eml(tmp_path / "a.eml", "a@example.co.jp", YAMADA, TANAKA, "Fri, 20 Nov 2026 10:00:00 +0900", A_BODY)


def _by_mid(rows, mid):
    return next(r for r in rows if r["message_id"] == mid)


# --- ACL data model ----------------------------------------------------------


def test_bcc_never_in_output_and_acl_has_no_plaintext(tmp_path, monkeypatch):
    monkeypatch.delenv("MAIL_ACL_HMAC_KEY", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    body = A_BODY + "\n控えとして jiro.sato@example.co.jp にも送付しています。"
    one = _eml(tmp_path / "one.eml", "m1@example.co.jp", YAMADA, TANAKA, "Fri, 20 Nov 2026 10:00:00 +0900",
               body, bcc=SATO)
    rows, _ = convert_mail_inputs([one], tenant_id="t1")
    dumped = json.dumps(rows, ensure_ascii=False)
    assert BCC_ADDR not in dumped.lower()
    row = rows[0]
    assert row["acl_version"] == mail_access.ACL_VERSION and row["acl_key_id"] == mail_access.DEV_KEY_ID
    key, _ = mail_access.acl_key()
    assert row["acl_principals"] == sorted([mail_access.principal_digest("taro.yamada@example.co.jp", key),
                                            mail_access.principal_digest("taro.tanaka@example.co.jp", key)])
    assert all(re.fullmatch(r"[0-9a-f]{64}", p) for p in row["acl_principals"])
    assert mail_access.principal_digest(BCC_ADDR, key) not in row["acl_principals"]
    acl_dump = json.dumps({k: row[k] for k in ("acl_principals", "acl_version", "acl_key_id", "sender_principal")})
    assert "@" not in acl_dump
    assert not any(k.startswith("_") for k in row)


def test_mailbox_owner_bcc_copy_includes_only_the_owner(tmp_path):
    one = _eml(tmp_path / "one.eml", "m1@example.co.jp", YAMADA, TANAKA, "Fri, 20 Nov 2026 10:00:00 +0900",
               A_BODY, bcc=SATO + ", 株式会社サンプル <info@example.co.jp>")
    rows, _ = convert_mail_inputs([one], tenant_id="t1", mailbox_owner="Jiro.Sato@example.co.jp")
    key, _ = mail_access.acl_key()
    acl = rows[0]["acl_principals"]
    assert mail_access.principal_digest(BCC_ADDR, key) in acl
    assert mail_access.principal_digest("info@example.co.jp", key) not in acl
    assert "info@example.co.jp" not in json.dumps(rows, ensure_ascii=False)
    with pytest.raises(ValueError, match="invalid_mailbox_owner"):
        convert_mail_inputs([one], tenant_id="t1", mailbox_owner="not an address")


def test_production_without_hmac_key_refuses_ingestion(tmp_path, monkeypatch):
    one = _original(tmp_path)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("MAIL_ACL_HMAC_KEY", raising=False)
    with pytest.raises(ValueError, match="mail_acl_key_required"):
        convert_mail_inputs([one], tenant_id="t1")
    monkeypatch.setenv("MAIL_ACL_HMAC_KEY", "synthetic-test-key")
    rows, _ = convert_mail_inputs([one], tenant_id="t1")
    assert rows[0]["acl_key_id"].startswith("k1:")


def test_mail_visible_to_is_fail_closed(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    key = b"synthetic"
    me = {mail_access.principal_digest("taro.yamada@example.co.jp", key)}
    other = {mail_access.principal_digest("taro.tanaka@example.co.jp", key)}
    row = {"type": "email", "acl_principals": sorted(me), "acl_version": 1, "acl_key_id": "k1:x"}
    assert mail_access.mail_visible_to(row, me)
    assert not mail_access.mail_visible_to(row, other)
    assert not mail_access.mail_visible_to(row, set())
    assert not mail_access.mail_visible_to({"type": "email"}, me)
    assert not mail_access.mail_visible_to({**row, "acl_principals": []}, me)
    assert not mail_access.mail_visible_to({**row, "acl_version": 99}, me)
    assert not mail_access.mail_visible_to({**row, "acl_version": None}, me)
    monkeypatch.setenv("APP_ENV", "production")
    assert not mail_access.mail_visible_to({**row, "acl_key_id": "dev"}, me)
    assert mail_access.mail_visible_to(row, me)
    monkeypatch.delenv("APP_ENV")
    # Current exclusion stays: mail is never allowed while MAIL_SEARCH_ENABLED is off.
    monkeypatch.setattr(mail_access, "MAIL_SEARCH_ENABLED", False)
    assert mail_access.is_private_mail_record(row)
    assert not mail_access.mail_record_allowed(row, me)
    assert mail_access.mail_record_allowed({"type": "pdf", "source_doc": "a.pdf"}, set())
    monkeypatch.setattr(mail_access, "MAIL_SEARCH_ENABLED", True)
    assert mail_access.mail_record_allowed(row, me)
    assert not mail_access.mail_record_allowed(row, other)
    assert not mail_access.mail_record_allowed({"type": "email"}, me)


def test_looks_like_mail_table():
    assert mail_access.looks_like_mail_table(["差出人", "宛先", "件名", "本文"])
    assert mail_access.looks_like_mail_table(["From", "Date", "Body"])
    assert not mail_access.looks_like_mail_table(["商品", "価格"])
    assert not mail_access.looks_like_mail_table(["text", "subject"])  # no sender column
    assert not mail_access.looks_like_mail_table([])


# --- ACL-aware dedupe and latest-only ---------------------------------------------


def test_quote_dedupe_only_when_referenced_acl_covers_quoting_acl(tmp_path):
    a = _original(tmp_path)
    same = _eml(tmp_path / "b.eml", "b@example.co.jp", TANAKA, YAMADA, "Fri, 20 Nov 2026 12:00:00 +0900",
                REPLY, irt="a@example.co.jp")
    rows, _ = convert_mail_inputs([a, same], tenant_id="t1")
    reply = _by_mid(rows, "b@example.co.jp")
    assert "定例会議は十二月十日" not in reply["display_text"]
    assert reply["quote_refs"] == [_by_mid(rows, "a@example.co.jp")["source_doc"]]

    wider = _eml(tmp_path / "c.eml", "c@example.co.jp", TANAKA, YAMADA, "Fri, 20 Nov 2026 12:00:00 +0900",
                 REPLY, cc=SATO, irt="a@example.co.jp")
    rows, _ = convert_mail_inputs([a, wider], tenant_id="t1")
    reply = _by_mid(rows, "c@example.co.jp")
    # 佐藤次郎 is a party to the reply only: the quoted context stays inline.
    assert "定例会議は十二月十日" in reply["display_text"]
    assert reply["quote_refs"] == []


def test_superseded_by_and_keep_latest_only(tmp_path):
    a = _original(tmp_path)
    b = _eml(tmp_path / "b.eml", "b@example.co.jp", TANAKA, YAMADA, "Fri, 20 Nov 2026 12:00:00 +0900",
             REPLY, irt="a@example.co.jp")
    rows, _ = convert_mail_inputs([a, b], tenant_id="t1")
    assert _by_mid(rows, "a@example.co.jp")["superseded_by"] == _by_mid(rows, "b@example.co.jp")["source_doc"]
    assert _by_mid(rows, "b@example.co.jp")["superseded_by"] == ""
    rows, _ = convert_mail_inputs([a, b], tenant_id="t1", keep_latest_only=True)
    latest = _by_mid(rows, "b@example.co.jp")
    assert "定例会議は十二月十日" in latest["display_text"] and latest["quote_refs"] == []
    # The new content of the superseded message is never dropped.
    assert "定例会議は十二月十日" in _by_mid(rows, "a@example.co.jp")["display_text"]

    narrower = _eml(tmp_path / "d.eml", "d@example.co.jp", TANAKA, YAMADA, "Fri, 20 Nov 2026 12:00:00 +0900",
                    REPLY, irt="a@example.co.jp")
    wide_a = _eml(tmp_path / "a2.eml", "a2@example.co.jp", YAMADA, TANAKA, "Fri, 20 Nov 2026 10:00:00 +0900",
                  A_BODY, cc=SATO)
    rows, _ = convert_mail_inputs([wide_a, narrower], tenant_id="t1")
    assert _by_mid(rows, "a2@example.co.jp")["superseded_by"] == ""  # reply has fewer parties


# --- update safety ------------------------------------------------------------------


def _state(rows):
    return {r["source_doc"]: {"source_doc": r["source_doc"], "content_sha256": r["content_sha256"],
                              "date_iso": r["date_iso"], "sender_principal": r["sender_principal"]} for r in rows}


def test_reingest_same_message_id_update_policy(tmp_path):
    v1 = _eml(tmp_path / "v1.eml", "m@example.co.jp", YAMADA, TANAKA, "Fri, 20 Nov 2026 11:00:00 +0900", "版1の本文です。")
    existing = _state(convert_mail_inputs([v1], tenant_id="t1")[0])

    def check(name, frm, date, body):
        path = _eml(tmp_path / name, "m@example.co.jp", frm, TANAKA, date, body)
        return apply_update_policy(convert_mail_inputs([path], tenant_id="t1")[0], existing)

    keep, counts, _ = check("same.eml", YAMADA, "Fri, 20 Nov 2026 11:00:00 +0900", "版1の本文です。")
    assert not keep and counts["skipped_identical"] == 1
    keep, counts, _ = check("old.eml", YAMADA, "Fri, 20 Nov 2026 09:00:00 +0900", "古い版の本文です。")
    assert not keep and counts["skipped_older"] == 1
    keep, counts, _ = check("tie.eml", YAMADA, "Fri, 20 Nov 2026 11:00:00 +0900", "同時刻の別内容です。")
    assert not keep and counts["conflict_same_message_id"] == 1
    keep, counts, _ = check("forged.eml", SATO, "Sat, 21 Nov 2026 11:00:00 +0900", "なりすましの新しい版です。")
    assert not keep and counts["conflict_same_message_id"] == 1
    keep, counts, replaced = check("new.eml", YAMADA, "Sat, 21 Nov 2026 11:00:00 +0900", "版2の本文です。")
    assert len(keep) == 1 and counts["replaced_newer"] == 1 and replaced == {keep[0]["source_doc"]}


def test_jobs_state_store_cross_import_quotes_and_deletion(tmp_path, monkeypatch):
    from webapi import ingestion_jobs as ij
    from webapi import mail_ingestion_jobs as mj

    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setenv("INGESTION_REGISTRY_PATH", str(tmp_path / "registry.sqlite3"))
    monkeypatch.setenv("MAIL_CONVERT_TIMEOUT_SECONDS", "0")  # in-process; isolation is tested below
    monkeypatch.setattr(ij, "_vectorstore_has_collection", lambda _: False)
    written = []
    monkeypatch.setattr(ij, "_ingest_chunks", lambda rows, name: written.append(rows) or {"ingested": len(rows)})
    a = _original(tmp_path)
    first = mj.run_mail_job([str(a)], expected_tenant="t1", collection="t1_mail_stage", execute=True)
    assert first["ok"] and first["mail_counts"]["new"] == 1
    state_text = mj.mail_state_path("t1", "t1_mail_stage").read_text(encoding="utf-8")
    assert "@" not in state_text and "定例会議" not in state_text

    again = mj.run_mail_job([str(a)], expected_tenant="t1", collection="t1_mail_stage", execute=True)
    assert again["ok"] and again["nothing_to_write"] and again["mail_counts"]["skipped_identical"] == 1
    assert len(written) == 1

    b = _eml(tmp_path / "b.eml", "b@example.co.jp", TANAKA, YAMADA, "Fri, 20 Nov 2026 12:00:00 +0900", REPLY)
    second = mj.run_mail_job([str(b)], expected_tenant="t1", collection="t1_mail_stage", execute=True)
    assert second["ok"]
    reply = written[-1][0]
    a_doc = written[0][0]["source_doc"]
    assert reply["quote_refs"] == [a_doc]
    assert "未照合の引用" not in reply["display_text"] and "定例会議は十二月十日" not in reply["display_text"]

    plan = mj.delete_mail("t1", "t1_mail_stage", message_id="a@example.co.jp")
    assert plan["found"] and plan["source_doc"] == a_doc and plan["ids"] == [written[0][0]["id"]]
    assert plan["dependent_source_docs"] == [reply["source_doc"]] and not plan["committed"]
    assert mj.delete_mail("t1", "t1_mail_stage", source_doc=a_doc, commit=True)["committed"]
    assert a_doc not in mj.load_mail_state("t1", "t1_mail_stage")
    assert not plan_mail_deletion({}, "t1", message_id="a@example.co.jp")["found"]


# --- identity -----------------------------------------------------------------------


def test_table_identity_uses_file_bytes_not_file_name(tmp_path):
    columns = "from,to,date,subject,text\n"
    one, two = tmp_path / "x", tmp_path / "y"
    one.mkdir()
    two.mkdir()
    (one / "mails.csv").write_text(columns + f'"{YAMADA}",taro.tanaka@example.co.jp,2026-11-20 10:00,件名,本文その一\n',
                                   encoding="utf-8")
    (two / "mails.csv").write_text(columns + f'"{YAMADA}",taro.tanaka@example.co.jp,2026-11-20 10:00,件名,本文その二\n',
                                   encoding="utf-8")
    r1, _ = convert_mail_inputs([one / "mails.csv"], tenant_id="t1")
    r2, _ = convert_mail_inputs([two / "mails.csv"], tenant_id="t1")
    assert r1[0]["source_doc"] != r2[0]["source_doc"]

    head = "id,from,to,date,subject,text\n"
    line1 = f'EXPORT-0001,"{YAMADA}",taro.tanaka@example.co.jp,2026-11-20 10:00,件名,一通目の本文\n'
    line2 = f'EXPORT-0002,"{TANAKA}",taro.yamada@example.co.jp,2026-11-20 11:00,件名,二通目の本文\n'
    (one / "ids.csv").write_text(head + line1 + line2, encoding="utf-8")
    (two / "ids.csv").write_text(head + line2 + line1, encoding="utf-8")
    docs1 = {r["display_text"]: r["source_doc"] for r in convert_mail_inputs([one / "ids.csv"], tenant_id="t1")[0]}
    docs2 = {r["display_text"]: r["source_doc"] for r in convert_mail_inputs([two / "ids.csv"], tenant_id="t1")[0]}
    assert docs1 == docs2  # row reorder does not change identity when an id column exists


# --- isolation and MIME limits --------------------------------------------------------


def test_isolated_task_timeout_kills_the_worker(monkeypatch):
    from rag_core.document_converters import isolated

    monkeypatch.setitem(isolated._TASK_TARGETS, "sleep_stub", "time:sleep")
    started = time.monotonic()
    with pytest.raises(isolated.ConversionFailed) as exc:
        isolated.run_task("sleep_stub", {"__args__": [30]}, timeout=0.5)
    assert exc.value.error_type == "ConversionTimeout"
    assert time.monotonic() - started < 5
    with pytest.raises(ValueError, match="unknown_isolated_task"):
        isolated.run_task("not_registered", {}, timeout=1)


def test_isolated_mail_batch_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("MAIL_CONVERT_TIMEOUT_SECONDS", "60")
    batch = convert_mail_batch([_original(tmp_path)], tenant_id="t1", isolate=True)
    assert len(batch.rows) == 1 and batch.rows[0]["acl_principals"]
    assert batch.state[0]["sh"] and "@" not in json.dumps(batch.state)
    plain = tmp_path / "plain.csv"
    plain.write_text("商品,価格\nりんご,10\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_mail_input_format"):
        convert_mail_batch([plain], tenant_id="t1", isolate=True)


def _multipart(n_parts: int) -> str:
    parts = "".join(f"--B\nContent-Type: text/plain; charset=utf-8\n\n本文{i}\n" for i in range(n_parts))
    return (f"From: {YAMADA}\nTo: {TANAKA}\nMessage-ID: <mp@example.co.jp>\nSubject: 件名\n"
            "MIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=B\n\n" + parts + "--B--\n")


def _nested(depth: int) -> str:
    inner = "Content-Type: text/plain; charset=utf-8\n\n深い本文\n"
    for level in range(depth):
        inner = (f"Content-Type: multipart/mixed; boundary=L{level}\n\n--L{level}\n" + inner + f"--L{level}--\n")
    return f"From: {YAMADA}\nTo: {TANAKA}\nMIME-Version: 1.0\n" + inner


def test_mime_part_and_depth_caps(tmp_path, monkeypatch):
    many = tmp_path / "many.eml"
    many.write_text(_multipart(5), encoding="utf-8")
    assert "本文4" in mail_ingest.read_eml(many).body
    monkeypatch.setenv("MAIL_MAX_MIME_PARTS", "3")
    with pytest.raises(ValueError, match="mime_part_limit"):
        mail_ingest.read_eml(many)
    with pytest.raises(ValueError, match="mail_mime_limit"):
        convert_mail_inputs([many], tenant_id="t1")
    monkeypatch.delenv("MAIL_MAX_MIME_PARTS")
    deep = tmp_path / "deep.eml"
    deep.write_text(_nested(6), encoding="utf-8")
    assert "深い本文" in mail_ingest.read_eml(deep).body
    monkeypatch.setenv("MAIL_MAX_MIME_DEPTH", "3")
    with pytest.raises(ValueError, match="mime_depth_limit"):
        mail_ingest.read_eml(deep)
