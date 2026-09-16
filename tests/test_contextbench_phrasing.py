"""ContextBench phrasing variants share one canonical answer key."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_DOGFOOD_PATH = Path(__file__).parent.parent / "benchmarks" / "dogfood.py"
_SPEC = importlib.util.spec_from_file_location("contextbench_dogfood", _DOGFOOD_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_DOGFOOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_DOGFOOD)

iter_task_phrasings = _DOGFOOD.iter_task_phrasings
load_answer_keys = _DOGFOOD.load_answer_keys


def test_fastapi_phrasings_reuse_the_same_answer_key_object():
    answer_keys = load_answer_keys()
    canonical = "dependency override not applied in nested routers"

    cases = list(iter_task_phrasings("fastapi", [canonical], answer_keys, "all"))

    assert [label for _task, label, _key in cases] == ["existing", "technical", "natural"]
    assert len({id(key) for _task, _label, key in cases}) == 1
    assert cases[0][0] == canonical
    assert "stops working" in cases[2][0]


def test_existing_series_does_not_implicitly_run_new_phrasings():
    answer_keys = load_answer_keys()
    canonical = "dependency override not applied in nested routers"

    cases = list(iter_task_phrasings("fastapi", [canonical], answer_keys, "existing"))

    assert [(task, label) for task, label, _key in cases] == [(canonical, "existing")]
