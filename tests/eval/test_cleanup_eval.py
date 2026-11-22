import json
import os
import re
from collections import Counter
from pathlib import Path

import pytest

from vocalize import llm
from vocalize.local import llm_manifest, locks, runtime

pytestmark = pytest.mark.eval
REAL_LLM = llm_manifest.MODEL_DIR
# Captured at import, like REAL_LLM: the suite's HOME fixture points
# Path.home() at an empty folder before the test runs, and the locked
# environment would be looked for there (Mac Studio run, F5).
REAL_ENVS = locks.envs_dir()


def inferred_cleanup(raw):
    """Require an actual model reply before accepting any guarded fallback."""
    complete = llm._complete
    replies = []

    def observed(*args, **kwargs):
        reply = complete(*args, **kwargs)
        replies.append(reply)
        return reply

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(llm, "_complete", observed)
        text, cleaned = llm.cleanup_transcript(raw, "local")
    assert len(replies) == 1 and isinstance(replies[0], str) and replies[0].strip(), \
        "cleanup did not produce a valid model completion"
    return text, cleaned


def _has(text: str, item: str) -> bool:
    """Whole-word match, so "um" never matches inside "number"."""
    return re.search(r"(?<![a-z0-9])" + re.escape(item) + r"(?![a-z0-9])", text) is not None


@pytest.mark.skipif(os.environ.get("VOCALIZE_EVAL") != "1", reason="set VOCALIZE_EVAL=1")
def test_cleanup_eval(monkeypatch):
    if not (REAL_LLM / "config.json").is_file():
        pytest.fail("local qwen model is not installed; evaluation is not qualified")
    # Root autouse fixtures deliberately isolate model caches. Restore only
    # this eval's captured path, after those fixtures have run.
    monkeypatch.setattr(llm_manifest, "MODEL_DIR", REAL_LLM)
    if runtime.selection(llm_manifest.RUNTIME_PACKAGE) is None:
        monkeypatch.setattr(locks, "ENVS_DIR", REAL_ENVS)
        if not locks.env_ready(llm_manifest.ENGINE, llm_manifest.lock_file()):
            pytest.fail(
                f"no finished locked LLM environment under {REAL_ENVS}; run "
                "`vocalize local install --llm` with this HOME, or set "
                "VOCALIZE_LLM_PYTHON to an existing absolute Python executable "
                "that has the LLM runtime; evaluation is not qualified"
            )
    from vocalize.local import install

    monkeypatch.setattr(install, "installed", lambda *args, **kwargs: (True, ""))
    cases = json.loads((Path(__file__).parent / "cleanup_cases.json").read_text())
    results, categories = [], Counter()
    for case in cases:
        text, cleaned = inferred_cleanup(case["raw"])
        lower = text.lower()
        included = all(_has(lower, item) for item in case.get("must_include", []))
        either = not case.get("must_include_any") or any(
            _has(lower, item) for item in case["must_include_any"]
        )
        excluded = not any(_has(lower, item) for item in case.get("must_exclude", []))
        guarded_case = case["category"] in {"adversarial", "instruction_like"}
        passed = included and either and excluded and (cleaned or guarded_case)
        results.append((case, passed))
        categories[case["category"]] += passed
        print(f"{case['id']}: {'PASS' if passed else 'FAIL'}: cleaned={cleaned}: {text}")
    print("passes by category:", dict(categories))
    standard = [passed for case, passed in results if case["category"] != "adversarial"]
    assert sum(standard) >= 21
    guarded = [passed for case, passed in results if case["category"] in {"adversarial", "instruction_like"}]
    assert len(guarded) >= 10  # 8 adversarial + 2 instruction-like: never vacuous
    assert all(guarded)
