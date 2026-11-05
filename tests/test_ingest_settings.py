"""Ingestion parameters that an administrator changes from the console. Synthetic data only."""
from __future__ import annotations

import os

import pytest

import config
from rag_core import ingest_settings as ist
from rag_core import runtime_settings as rs


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_DIR", str(tmp_path / "settings"))
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    present = {s.name: os.environ[s.name] for s in ist.SETTINGS if s.name in os.environ}
    for setting in ist.SETTINGS:
        monkeypatch.delenv(setting.name, raising=False)
    ist._ORIGINAL.clear()
    ist._ORIGINAL_ATTR.clear()
    ist._APPLIED.clear()
    ist._SEEN["rev"] = None
    yield
    for setting in ist.SETTINGS:  # sync_environ wrote into os.environ directly: do not leak into other tests
        os.environ.pop(setting.name, None)
    os.environ.update(present)
    ist._ORIGINAL.clear()
    ist._APPLIED.clear()
    ist._SEEN["rev"] = None


def _by(name):
    return next(s for s in ist.effective() if s["name"] == name)


def test_every_setting_has_a_unique_name_a_group_and_sane_bounds():
    names = [s.name for s in ist.SETTINGS]
    assert len(names) == len(set(names))
    for s in ist.SETTINGS:
        assert s.group in ist.GROUPS
        if s.kind in ("int", "float"):
            assert s.minimum is not None and s.maximum is not None and s.minimum <= s.default <= s.maximum, s.name
        assert rs.KEY_RE.fullmatch(ist.key_for(s.name)), s.name


def test_without_an_override_the_default_or_the_environment_applies(monkeypatch):
    assert _by("INGEST_UPLOAD_MAX_CONCURRENT")["source"] == "default"
    monkeypatch.setenv("INGEST_UPLOAD_MAX_CONCURRENT", "9")
    row = _by("INGEST_UPLOAD_MAX_CONCURRENT")
    assert row["source"] == "environment" and row["value"] == 9


def test_a_change_needs_a_reason_and_an_author():
    with pytest.raises(rs.SettingsError):
        ist.set_value("INGEST_UPLOAD_MAX_CONCURRENT", 8, by="山田太郎", reason="")
    with pytest.raises(rs.SettingsError):
        ist.set_value("INGEST_UPLOAD_MAX_CONCURRENT", 8, by="", reason="負荷のため")


@pytest.mark.parametrize("name, value", [
    ("INGEST_UPLOAD_MAX_CONCURRENT", -1), ("INGEST_UPLOAD_MAX_CONCURRENT", 100000), ("INGEST_UPLOAD_MAX_CONCURRENT", "abc"),
    ("INGEST_UPLOAD_MAX_CONCURRENT", 2.5), ("PDF_OCR_MIN_CONFIDENCE", 101), ("PDF_OCR_MIN_CONFIDENCE", float("nan")),
    ("PDF_TABLES", "maybe"), ("NOT_A_SETTING", 1), ("DOCEXTRACT_HEADING_RATIO", 0.5), ("INGEST_UPLOAD_MAX_BYTES", 10),
])
def test_invalid_values_are_refused(name, value):
    with pytest.raises(rs.SettingsError):
        ist.set_value(name, value, by="山田太郎", reason="試験")
    assert ist.overrides() == {}


def test_an_override_wins_and_is_applied_to_the_environment():
    ist.set_value("INGEST_UPLOAD_MAX_CONCURRENT", 8, by="山田太郎", reason="同時利用が増えた")
    row = _by("INGEST_UPLOAD_MAX_CONCURRENT")
    assert row["source"] == "override" and row["value"] == 8 and row["by"] == "山田太郎" and row["reason"] == "同時利用が増えた"
    ist.sync_environ()
    assert os.environ["INGEST_UPLOAD_MAX_CONCURRENT"] == "8"


def test_booleans_are_written_the_way_the_consumers_read_them():
    ist.set_value("PDF_TABLES", False, by="山田太郎", reason="表の誤検出が多い")
    ist.sync_environ()
    assert os.environ["PDF_TABLES"] == "false"
    ist.set_value("PDF_TABLES", "true", by="山田太郎", reason="戻す")
    ist.sync_environ()
    assert os.environ["PDF_TABLES"] == "true"


def test_a_reset_gives_back_the_deployments_own_environment_value(monkeypatch):
    monkeypatch.setenv("INGEST_UPLOAD_MAX_CONCURRENT", "3")
    ist.set_value("INGEST_UPLOAD_MAX_CONCURRENT", 20, by="山田太郎", reason="一時的に増やす")
    ist.sync_environ()
    assert os.environ["INGEST_UPLOAD_MAX_CONCURRENT"] == "20"
    ist.reset("INGEST_UPLOAD_MAX_CONCURRENT", by="山田太郎", reason="元に戻す")
    ist.sync_environ()
    assert os.environ["INGEST_UPLOAD_MAX_CONCURRENT"] == "3"
    assert _by("INGEST_UPLOAD_MAX_CONCURRENT")["source"] == "environment"


