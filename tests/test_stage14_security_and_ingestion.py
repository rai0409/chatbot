"""Stage 14 (P0): authentication fall-through, document names of uploads,
limits for hostile Office files, conversion time limits, concurrent keyword
corpus writes and interrupted ingestion jobs. Synthetic data only.
"""
from __future__ import annotations

import json
import threading
import time
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# --- 1. no credentials with OIDC / the proxy bridge on ---------------------------------------------


@pytest.mark.parametrize("flag", ["ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED"])
def test_requests_without_credentials_are_refused_when_sso_is_on(monkeypatch, flag):
    from webapi import api_auth

    for name in ("API_AUTH_ENABLED", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(flag, "true")
    with pytest.raises(HTTPException) as exc:
        api_auth.require_api_auth_headers({})
    assert exc.value.status_code == 401


def test_open_development_mode_is_unchanged(monkeypatch):
    from webapi import api_auth

    for name in ("API_AUTH_ENABLED", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    ctx = api_auth.require_api_auth_headers({})
    assert ctx.authenticated is False and ctx.tenant_allowed("default")


def test_non_ascii_api_key_is_a_403_not_a_500(monkeypatch):
    from webapi import api_auth

    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "test-key-placeholder-0123456789")
    with pytest.raises(HTTPException) as exc:
        api_auth.require_api_auth_headers({"x-api-key": "鍵のテスト"})
    assert exc.value.status_code == 403


# --- 2. document names -----------------------------------------------------------------------------


def test_upload_prefix_is_not_part_of_the_document_name(tmp_path):
    from webapi.ingestion_jobs import document_name

    upload = tmp_path / "uploads" / "default" / "20261021" / "0123456789ab_経費規程.pdf"
    assert document_name(upload) == "経費規程.pdf"
    # Outside the upload layout a matching name is kept as it is.
    assert document_name(tmp_path / "docs" / "0123456789ab_経費規程.pdf") == "0123456789ab_経費規程.pdf"


def test_two_inputs_with_one_document_name_are_refused(tmp_path, monkeypatch):
    from webapi import ingestion_jobs

    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(tmp_path))
    monkeypatch.setenv("INGESTION_REGISTRY_PATH", str(tmp_path / "registry.sqlite3"))
    monkeypatch.setenv("INGEST_CONVERT_TIMEOUT_SECONDS", "0")
    paths = []
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        path = tmp_path / folder / "手順書.csv"
        path.write_text("項目,内容\n申請,月末\n", encoding="utf-8")
        paths.append(str(path))
    job = ingestion_jobs.run_raw_document_job(paths, expected_tenant="default", collection="pilot_staging_v1")
    assert {w["reason"] for w in job["warnings"]} == {"duplicate_document_name"}


# --- 3. hostile Office files ---------------------------------------------------------------------


def _docx(path: Path, body: str) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
    return path


def test_zip_member_over_the_cap_is_refused(tmp_path, monkeypatch):
    from rag_core.document_converters.docx_converter import convert_docx
    from rag_core.document_converters.limits import DocumentTooLarge

    monkeypatch.setenv("OFFICE_MAX_MEMBER_BYTES", "1000")
    body = "<w:p><w:r><w:t>" + "あ" * 2000 + "</w:t></w:r></w:p>"
    with pytest.raises(DocumentTooLarge):
        convert_docx(_docx(tmp_path / "big.docx", body))


def test_high_compression_ratio_is_refused(tmp_path, monkeypatch):
    from rag_core.document_converters.limits import DocumentTooLarge, open_office_zip

    monkeypatch.setenv("OFFICE_MAX_RATIO", "100")
    path = tmp_path / "bomb.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", "0" * (3 * 1024 * 1024))
    with pytest.raises(DocumentTooLarge):
        open_office_zip(path)


def test_huge_grid_span_is_capped(tmp_path):
    from rag_core.document_converters.docx_converter import convert_docx

    cell = '<w:tc><w:tcPr><w:gridSpan w:val="1000000000"/></w:tcPr><w:p><w:r><w:t>値</w:t></w:r></w:p></w:tc>'
    started = time.monotonic()
    convert_docx(_docx(tmp_path / "span.docx", f"<w:tbl><w:tr>{cell}</w:tr></w:tbl>"))
    assert time.monotonic() - started < 5


def _xlsx(path: Path, sheet_xml: str) -> Path:
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>')
        zf.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        zf.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}">{sheet_xml}</worksheet>')
    return path


def test_whole_sheet_merge_range_does_not_explode(tmp_path):
    from rag_core.document_converters.xlsx_converter import convert_xlsx

    sheet = ('<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>項目</t></is></c><c r="B1" t="inlineStr"><is><t>内容</t></is></c></row>'
             '<row r="2"><c r="A2" t="inlineStr"><is><t>申請</t></is></c><c r="B2" t="inlineStr"><is><t>月末</t></is></c></row></sheetData>'
             '<mergeCells><mergeCell ref="A1:XFD1048576"/></mergeCells>')
    started = time.monotonic()
    convert_xlsx(_xlsx(tmp_path / "merge.xlsx", sheet))
    assert time.monotonic() - started < 5


