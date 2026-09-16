"""Excerpt-hit recall: file recall alone can't tell "right file, wrong
excerpt" from a real recovery.

The fastapi corpus case demonstrated this for real: the benchmark awarded
4/4 file recall for a task while `fastapi/routing.py`'s only delivered
excerpt was an unrelated pragma-no-cover stub, and
`fastapi/dependencies/utils.py`'s excerpts omitted the override-lookup
logic the task is actually about. An agent given `--json` would see the
right filenames and the wrong code. `answer_key["anchors"]` (a required
substring per file) and the `answer_key_excerpt_*` fields on run_case's
result close that gap; these tests cover the mechanism in miniature.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_DOGFOOD_PATH = Path(__file__).parent.parent / "benchmarks" / "dogfood.py"
_SPEC = importlib.util.spec_from_file_location("contextbench_dogfood_excerpt", _DOGFOOD_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_DOGFOOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_DOGFOOD)

run_case = _DOGFOOD.run_case
load_answer_keys = _DOGFOOD.load_answer_keys
AnswerKeyError = _DOGFOOD.AnswerKeyError


def _write(root: Path, rel_path: str, content: str) -> None:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


TASK = "dependency override not applied"


FIXTURE_PATH = "app/dependency_override.py"


def _fixture(root: Path) -> None:
    # A large-enough function that the relevant behavior sits far from
    # the top of the file, so the excerpt extractor has to choose what to
    # quote -- same shape as the real fastapi case, where the file is
    # recovered but a *different* part of it gets excerpted.
    padding = "\n".join(f"# padding line {i}" for i in range(80))
    _write(
        root,
        FIXTURE_PATH,
        f'"""Dependency override handling."""\n{padding}\n\n'
        "def unrelated_stub():\n    pass  # pragma: no cover\n\n"
        f"{padding}\n\n"
        "def include_router(router, dependency_overrides_provider=None):\n"
        "    router.dependency_overrides_provider = dependency_overrides_provider\n",
    )
    _write(
        root,
        "tests/test_overrides.py",
        "def test_dependency_override_nested():\n    assert True\n",
    )


def test_excerpt_hit_when_anchor_text_is_delivered(tmp_path):
    _fixture(tmp_path)
    answer_key = {
        "task": TASK,
        "files": {FIXTURE_PATH: "r"},
        "anchors": {FIXTURE_PATH: ["dependency_overrides_provider"]},
    }

    result = run_case(str(tmp_path), TASK, check_determinism=False, answer_key=answer_key)

    assert result["answer_key_found"] == 1
    assert result["answer_key_excerpt_total"] == 1
    assert result["answer_key_excerpt_found"] == 1
    assert result["answer_key_excerpt_missing"] == []


def test_excerpt_miss_when_anchor_text_is_not_in_the_delivered_excerpt(tmp_path):
    _fixture(tmp_path)
    # `unrelated_stub` is real code in this file, but the extractor's
    # chosen excerpt only covers the docstring and `include_router` --
    # the same "right file, wrong excerpt" shape as the real fastapi
    # case. File recall must still count the file; excerpt-hit recall
    # must not.
    answer_key = {
        "task": TASK,
        "files": {FIXTURE_PATH: "r"},
        "anchors": {FIXTURE_PATH: ["unrelated_stub"]},
    }

    result = run_case(str(tmp_path), TASK, check_determinism=False, answer_key=answer_key)

    assert result["answer_key_found"] == 1
    assert result["answer_key_excerpt_total"] == 1
    assert result["answer_key_excerpt_found"] == 0
    assert result["answer_key_excerpt_missing"] == [FIXTURE_PATH]


def test_file_not_found_is_also_an_excerpt_miss(tmp_path):
    _fixture(tmp_path)
    answer_key = {
        "task": TASK,
        "files": {FIXTURE_PATH: "r", "app/does_not_exist.py": "r"},
        "anchors": {"app/does_not_exist.py": ["anything"]},
    }

    result = run_case(str(tmp_path), TASK, check_determinism=False, answer_key=answer_key)

    assert "app/does_not_exist.py" in result["answer_key_missing"]
    assert "app/does_not_exist.py" in result["answer_key_excerpt_missing"]


def test_file_with_no_anchors_entry_is_unaffected(tmp_path):
    _fixture(tmp_path)
    answer_key = {"task": TASK, "files": {FIXTURE_PATH: "r"}}

    result = run_case(str(tmp_path), TASK, check_determinism=False, answer_key=answer_key)

    assert result["answer_key_excerpt_total"] == 0
    assert result["answer_key_excerpt_found"] == 0
    assert result["answer_key_excerpt_missing"] == []


# --- schema validation ---------------------------------------------------


def test_anchors_referencing_a_path_outside_files_is_rejected(tmp_path):
    keys_path = tmp_path / "keys.json"
    keys_path.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "repo": "a",
                        "task": "t",
                        "files": {"x.py": "r"},
                        "anchors": {"y.py": ["symbol"]},
                    }
                ]
            }
        )
    )
    with pytest.raises(AnswerKeyError, match="not in 'files'"):
        load_answer_keys(keys_path)


def test_anchors_with_empty_substring_list_is_rejected(tmp_path):
    keys_path = tmp_path / "keys.json"
    keys_path.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "repo": "a",
                        "task": "t",
                        "files": {"x.py": "r"},
                        "anchors": {"x.py": []},
                    }
                ]
            }
        )
    )
    with pytest.raises(AnswerKeyError):
        load_answer_keys(keys_path)


def test_well_formed_anchors_load_normally(tmp_path):
    keys_path = tmp_path / "keys.json"
    keys_path.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "repo": "a",
                        "task": "t",
                        "files": {"x.py": "r"},
                        "anchors": {"x.py": ["some_symbol"]},
                    }
                ]
            }
        )
    )
    keys = load_answer_keys(keys_path)
    assert keys[("a", "t")]["anchors"] == {"x.py": ["some_symbol"]}
