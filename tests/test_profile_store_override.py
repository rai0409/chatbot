import json

import pytest

from rag_core import answer_cache, profile_loader, profile_store, query_preprocess

PID = "tenant_a"


def _write(directory, name, value):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _source(directory, variants):
    _write(directory, "profile.json", {"project_id": PID})
    _write(directory, "question_type_rules.json", {"rules": []})
    _write(directory, "domain_terms.json", {"terms": []})
    _write(directory, "synonyms.json", {"synonyms": [{"canonical": "サーバー", "variants": variants}]})
    _write(directory, "retrieval_boost_rules.json", {"rules": []})
    _write(directory, "validation_rules.json", {"rules": []})
    _write(directory, "answer_templates.json", {"templates": []})
    (directory / "golden_qa.jsonl").write_text("", encoding="utf-8")


@pytest.fixture
def versions(monkeypatch, tmp_path):
    monkeypatch.setattr(profile_loader, "PROFILE_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(query_preprocess, "RULES_DIR", tmp_path / "rules")
    monkeypatch.setenv("PROFILE_STORE_DIR", str(tmp_path / "store"))
    source = tmp_path / "profiles" / PID / "exported"
    _source(source, ["サーバ"])
    v1 = profile_store.create_version(PID)["version"]
    _source(source, ["サーバ", "ｻｰﾊﾞｰ"])
    v2 = profile_store.create_version(PID)["version"]
    profile_store.activate(PID, v1)
    profile_loader.reload_rag_profile_cache()
    yield v1, v2
    profile_loader.reload_rag_profile_cache()


def _variants():
    return [v for entry in profile_loader.load_rag_profile(PID)["synonyms"] for v in entry["variants"]]


def test_an_override_serves_the_candidate_in_this_process_only(versions):
    v1, v2 = versions
    assert _variants() == ["サーバ"] and profile_loader.pin_state(PID) == v1
    key_before = answer_cache._profile_state("default", PID)
    with profile_store.override_version(PID, v2):
        assert _variants() == ["サーバ", "ｻｰﾊﾞｰ"] and profile_loader.pin_state(PID) == v2
        assert profile_loader.load_rag_profile(PID)["profile_version"] == v2
        assert answer_cache._profile_state("default", PID) != key_before
        assert profile_store.status(PID)["active"]["version"] == v1  # the pointer file is untouched
    assert _variants() == ["サーバ"] and profile_loader.pin_state(PID) == v1
    assert answer_cache._profile_state("default", PID) == key_before


def test_an_override_works_without_an_active_version_and_nests(versions):
    v1, v2 = versions
    profile_store.clear(PID)
    assert profile_loader.pin_state(PID) == ""
    with profile_store.override_version(PID, v1):
        assert profile_loader.pin_state(PID) == v1
        with profile_store.override_version(PID, v2):
            assert profile_loader.pin_state(PID) == v2
        assert profile_loader.pin_state(PID) == v1
    assert profile_loader.pin_state(PID) == ""


def test_an_override_is_removed_even_when_the_block_raises(versions):
    v1, v2 = versions
    with pytest.raises(RuntimeError):
        with profile_store.override_version(PID, v2):
            raise RuntimeError("boom")
    assert profile_loader.pin_state(PID) == v1


def test_an_unknown_or_tampered_version_cannot_be_overridden(versions):
    v1, v2 = versions
    with pytest.raises(profile_store.ProfileStoreError):
        with profile_store.override_version(PID, "0123456789abcdef"):
            pass
    with pytest.raises(profile_store.ProfileStoreError):
        with profile_store.override_version("../x", v1):
            pass
    (profile_store.version_dir(PID, v2) / "synonyms.json").write_text('{"synonyms": []}', encoding="utf-8")
    with pytest.raises(profile_store.ProfileStoreError):
        with profile_store.override_version(PID, v2):
            pass
    assert profile_loader.pin_state(PID) == v1
