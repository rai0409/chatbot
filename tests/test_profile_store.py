import json

import pytest

from rag_core import answer_cache, profile_loader, profile_store, query_preprocess
from scripts import profile_versions

PID = "tenant_a"


def _write(directory, name, value):
    directory.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    (directory / name).write_text(text, encoding="utf-8")


def _source(directory, *, synonyms=("サーバ",), golden_extra=False):
    _write(directory, "profile.json", {"project_id": PID})
    _write(directory, "question_type_rules.json", {"rules": []})
    _write(directory, "domain_terms.json", {"terms": []})
    _write(directory, "synonyms.json", {"synonyms": [{"canonical": "サーバー", "variants": list(synonyms)}]})
    _write(directory, "retrieval_boost_rules.json", {"rules": []})
    _write(directory, "validation_rules.json", {"rules": []})
    _write(directory, "answer_templates.json", {"templates": []})
    rows = ['{"id": "g1", "question": "質問", "expected_all": ["回答"]}']
    if golden_extra:
        rows.append('{"id": "g2", "question": "別の質問", "expected_all": ["回答"]}')
    _write(directory, "golden_qa.jsonl", "\n".join(rows) + "\n")


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(profile_loader, "PROFILE_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(query_preprocess, "RULES_DIR", tmp_path / "rules")
    monkeypatch.setenv("PROFILE_STORE_DIR", str(tmp_path / "store"))
    source = tmp_path / "profiles" / PID / "exported"
    _source(source)
    profile_loader.reload_rag_profile_cache()
    yield source
    profile_loader.reload_rag_profile_cache()


def _synonym_variants(profile):
    return [v for entry in profile["synonyms"] for v in entry["variants"]]


def test_a_version_is_named_by_the_normalized_content(env, tmp_path):
    first = profile_store.create_version(PID, created_by="山田太郎", note="初版")
    assert first["created"] is True and len(first["version"]) == 16 and first["created_by"] == "山田太郎"

    # same content in another layout (key order, indentation, blank lines) is the same version, nothing is written
    _write(env, "synonyms.json", '{"synonyms":[{"variants":["サーバ"],"canonical":"サーバー"}]}')
    _write(env, "golden_qa.jsonl", '\n{"expected_all":["回答"],"question":"質問","id":"g1"}\n\n')
    again = profile_store.create_version(PID)
    assert again["created"] is False and again["version"] == first["version"]
    assert [row["version"] for row in profile_store.list_versions(PID)] == [first["version"]]

    # a changed value is a new version that remembers the active one as its parent
    profile_store.activate(PID, first["version"])
    _source(env, synonyms=("サーバ", "ｻｰﾊﾞｰ"))
    second = profile_store.create_version(PID)
    assert second["created"] is True and second["version"] != first["version"]
    assert second["parent"] == first["version"]
    # the golden questions are part of the content too
    _source(env, synonyms=("サーバ", "ｻｰﾊﾞｰ"), golden_extra=True)
    assert profile_store.create_version(PID)["version"] not in (first["version"], second["version"])


def test_a_source_that_is_not_json_does_not_become_a_version(env):
    _write(env, "synonyms.json", "{not json")
    with pytest.raises(profile_store.ProfileStoreError, match="synonyms.json: invalid JSON"):
        profile_store.create_version(PID)
    assert profile_store.list_versions(PID) == []
    with pytest.raises(profile_store.ProfileStoreError):
        profile_store.create_version(PID, source=env.parent / "missing")
    with pytest.raises(profile_store.ProfileStoreError):
        profile_store.create_version(PID, source=env.parent.parent)  # no profile files in it


@pytest.mark.parametrize("profile", ["../x", "a/b", "", "x" * 65, "日本語"])
def test_unsafe_profile_ids_are_rejected(env, profile):
    with pytest.raises(profile_store.ProfileStoreError):
        profile_store.create_version(profile)
    with pytest.raises(profile_store.ProfileStoreError):
        profile_store.pinned_version(profile)


@pytest.mark.parametrize("version", ["../../x", "ABCDEF0123456789", "abc", "g" * 16, "0123456789abcdef0"])
def test_unsafe_versions_are_rejected(env, version):
    with pytest.raises(profile_store.ProfileStoreError):
        profile_store.version_dir(PID, version)
    with pytest.raises(profile_store.ProfileStoreError):
        profile_store.activate(PID, version)


def test_a_tampered_or_missing_version_is_refused(env):
    version = profile_store.create_version(PID)["version"]
    assert profile_store.verify_version(PID, version)["version"] == version
    stored = profile_store.version_dir(PID, version)

    (stored / "synonyms.json").write_text('{"synonyms": []}', encoding="utf-8")
    with pytest.raises(profile_store.ProfileStoreError, match="do not match the digest"):
        profile_store.verify_version(PID, version)
    with pytest.raises(profile_store.ProfileStoreError):
        profile_store.activate(PID, version)
    assert profile_store.pinned_version(PID) is None

    with pytest.raises(profile_store.ProfileStoreError, match="not found"):
        profile_store.read_version(PID, "0123456789abcdef")


def test_an_added_file_is_a_mismatch_too(env):
    version = profile_store.create_version(PID)["version"]
    stored = profile_store.version_dir(PID, version)
    (stored / "profile.json").unlink()
    with pytest.raises(profile_store.ProfileStoreError):
        profile_store.verify_version(PID, version)


def test_without_a_pointer_the_profile_comes_from_config_as_before(env):
    profile = profile_loader.load_rag_profile(PID)
    assert profile["warnings"] == [] and profile["profile_pinned"] is False
    assert _synonym_variants(profile) == ["サーバ"]
    assert profile["profile_version"] == profile_store.content_digest(env)[:16]
    assert profile_loader.active_profile_dir(PID) == env.resolve()
    assert profile_loader.pin_state(PID) == ""


def test_the_pinned_version_is_served_until_it_is_changed(env):
    v1 = profile_store.create_version(PID)["version"]
    profile_store.activate(PID, v1, activated_by="佐藤次郎")
    _source(env, synonyms=("サーバ", "ｻｰﾊﾞｰ"))  # config/ is edited; the pin does not move
    profile = profile_loader.load_rag_profile(PID)  # no cache reload on purpose
    assert profile["profile_pinned"] is True and profile["profile_version"] == v1
    assert _synonym_variants(profile) == ["サーバ"] and profile["warnings"] == []
    assert profile_loader.pin_state(PID) == v1
    assert profile_loader.active_profile_dir(PID) == profile_store.version_dir(PID, v1)

    v2 = profile_store.create_version(PID)["version"]
    profile_store.activate(PID, v2)
    assert _synonym_variants(profile_loader.load_rag_profile(PID)) == ["サーバ", "ｻｰﾊﾞｰ"]

    profile_store.rollback(PID)
    assert profile_loader.load_rag_profile(PID)["profile_version"] == v1
    profile_store.clear(PID)
    cleared = profile_loader.load_rag_profile(PID)
    assert cleared["profile_pinned"] is False and cleared["profile_version"] == v2  # config/ holds v2's content now
    profile_store.rollback(PID)  # the cleared version comes back
    assert profile_loader.load_rag_profile(PID)["profile_version"] == v1


def test_activate_and_rollback_refuse_what_cannot_be_done(env):
    v1 = profile_store.create_version(PID)["version"]
    with pytest.raises(profile_store.ProfileStoreError, match="no previous"):
        profile_store.rollback(PID)
    profile_store.activate(PID, v1)
    with pytest.raises(profile_store.ProfileStoreError, match="already active"):
        profile_store.activate(PID, v1)
    status = profile_store.status(PID)
    assert status["active"]["version"] == v1 and status["history"] == [] and status["versions"] == [v1]


def test_a_broken_pointer_gives_an_empty_profile_with_a_warning_not_another_version(env, tmp_path):
    v1 = profile_store.create_version(PID)["version"]
    profile_store.activate(PID, v1)
    profile_store.pointer_path(PID).write_text("{broken", encoding="utf-8")
    profile = profile_loader.load_rag_profile(PID)
    assert profile["synonyms"] == [] and profile["warnings"]
    assert str(tmp_path) not in " ".join(profile["warnings"])
    assert profile_loader.pin_state(PID) == "!unusable"

    profile_store.pointer_path(PID).write_text(
        json.dumps({"schema_version": 1, "active": {"version": "0123456789abcdef"}, "history": []}), encoding="utf-8"
    )
    profile = profile_loader.load_rag_profile(PID)  # points at a version that is not in the store
    assert profile["synonyms"] == [] and "missing from the store" in " ".join(profile["warnings"])


def test_a_missing_profile_falls_back_to_the_pinned_default(env, tmp_path):
    default = tmp_path / "profiles" / "default" / "exported"
    _source(default, synonyms=("既定",))
    v1 = profile_store.create_version("default")["version"]
    profile_store.activate("default", v1)
    _source(default, synonyms=("編集後",))
    profile = profile_loader.load_rag_profile("unknown_tenant")
    assert _synonym_variants(profile) == ["既定"] and profile["profile_pinned"] is True
    assert any("loaded default profile instead" in w for w in profile["warnings"])


def test_the_answer_cache_state_follows_the_pin(env):
    unpinned = answer_cache._profile_state("default", PID)
    v1 = profile_store.create_version(PID)["version"]
    profile_store.activate(PID, v1)
    pinned = answer_cache._profile_state("default", PID)
    assert pinned != unpinned and v1 in pinned

    _source(env, synonyms=("サーバ", "ｻｰﾊﾞｰ"))
    v2 = profile_store.create_version(PID)["version"]
    profile_store.activate(PID, v2)
    assert answer_cache._profile_state("default", PID) not in (pinned, unpinned)
    profile_store.rollback(PID)
    assert answer_cache._profile_state("default", PID) == pinned


def test_diff_names_the_files_that_differ(env):
    v1 = profile_store.create_version(PID)["version"]
    assert profile_store.diff_versions(PID, v1, "source")["identical"] is True
    _source(env, synonyms=("サーバ", "ｻｰﾊﾞｰ"))
    (env / "answer_templates.json").unlink()
    result = profile_store.diff_versions(PID, v1, "source")
    assert result["identical"] is False
    assert {k: v for k, v in result["files"].items() if v != "same"} == {
        "synonyms.json": "changed",
        "answer_templates.json": "removed",
    }


def test_the_command_line_round_trip(env, capsys):
    def run(*argv):
        code = profile_versions.main(list(argv))
        return code, json.loads(capsys.readouterr().out)

    code, created = run("create", "--profile", PID, "--by", "田中太郎", "--note", "初版")
    assert code == 0 and created["created"] is True
    version = created["version"]
    assert run("list", "--profile", PID)[1]["versions"][0]["version"] == version
    assert run("show", "--profile", PID, "--version", version)[1]["note"] == "初版"
    assert run("verify", "--profile", PID)[0] == 1  # no version given and none is active
    code, activated = run("activate", "--profile", PID, "--version", version)
    assert code == 0 and activated["active"]["version"] == version
    code, verified = run("verify", "--profile", PID)
    assert code == 0 and verified["verified"] is True
    assert run("diff", "--profile", PID, version, "source")[1]["identical"] is True
    assert run("status", "--profile", PID)[1]["active"]["version"] == version
    assert run("activate", "--profile", PID, "--version", version)[0] == 1  # already active
    assert run("activate", "--profile", PID)[0] == 1  # --version missing
    assert run("show", "--profile", "../x", "--version", version)[0] == 1
    assert run("clear", "--profile", PID)[1]["active"] is None
    assert run("rollback", "--profile", PID)[1]["active"]["version"] == version
