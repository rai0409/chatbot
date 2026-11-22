"""Mail ingest integration: synthetic mail only; never touches a real vectorstore."""
from __future__ import annotations

from pathlib import Path

import pytest

from rag_core.mail_to_canonical import convert_mail_inputs


def _eml(path: Path, mid: str, body: str, parent: str = "") -> Path:
    headers = (
        "From: Alice <alice@example.invalid>\n"
        "To: Bob <bob@example.invalid>\n"
        f"Message-ID: <{mid}>\n"
        f"Date: Fri, 20 Nov 2026 1{1 if not parent else 2}:00:00 +0900\n"
        "Subject: Delivery schedule\n"
        "Content-Type: text/plain; charset=utf-8\n"
    )
    if parent:
        headers += f"In-Reply-To: <{parent}>\nReferences: <{parent}>\n"
    path.write_text(headers + "\n" + body + "\n", encoding="utf-8")
    return path


def test_mail_convert_batch_preserves_reply_and_canonical(tmp_path):
    one = _eml(tmp_path / "one.eml", "m1@example.invalid", "Delivery is Dec 10.")
    two = _eml(tmp_path / "two.eml", "m2@example.invalid", "Delivery is Dec 15.", "m1@example.invalid")
    rows, summary = convert_mail_inputs([one, two], tenant_id="acme")
    assert len(rows) == 2 and summary["canonical_rows"] == 2
    assert len({r["id"] for r in rows}) == 2
    assert all(r["tenant_id"] == "acme" for r in rows)
    assert all(r["type"] == "email" for r in rows)
    assert all(r["source_doc"] and r["text"] and r["searchable_text"] for r in rows)
    assert len({r["source_doc"] for r in rows}) == 2
    assert next(r for r in rows if r["message_id"] == "m2@example.invalid")["parent_source_doc"] == next(
        r for r in rows if r["message_id"] == "m1@example.invalid"
    )["source_doc"]
    rows_again, _ = convert_mail_inputs([one, two], tenant_id="acme")
    assert [r["id"] for r in rows] == [r["id"] for r in rows_again]
    assert all("bcc" not in r for r in rows)


def test_mail_duplicate_conflicting_message_id_refused(tmp_path):
    one = _eml(tmp_path / "one.eml", "same@example.invalid", "Content 1")
    two = _eml(tmp_path / "two.eml", "same@example.invalid", "Content 2")
    with pytest.raises(ValueError, match="conflicting_message_identity"):
        convert_mail_inputs([one, two], tenant_id="acme")


def test_mail_limits_and_plain_csv_protection(tmp_path, monkeypatch):
    one = _eml(tmp_path / "one.eml", "m1@example.invalid", "test")
    monkeypatch.setenv("MAIL_INGEST_MAX_FILE_BYTES", "10")
    with pytest.raises(ValueError, match="mail_input_size_limit"):
        convert_mail_inputs([one], tenant_id="acme")
    monkeypatch.delenv("MAIL_INGEST_MAX_FILE_BYTES")
    csv = tmp_path / "plain.csv"
    csv.write_text("商品,価格\nりんご,10\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_mail_input_format"):
        convert_mail_inputs([csv], tenant_id="acme")


def test_mail_jobs_dry_run_execute_and_backend_fail_closed(tmp_path, monkeypatch):
    from webapi import ingestion_jobs as ij
    from webapi import mail_ingestion_jobs as mj

    one = _eml(tmp_path / "one.eml", "m1@example.invalid", "Delivery is Dec 10")
    two = _eml(tmp_path / "two.eml", "m2@example.invalid", "Delivery is Dec 15", "m1@example.invalid")
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setenv("INGESTION_REGISTRY_PATH", str(tmp_path / "registry.sqlite3"))
    collection = "test_mail_stage"
    paths = [str(one), str(two)]
    called = []
    monkeypatch.setattr(ij, "_ingest_chunks", lambda *a, **k: called.append((a, k)))
    result = mj.run_mail_job(paths, expected_tenant="acme", collection=collection)
    assert result["ok"] and result["mode"] == "mail_dry_run"
    assert result["chunks_generated"] == 2
    assert not called and ij.get_collection(collection) is None

    monkeypatch.setattr(ij, "_vectorstore_has_collection", lambda _: (_ for _ in ()).throw(ConnectionError("offline")))
    result = mj.run_mail_job(paths, expected_tenant="acme", collection=collection, execute=True)
    assert result["status"] == "error" and result["ok"] is False
    assert result.get("vectorstore_mutated") is False
    assert not called and ij.get_collection(collection) is None

    monkeypatch.setattr(ij, "_vectorstore_has_collection", lambda _: False)
    def fake_ingest(rows, name):
        assert name == collection and len(rows) == 2
        called.append(name)
        return {"ingested": len(rows), "embedding_fingerprint": {}}
    monkeypatch.setattr(ij, "_ingest_chunks", fake_ingest)
    result = mj.run_mail_job(paths, expected_tenant="acme", collection=collection, execute=True)
    assert result["ok"] and result["ingested_chunks"] == 2
    assert called == [collection]
    assert ij.get_collection(collection)["status"] == ij.COLLECTION_READY
    with pytest.raises(PermissionError):
        mj.run_mail_job(paths, expected_tenant="other", collection=collection, execute=True)


