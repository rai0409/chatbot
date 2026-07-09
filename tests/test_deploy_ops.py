from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPTS = [
    Path("scripts/deploy_smoke.sh"),
    Path("scripts/backup.sh"),
    Path("scripts/restore.sh"),
]


def _run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


# --- static safety -------------------------------------------------------------


def test_scripts_pass_bash_syntax_check():
    for script in SCRIPTS:
        result = _run(["bash", "-n", str(script)])
        assert result.returncode == 0, (script, result.stderr)


def test_scripts_never_read_the_repo_dotenv():
    forbidden = ["cp .env", "cat .env", "source .env", ". .env", "cp ./.env", "env_file: .env\n"]
    for script in SCRIPTS:
        text = script.read_text(encoding="utf-8")
        for pattern in forbidden:
            assert pattern not in text, (script, pattern)


def test_deploy_smoke_never_mounts_live_repo_state_dirs():
    text = Path("scripts/deploy_smoke.sh").read_text(encoding="utf-8")
    # All volume binds must come from the temp SMOKE_DIR, never the repo dirs.
    for live in ("REPO_DIR}/vectorstore", "REPO_DIR}/data", "REPO_DIR}/runs", "REPO_DIR}/index"):
        assert live not in text


# --- deploy smoke env generation (no docker needed) ------------------------------


def test_env_generation_writes_placeholders_and_marker(tmp_path):
    env_file = tmp_path / "env.smoke"
    result = _run(
        ["bash", "scripts/deploy_smoke.sh", "--check-env-only", "--env-file", str(env_file)]
    )

    assert result.returncode == 0, result.stderr
    content = env_file.read_text(encoding="utf-8")
    assert content.startswith("# generated-by=deploy_smoke.sh")
    assert "OPENAI_API_KEY=OPENAI_KEY_PLACEHOLDER_NOT_REAL" in content
    assert "API_AUTH_ENABLED=true" in content
    assert "SEARCH_DEBUG_ENABLED=false" in content


def test_env_generation_overwrites_only_its_own_files(tmp_path):
    env_file = tmp_path / "env.smoke"
    first = _run(["bash", "scripts/deploy_smoke.sh", "--check-env-only", "--env-file", str(env_file)])
    assert first.returncode == 0
    # Re-running over its own marker file is fine.
    second = _run(["bash", "scripts/deploy_smoke.sh", "--check-env-only", "--env-file", str(env_file)])
    assert second.returncode == 0

    foreign = tmp_path / "precious.txt"
    foreign.write_text("do not clobber", encoding="utf-8")
    refused = _run(["bash", "scripts/deploy_smoke.sh", "--check-env-only", "--env-file", str(foreign)])
    assert refused.returncode == 2
    assert "refusing to overwrite" in refused.stderr
    assert foreign.read_text(encoding="utf-8") == "do not clobber"


# --- backup / restore round trip --------------------------------------------------


def _make_source(tmp_path: Path) -> Path:
    src = tmp_path / "source"
    (src / "vectorstore").mkdir(parents=True)
    (src / "data" / "approved_qa").mkdir(parents=True)
    (src / "runs" / "audit").mkdir(parents=True)
    (src / "vectorstore" / "chroma.bin").write_bytes(b"\x00\x01synthetic")
    (src / "data" / "approved_qa" / "default.jsonl").write_text(
        '{"qa_id":"qa1","question":"q","approved_answer":"a","status":"approved"}\n',
        encoding="utf-8",
    )
    (src / "runs" / "audit" / "chat_events.jsonl").write_text('{"event":1}\n', encoding="utf-8")
    return src


def test_backup_restore_round_trip_with_hash_verification(tmp_path):
    src = _make_source(tmp_path)
    out = tmp_path / "backups"

    backup = _run(["bash", "scripts/backup.sh", "--source-dir", str(src), "--output-dir", str(out)])
    assert backup.returncode == 0, backup.stderr
    archives = list(out.glob("chatbot_backup_*.tar.gz"))
    assert len(archives) == 1

    target = tmp_path / "restored"
    restore = _run(["bash", "scripts/restore.sh", str(archives[0]), "--target", str(target)])
    assert restore.returncode == 0, restore.stderr
    assert "restore verified: 3 files OK" in restore.stdout
    assert (target / "vectorstore" / "chroma.bin").read_bytes() == b"\x00\x01synthetic"
    assert (target / "data" / "approved_qa" / "default.jsonl").exists()
    assert (target / "runs" / "audit" / "chat_events.jsonl").exists()


def test_restore_fails_on_corrupted_archive_content(tmp_path):
    src = _make_source(tmp_path)
    out = tmp_path / "backups"
    assert _run(["bash", "scripts/backup.sh", "--source-dir", str(src), "--output-dir", str(out)]).returncode == 0
    archive = next(out.glob("chatbot_backup_*.tar.gz"))

    # Corrupt one restored file by tampering with the extracted tree via a
    # pre-seeded target, then re-verify manifest through restore.sh on a
    # tampered re-pack.
    work = tmp_path / "tamper"
    work.mkdir()
    _run(["tar", "-xzf", str(archive), "-C", str(work)])
    (work / "vectorstore" / "chroma.bin").write_bytes(b"tampered")
    tampered = tmp_path / "tampered.tar.gz"
    _run(
        ["tar", "-czf", str(tampered), "-C", str(work), "backup_manifest.sha256", "vectorstore", "data", "runs"]
    )

    target = tmp_path / "restored"
    restore = _run(["bash", "scripts/restore.sh", str(tampered), "--target", str(target)])
    assert restore.returncode != 0


def test_backup_refuses_empty_source(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _run(["bash", "scripts/backup.sh", "--source-dir", str(empty), "--output-dir", str(tmp_path / "b")])
    assert result.returncode == 1
    assert "nothing to back up" in result.stderr
