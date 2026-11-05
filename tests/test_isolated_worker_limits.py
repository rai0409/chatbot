"""Conversion workers: OS memory limit and no inherited secrets. Synthetic data only."""
from __future__ import annotations

import zipfile


from rag_core.document_converters import isolated

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(path, text="経費精算の締め日は毎月25日です。"):
    body = f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", body)
    return path


def test_secret_looking_variables_are_listed_for_removal():
    environ = {"OPENAI_API_KEY": "x", "ADMIN_AUTH_TOKEN": "x", "DB_PASSWORD": "x", "OIDC_CLIENT_SECRET": "x",
               "PATH": "/usr/bin", "INGEST_CONVERT_WORKERS": "2", "DOCEXTRACT_TMP_DIR": "/t", "LANG": "C",
               "SHAREPOINT_CLIENT_SECRET": "x", "FEEDBACK_TOKEN_SECRET": "x"}
    assert sorted(isolated.secret_variable_names(environ)) == sorted(
        ["OPENAI_API_KEY", "ADMIN_AUTH_TOKEN", "DB_PASSWORD", "OIDC_CLIENT_SECRET", "SHAREPOINT_CLIENT_SECRET",
         "FEEDBACK_TOKEN_SECRET"])


def test_scrubbing_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("INGEST_CONVERT_SCRUB_ENV", "false")
    assert isolated.scrub_enabled() is False
    monkeypatch.delenv("INGEST_CONVERT_SCRUB_ENV")
    assert isolated.scrub_enabled() is True


def test_memory_limit_setting(monkeypatch):
    monkeypatch.delenv("INGEST_CONVERT_MAX_MEMORY_MB", raising=False)
    assert isolated.memory_limit_bytes() == 0  # off unless set: OCR engines need a large address space
    monkeypatch.setenv("INGEST_CONVERT_MAX_MEMORY_MB", "512")
    assert isolated.memory_limit_bytes() == 512 * 1024 * 1024
    monkeypatch.setenv("INGEST_CONVERT_MAX_MEMORY_MB", "abc")
    assert isolated.memory_limit_bytes() == 0


def test_a_worker_with_limits_still_converts_and_does_not_see_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-real")
    monkeypatch.setenv("INGEST_CONVERT_MAX_MEMORY_MB", "4096")
    path = _docx(tmp_path / "ok.docx")
    isolated.shutdown()
    out = isolated.convert_files([(path, "default", "ok.docx")], timeout=120)
    assert out[path] and not isinstance(out[path], Exception)
    probe = isolated.convert_files([(path, "default", "ok.docx")], timeout=120)
    assert probe[path]
    isolated.shutdown()


def test_the_worker_environment_report_says_the_secret_is_gone(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-real")
    isolated.shutdown()
    assert isolated.worker_sees_variable("OPENAI_API_KEY", timeout=120) is False
    monkeypatch.setenv("INGEST_CONVERT_SCRUB_ENV", "false")
    isolated.shutdown()
    assert isolated.worker_sees_variable("OPENAI_API_KEY", timeout=120) is True
    isolated.shutdown()
