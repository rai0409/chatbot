"""S7: the one-file bundle that carries the changes to another checkout (scripts/make_airdrop_bundle.py, scripts/airdrop_apply.py)."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


maker = _load("make_airdrop_bundle")


def _git(path, *args):
    subprocess.run(["git", "-c", "user.name=Taro_Yamada", "-c", "user.email=taro@example.co.jp", *args], cwd=path, check=True, capture_output=True)


@pytest.fixture
def work(tmp_path):
    """A repository with a base commit, then one changed file, one new file, one new file in a skipped folder."""
    repo = tmp_path / "work"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("base a\n", encoding="utf-8")
    (repo / "keep.txt").write_text("unchanged\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "a.txt").write_text("new a\n", encoding="utf-8")
    (repo / "sub").mkdir()
    (repo / "sub" / "b.txt").write_text("日本語の新規ファイル\n", encoding="utf-8")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "skip.js").write_text("x", encoding="utf-8")
    return repo


def _bundle(work, tmp_path):
    out = tmp_path / "out" / "apply.py"
    meta = maker.build(work, out, now="2026-10-29 21:00")
    return out, meta


def _checkout_of_base(tmp_path, work):
    target = tmp_path / "target"
    subprocess.run(["git", "clone", "-q", str(work), str(target)], check=True, capture_output=True)  # a clone has the base files only
    return target


def _run(apply_file, *args):
    return subprocess.run([sys.executable, "-I", str(apply_file), *args], capture_output=True, text=True)


def test_the_bundle_carries_changed_and_new_files_but_not_the_skipped_ones(work, tmp_path):
    _, meta = _bundle(work, tmp_path)
    assert {(f["path"], f["status"]) for f in meta["files"]} == {("a.txt", "M"), ("sub/b.txt", "A")}
    assert meta["warnings"] == []


def test_demo_writes_nothing_then_apply_brings_the_target_to_the_work_tree(work, tmp_path):
    apply_file, _ = _bundle(work, tmp_path)
    target = _checkout_of_base(tmp_path, work)
    demo = _run(apply_file, "--target", str(target))
    assert demo.returncode == 0 and "demo" in demo.stdout and "add 1 / update 1" in demo.stdout
    assert (target / "a.txt").read_text(encoding="utf-8") == "base a\n" and not (target / "sub").exists()
    done = _run(apply_file, "--target", str(target), "--apply")
    assert done.returncode == 0, done.stdout + done.stderr
    assert (target / "a.txt").read_text(encoding="utf-8") == "new a\n"
    assert (target / "sub" / "b.txt").read_text(encoding="utf-8") == "日本語の新規ファイル\n"
    assert (target / "keep.txt").read_text(encoding="utf-8") == "unchanged\n"
    backups = list(target.glob(".airdrop_backup_*/a.txt"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "base a\n"  # the replaced original is kept
    again = _run(apply_file, "--target", str(target), "--apply")
    assert "already applied 2" in again.stdout and "done: 0 files" in again.stdout  # running it again changes nothing


def test_a_file_changed_by_someone_else_is_a_conflict_and_nothing_is_written(work, tmp_path):
    apply_file, _ = _bundle(work, tmp_path)
    target = _checkout_of_base(tmp_path, work)
    (target / "a.txt").write_text("someone else's work\n", encoding="utf-8")
    result = _run(apply_file, "--target", str(target), "--apply")
    assert result.returncode == 1 and "CONFLICT a.txt" in result.stdout
    assert (target / "a.txt").read_text(encoding="utf-8") == "someone else's work\n" and not (target / "sub").exists()
    forced = _run(apply_file, "--target", str(target), "--apply", "--force")
    assert forced.returncode == 0 and (target / "a.txt").read_text(encoding="utf-8") == "new a\n"
    assert next(target.glob(".airdrop_backup_*/a.txt")).read_text(encoding="utf-8") == "someone else's work\n"


def test_a_checkout_with_other_line_endings_is_still_recognised_as_the_base(work, tmp_path):
    apply_file, _ = _bundle(work, tmp_path)
    target = _checkout_of_base(tmp_path, work)
    (target / "a.txt").write_bytes(b"base a\r\n")
    assert _run(apply_file, "--target", str(target), "--apply").returncode == 0


def test_a_damaged_bundle_is_refused_and_a_path_that_leaves_the_target_is_refused(work, tmp_path):
    apply_file, meta = _bundle(work, tmp_path)
    target = _checkout_of_base(tmp_path, work)
    text = apply_file.read_text(encoding="utf-8")
    start = text.index("BUNDLE_B64 = (\n") + len("BUNDLE_B64 = (\n")
    damaged = apply_file.with_name("damaged.py")
    damaged.write_text(text[:start] + '    "AAAA"\n' + text[text.index("\n)\n", start):], encoding="utf-8")
    result = _run(damaged, "--target", str(target), "--apply")
    assert result.returncode == 2 and "damaged" in result.stdout
    applier = _load("airdrop_apply")
    for bad in ("../escape.txt", "/etc/passwd", "a/../../b", "C:/x"):
        with pytest.raises(ValueError):
            applier._safe_relative(bad)


def test_a_deleted_file_stops_the_bundle_because_it_never_deletes(work, tmp_path):
    (work / "keep.txt").unlink()
    with pytest.raises(SystemExit) as stopped:
        maker.collect(work)
    assert "keep.txt" in str(stopped.value)


def test_an_address_outside_the_reserved_domains_is_reported(work, tmp_path):
    (work / "c.txt").write_text("contact: user@mail.invalid\nok: taro@example.co.jp\n", encoding="utf-8")
    _, meta = _bundle(work, tmp_path)
    assert any(w.startswith("c.txt") for w in meta["warnings"])
