from __future__ import annotations

import json
import sys

from scripts.build_canonical_index import (
    DEFAULT_OUTPUT_NAME,
    build_canonical_index,
    collect_input_paths,
    main,
)


def _write_jsonl(path, rows, *, extra_lines=()):
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    lines.extend(extra_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_build_dedups_and_sorts(tmp_path):
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    _write_jsonl(
        index_dir / "a.jsonl",
        [
            {"id": "c2", "text": "古い回答", "updated_at": "2026-01-01T00:00:00"},
            {"id": "c1", "text": "窓口案内"},
        ],
    )
    _write_jsonl(
        index_dir / "b.jsonl",
        [
            {"id": "c2", "text": "新しい回答", "updated_at": "2026-02-01T00:00:00"},
            {"text": "idなしの行"},
        ],
        extra_lines=["", "{bad json"],
    )
    output = index_dir / DEFAULT_OUTPUT_NAME

    stats = build_canonical_index(collect_input_paths(index_dir, output), output)

    rows = _read_jsonl(output)
    assert [row["id"] for row in rows] == ["c1", "c2"]
    assert rows[1]["text"] == "新しい回答"
    assert stats == {
        "input_files": 2,
        "records_read": 4,
        "skipped_no_id": 1,
        "duplicates_merged": 1,
        "records_written": 2,
    }


def test_collect_input_paths_excludes_output(tmp_path):
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    _write_jsonl(index_dir / "a.jsonl", [{"id": "c1", "text": "本文"}])
    output = index_dir / DEFAULT_OUTPUT_NAME
    _write_jsonl(output, [{"id": "old", "text": "前回の出力"}])

    paths = collect_input_paths(index_dir, output)

    assert [p.name for p in paths] == ["a.jsonl"]


def test_main_builds_default_output(tmp_path, monkeypatch, capsys):
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    _write_jsonl(index_dir / "a.jsonl", [{"id": "c1", "text": "本文"}])
    monkeypatch.setattr(sys, "argv", ["build_canonical_index.py", "--index-dir", str(index_dir)])

    assert main() == 0

    output = index_dir / DEFAULT_OUTPUT_NAME
    assert _read_jsonl(output) == [{"id": "c1", "text": "本文"}]
    summary = json.loads(capsys.readouterr().out.strip())
    assert summary["records_written"] == 1
    assert summary["output"] == str(output)
