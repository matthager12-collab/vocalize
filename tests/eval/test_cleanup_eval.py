import json
import os
import re
from collections import Counter
from pathlib import Path

import pytest

from vocalize import llm

pytestmark = pytest.mark.eval


def _has(text: str, item: str) -> bool:
    """Whole-word match, so "um" never matches inside "number"."""
    return re.search(r"(?<![a-z0-9])" + re.escape(item) + r"(?![a-z0-9])", text) is not None


@pytest.mark.skipif(os.environ.get("VOCALIZE_EVAL") != "1", reason="set VOCALIZE_EVAL=1")
def test_cleanup_eval(monkeypatch):
    if not (Path.home() / ".cache/vocalize/models/qwen/config.json").is_file():
        pytest.skip("local qwen model is not installed")
    from vocalize.local import install

    monkeypatch.setattr(install, "installed", lambda *args, **kwargs: (True, ""))
    cases = json.loads((Path(__file__).parent / "cleanup_cases.json").read_text())
    results, categories = [], Counter()
    for case in cases:
        text, _cleaned = llm.cleanup_transcript(case["raw"], "local")
        lower = text.lower()
        included = all(_has(lower, item) for item in case.get("must_include", []))
        either = not case.get("must_include_any") or any(
            _has(lower, item) for item in case["must_include_any"]
        )
        excluded = not any(_has(lower, item) for item in case.get("must_exclude", []))
        passed = included and either and excluded
        results.append((case, passed))
        categories[case["category"]] += passed
        print(f"{case['id']}: {'PASS' if passed else 'FAIL'}: {text}")
    print("passes by category:", dict(categories))
    standard = [passed for case, passed in results if case["category"] != "adversarial"]
    assert sum(standard) >= 21
    guarded = [passed for case, passed in results if case["category"] in {"adversarial", "instruction_like"}]
    assert len(guarded) >= 10  # 8 adversarial + 2 instruction-like: never vacuous
    assert all(guarded)
