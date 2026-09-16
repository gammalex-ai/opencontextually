"""ContextBench's own validation, not just its recall reporting.

A benchmark run that reports success without checking anything is worse
than no benchmark at all -- it looks like evidence. These tests cover the
failure modes that let that happen: an all-skipped/zero-case run exiting
0, a malformed or duplicate answer key loading silently, an unpinned
commit going unchecked, a stale answer-key file path going unchecked, and
a below-threshold recall exiting 0.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_DOGFOOD_PATH = Path(__file__).parent.parent / "benchmarks" / "dogfood.py"
_SPEC = importlib.util.spec_from_file_location("contextbench_dogfood_validation", _DOGFOOD_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_DOGFOOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_DOGFOOD)

load_answer_keys = _DOGFOOD.load_answer_keys
AnswerKeyError = _DOGFOOD.AnswerKeyError
check_commit = _DOGFOOD.check_commit
validate_answer_key_files = _DOGFOOD.validate_answer_key_files
main = _DOGFOOD.main


def _write_json(path: Path, data) -> Path:
    path.write_text(json.dumps(data))
    return path


# --- load_answer_keys: schema + duplicates ----------------------------


def test_duplicate_repo_task_pair_is_rejected(tmp_path):
    keys_path = _write_json(
        tmp_path / "keys.json",
        {
            "keys": [
                {"repo": "a", "task": "t", "files": {"x.py": "r"}},
                {"repo": "a", "task": "t", "files": {"y.py": "r"}},
            ]
        },
    )
    with pytest.raises(AnswerKeyError, match="duplicate"):
        load_answer_keys(keys_path)


def test_missing_required_field_is_rejected(tmp_path):
    keys_path = _write_json(tmp_path / "keys.json", {"keys": [{"repo": "a", "task": "t"}]})
    with pytest.raises(AnswerKeyError, match="files"):
        load_answer_keys(keys_path)


def test_empty_files_mapping_is_rejected(tmp_path):
    keys_path = _write_json(tmp_path / "keys.json", {"keys": [{"repo": "a", "task": "t", "files": {}}]})
    with pytest.raises(AnswerKeyError):
        load_answer_keys(keys_path)


def test_well_formed_keys_load_normally(tmp_path):
    keys_path = _write_json(
        tmp_path / "keys.json",
        {"keys": [{"repo": "a", "task": "t", "files": {"x.py": "r"}}]},
    )
    keys = load_answer_keys(keys_path)
    assert set(keys) == {("a", "t")}


# --- check_commit / validate_answer_key_files ---------------------------


def _init_repo(root: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "a"], cwd=root, check=True)
    (root / "x.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_check_commit_matches(tmp_path):
    head = _init_repo(tmp_path)
    assert check_commit(tmp_path, head) is None


def test_check_commit_mismatch_is_reported(tmp_path):
    _init_repo(tmp_path)
    error = check_commit(tmp_path, "0" * 40)
    assert error is not None
    assert "HEAD is" in error


def test_validate_answer_key_files_reports_missing_path(tmp_path):
    (tmp_path / "present.py").write_text("x = 1\n")
    answer_key = {"task": "t", "files": {"present.py": "r", "missing.py": "r"}}
    errors = validate_answer_key_files(tmp_path, answer_key)
    assert len(errors) == 1
    assert "missing.py" in errors[0]


# --- main(): zero-case and threshold failures ---------------------------


def _minimal_repo_with_answer_key(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("def helper():\n    return 1\n")

    corpus = _write_json(
        tmp_path / "corpus.json",
        {"repos": [{"root": str(repo), "repo": "demo", "tasks": ["fix the helper"]}]},
    )
    keys = _write_json(
        tmp_path / "keys.json",
        {"keys": [{"repo": "demo", "task": "fix the helper", "files": {"src.py": "r", "other.py": "r"}}]},
    )
    return repo, corpus, keys


def test_zero_cases_fails_even_without_strict(tmp_path):
    corpus = _write_json(
        tmp_path / "corpus.json",
        {"repos": [{"root": str(tmp_path / "does-not-exist"), "repo": "x", "tasks": ["t"]}]},
    )
    exit_code = main([str(corpus), "--no-determinism-check"])
    assert exit_code == 1


def test_min_recall_threshold_fails_run_below_it(tmp_path, capsys):
    _repo, corpus, keys = _minimal_repo_with_answer_key(tmp_path)

    exit_code = main(
        [str(corpus), "--no-determinism-check", "--answer-keys", str(keys), "--min-recall", "0.99"]
    )
    assert exit_code == 1
    assert "below --min-recall" in capsys.readouterr().err


def test_min_recall_threshold_passes_run_meeting_it(tmp_path):
    _repo, corpus, keys = _minimal_repo_with_answer_key(tmp_path)

    exit_code = main(
        [str(corpus), "--no-determinism-check", "--answer-keys", str(keys), "--min-recall", "0.0"]
    )
    assert exit_code == 0


def test_strict_fails_on_task_with_no_answer_key(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("x = 1\n")
    corpus = _write_json(
        tmp_path / "corpus.json",
        {"repos": [{"root": str(repo), "repo": "demo", "tasks": ["undocumented task"]}]},
    )
    empty_keys = _write_json(tmp_path / "keys.json", {"keys": []})

    exit_code = main([str(corpus), "--no-determinism-check", "--strict", "--answer-keys", str(empty_keys)])
    assert exit_code == 1


def test_strict_fails_on_stale_answer_key_file(tmp_path):
    _repo, corpus, keys = _minimal_repo_with_answer_key(tmp_path)

    exit_code = main([str(corpus), "--no-determinism-check", "--strict", "--answer-keys", str(keys)])
    assert exit_code == 1


def test_strict_fails_on_commit_mismatch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    corpus = _write_json(
        tmp_path / "corpus.json",
        {
            "repos": [
                {"root": str(repo), "repo": "demo", "tasks": ["fix the bug"], "commit": "0" * 40}
            ]
        },
    )
    keys = _write_json(
        tmp_path / "keys.json",
        {"keys": [{"repo": "demo", "task": "fix the bug", "files": {"x.py": "r"}}]},
    )

    exit_code = main([str(corpus), "--no-determinism-check", "--strict", "--answer-keys", str(keys)])
    assert exit_code == 1


def test_strict_passes_a_fully_consistent_corpus(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    head = _init_repo(repo)
    corpus = _write_json(
        tmp_path / "corpus.json",
        {"repos": [{"root": str(repo), "repo": "demo", "tasks": ["fix the bug"], "commit": head}]},
    )
    keys = _write_json(
        tmp_path / "keys.json",
        {"keys": [{"repo": "demo", "task": "fix the bug", "files": {"x.py": "r"}}]},
    )

    exit_code = main([str(corpus), "--no-determinism-check", "--strict", "--answer-keys", str(keys)])
    assert exit_code == 0