def test_cell_in_the_last_column_is_refused(tmp_path):
    from rag_core.document_converters.limits import DocumentTooLarge
    from rag_core.document_converters.xlsx_converter import convert_xlsx

    sheet = ('<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>項目</t></is></c></row>'
             '<row r="2"><c r="XFD2" t="inlineStr"><is><t>x</t></is></c></row></sheetData>')
    with pytest.raises(DocumentTooLarge):
        convert_xlsx(_xlsx(tmp_path / "wide.xlsx", sheet))


# --- 4. conversion time limit --------------------------------------------------------------------


def test_conversion_over_the_time_limit_fails_and_the_next_works(tmp_path):
    from rag_core.document_converters import isolated

    path = _docx(tmp_path / "ok.docx", "<w:p><w:r><w:t>経費精算の締め日は毎月25日です。</w:t></w:r></w:p>")
    # No idle worker from earlier tests: starting one alone takes longer than 1 ms.
    isolated.shutdown()
    out = isolated.convert_files([(path, "default", "ok.docx")], timeout=0.001)
    assert isinstance(out[path], isolated.ConversionFailed) and out[path].error_type == "ConversionTimeout"
    out = isolated.convert_files([(path, "default", "renamed.docx")], timeout=120)
    rows = out[path]
    assert rows and rows[0]["source_doc"] == "renamed.docx"
    isolated.shutdown()


def test_conversion_errors_keep_their_type(tmp_path):
    from rag_core.document_converters import isolated

    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"PK\x03\x04 not a zip")
    out = isolated.convert_files([(broken, "default", None)], timeout=120)
    assert isinstance(out[broken], isolated.ConversionFailed) and out[broken].error_type == "BadZipFile"
    isolated.shutdown()


# --- 5. concurrent keyword corpus writes ---------------------------------------------------------


def test_concurrent_keyword_writes_lose_no_document(tmp_path):
    from scripts.ingest_canonical_jsonl import write_keyword_rows

    corpus = tmp_path / "keyword.jsonl"
    errors = []

    def write(n: int) -> None:
        try:
            write_keyword_rows(corpus, [{"id": f"doc{n}-c1", "text": f"本文{n}", "source_doc": f"doc{n}.pdf", "tenant_id": "default"}])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    rows = [json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines()]
    assert sorted(r["source_doc"] for r in rows) == sorted(f"doc{n}.pdf" for n in range(12))
    assert not [p for p in tmp_path.iterdir() if ".tmp-" in p.name]


# --- 7. interrupted jobs and collection status ---------------------------------------------------


def test_jobs_of_a_dead_process_are_marked_interrupted(tmp_path, monkeypatch):
    from webapi import ingestion_jobs

    monkeypatch.setenv("INGESTION_REGISTRY_PATH", str(tmp_path / "registry.sqlite3"))
    owner = ingestion_jobs._owner_token()
    dead = dict(owner, pid=2_000_000_000)
    ingestion_jobs._save_job({"job_id": "job-dead", "status": "running", "created_at": time.time(), "owner": dead,
                              "write_started": True})
    ingestion_jobs._save_job({"job_id": "job-live", "status": "running", "created_at": time.time(), "owner": owner})
    ingestion_jobs._save_job({"job_id": "job-other-host", "status": "queued", "created_at": time.time(),
                              "owner": dict(dead, host="000000000000")})
    assert ingestion_jobs.recover_interrupted_jobs() == 1
    record = ingestion_jobs.get_job("job-dead")
    assert record["status"] == "interrupted" and record["partial_write_possible"] is True
    assert ingestion_jobs.get_job("job-live")["status"] == "running"
    assert ingestion_jobs.get_job("job-other-host")["status"] == "queued"


def test_collection_is_not_served_while_it_is_rewritten(tmp_path, monkeypatch):
    from webapi import ingestion_jobs

    monkeypatch.setenv("INGESTION_REGISTRY_PATH", str(tmp_path / "registry.sqlite3"))
    ingestion_jobs.register_collection("pilot_staging_v1", owner_tenant="default", status=ingestion_jobs.COLLECTION_READY)
    assert ingestion_jobs.staging_collection_status("pilot_staging_v1", tenant_id="default")["allowed"]
    ingestion_jobs._claim_collection_for_write("pilot_staging_v1", "default", "job-x")
    assert ingestion_jobs.get_collection("pilot_staging_v1")["status"] == ingestion_jobs.COLLECTION_WRITING
    assert not ingestion_jobs.staging_collection_status("pilot_staging_v1", tenant_id="default")["allowed"]
    ingestion_jobs.register_collection("pilot_staging_v1", owner_tenant="default", status=ingestion_jobs.COLLECTION_READY)
    assert ingestion_jobs.staging_collection_status("pilot_staging_v1", tenant_id="default")["allowed"]