def test_backend_probe_outage_is_not_nonexistence(monkeypatch):
    from webapi import ingestion_jobs as ij
    from rag_core import vector_backend

    monkeypatch.setattr(vector_backend, "backend_name", lambda: "qdrant")
    class Down:
        def __init__(self, name):
            pass
        def exists(self):
            raise RuntimeError("network offline")
    monkeypatch.setattr(vector_backend, "QdrantCollection", Down)
    with pytest.raises(ConnectionError, match="cannot verify Qdrant"):
        ij._vectorstore_has_collection("any_staging")


def test_mail_upload_and_api_tenant_guard(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from webapi.routers import ingestion

    root = tmp_path / "root"
    own = root / "uploads" / "acme" / "20261121"
    other = root / "uploads" / "another" / "20261121"
    own.mkdir(parents=True)
    other.mkdir(parents=True)
    mine = _eml(own / "0123456789ab_one.eml", "m1@example.invalid", "Hello")
    theirs = _eml(other / "0123456789ab_two.eml", "m2@example.invalid", "Secret")
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(root))
    monkeypatch.setenv("INGESTION_REGISTRY_PATH", str(tmp_path / "registry.sqlite3"))
    monkeypatch.setenv("INGEST_UPLOAD_DIR", str(root / "uploads"))
    monkeypatch.setattr(ingestion, "_audit_admin", lambda *a, **k: None)

    ingestion._validate_upload(mine, ".eml", mine.read_bytes()[:8192])
    dry = ingestion.ingestion_mail(
        ingestion.MailIngestionRequest(inputs=[str(mine)], expected_tenant="acme", collection="acme_mail"),
        request=None,
    )
    assert dry["ok"] and dry["mode"] == "mail_dry_run"
    with pytest.raises(HTTPException) as exc:
        ingestion.ingestion_mail(
            ingestion.MailIngestionRequest(inputs=[str(theirs)], expected_tenant="acme", collection="acme_mail"),
            request=None,
        )
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as prod:
        ingestion.ingestion_mail(
            ingestion.MailIngestionRequest(inputs=[str(mine)], expected_tenant="acme", collection="default", execute=True),
            request=None,
        )
    assert prod.value.status_code == 400


def test_mail_table_csv_and_xlsx(tmp_path):
    import csv
    from openpyxl import Workbook

    columns = ["message_id", "in_reply_to", "from", "to", "date", "subject", "text"]
    sample = [
        ["m1@example.invalid", "", "Alice <alice@example.invalid>", "bob@example.invalid",
         "2026-11-20 10:00", "Delivery", "Dec 10"],
        ["m2@example.invalid", "m1@example.invalid", "Bob <bob@example.invalid>", "alice@example.invalid",
         "2026-11-20 12:00", "Re: Delivery", "Dec 15"],
    ]
    csv_path = tmp_path / "mail.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(columns)
        writer.writerows(sample)
    wb = Workbook()
    ws = wb.active
    ws.append(columns)
    for row in sample:
        ws.append(row)
    xlsx_path = tmp_path / "mail.xlsx"
    wb.save(xlsx_path)

    for path in (csv_path, xlsx_path):
        rows, summary = convert_mail_inputs([path], tenant_id="acme")
        assert summary["canonical_rows"] == 2
        assert len(rows) == 2
        assert next(r for r in rows if r["message_id"] == "m2@example.invalid")["parent_source_doc"]


def test_mail_api_refuses_shared_root_even_when_allowed(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from webapi.routers import ingestion

    root = tmp_path / "root"
    root.mkdir()
    private = _eml(root / "outside_uploads.eml", "m3@example.invalid", "Private mail")
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(root))
    monkeypatch.setenv("INGEST_UPLOAD_DIR", str(root / "uploads"))
    with pytest.raises(HTTPException) as exc:
        ingestion.ingestion_mail(
            ingestion.MailIngestionRequest(inputs=[str(private)], expected_tenant="acme", collection="acme_mail"),
            request=None,
        )
    assert exc.value.status_code == 403