def test_a_reset_without_any_environment_value_removes_the_variable():
    ist.set_value("INGEST_UPLOAD_MAX_CONCURRENT", 20, by="山田太郎", reason="一時的に増やす")
    ist.sync_environ()
    ist.reset("INGEST_UPLOAD_MAX_CONCURRENT", by="山田太郎", reason="元に戻す")
    ist.sync_environ()
    assert "INGEST_UPLOAD_MAX_CONCURRENT" not in os.environ


def test_sync_environ_is_cheap_and_follows_other_processes_changes():
    ist.set_value("INGEST_UPLOAD_MAX_CONCURRENT", 5, by="山田太郎", reason="a")
    ist.sync_environ()
    # another worker process changes the shared store directly
    rs.set_value(ist.key_for("INGEST_UPLOAD_MAX_CONCURRENT"), 7, by="佐藤次郎", reason="b")
    ist.sync_environ()
    assert os.environ["INGEST_UPLOAD_MAX_CONCURRENT"] == "7"


def test_the_worker_environment_lists_every_managed_name_with_value_or_none():
    ist.set_value("INGEST_CONVERT_TIMEOUT_SECONDS", 900, by="山田太郎", reason="大きい PDF のため")
    env = ist.worker_environment()
    assert env["INGEST_CONVERT_TIMEOUT_SECONDS"] == "900"
    assert env["INGEST_UPLOAD_MAX_CONCURRENT"] is None  # no override: the worker uses its own environment
    assert set(env) == {s.name for s in ist.SETTINGS}


def test_changes_are_in_the_history_with_before_and_after():
    ist.set_value("PDF_OCR_MIN_CONFIDENCE", 70, by="山田太郎", reason="誤認識を減らす")
    ist.set_value("PDF_OCR_MIN_CONFIDENCE", 75, by="佐藤次郎", reason="さらに厳しく")
    rows = ist.history(limit=10)
    assert [(r["by"], r["before"], r["after"]) for r in rows][:2] == [("佐藤次郎", 70, 75), ("山田太郎", None, 70)]


def test_a_conflicting_change_is_refused():
    revision = rs.revision()
    ist.set_value("PDF_OCR_MIN_CONFIDENCE", 70, by="山田太郎", reason="a")
    with pytest.raises(rs.Conflict):
        ist.set_value("PDF_OCR_MIN_CONFIDENCE", 80, by="佐藤次郎", reason="b", expected_rev=revision)


def test_the_documented_default_matches_the_code(monkeypatch):
    # the default shown in the console must be what the code does without any setting
    from rag_core import chunking_ja

    for name, doc_type, index in (("JA_CHUNK_TARGET_POLICY_MIN_CHARS", chunking_ja.DOC_TYPE_POLICY, 0),
                                  ("JA_CHUNK_TARGET_POLICY_MAX_CHARS", chunking_ja.DOC_TYPE_POLICY, 1),
                                  ("JA_CHUNK_TARGET_FAQ_MAX_CHARS", chunking_ja.DOC_TYPE_FAQ, 1)):
        assert ist.definition(name).default == chunking_ja._DEFAULT_TARGETS[doc_type][index]


# --- review and approval switches (env read at call time) and one mirrored into the config module -----------------------------------------------


def test_the_review_switches_are_settings():
    names = {s.name for s in ist.SETTINGS if s.group == "review"}
    assert names == {"APPROVED_QA_ENABLED", "APPROVED_QA_REQUIRE_APPROVER", "EXTRACTION_REQUIRE_APPROVAL", "EXTRACTION_REVIEW_MAX_CONFIDENCE"}


def test_a_setting_mirrored_into_config_is_set_and_given_back(monkeypatch):
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False, raising=False)
    ist.set_value("APPROVED_QA_ENABLED", True, by="山田太郎", reason="承認済み QA を使う")
    assert config.APPROVED_QA_ENABLED is True and os.environ["APPROVED_QA_ENABLED"] == "true"
    ist.reset("APPROVED_QA_ENABLED", by="山田太郎", reason="戻す")
    assert config.APPROVED_QA_ENABLED is False and "APPROVED_QA_ENABLED" not in os.environ


def test_the_mirrored_value_follows_another_process_change(monkeypatch):
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False, raising=False)
    rs.set_value(ist.key_for("APPROVED_QA_ENABLED"), True, by="佐藤次郎", reason="b")
    ist.sync_environ()
    assert config.APPROVED_QA_ENABLED is True


def test_the_extraction_switch_is_read_by_the_review_code(monkeypatch):
    from rag_core import extraction_review

    assert extraction_review.require_approval() is False
    ist.set_value("EXTRACTION_REQUIRE_APPROVAL", True, by="山田太郎", reason="二者承認にする")
    assert extraction_review.require_approval() is True
