# Verification: intended mode for dictation

"Passing" means exit 0 and zero `FAILED` lines, read in the real checkout, never a builder's own summary. Eval gates need the local models and run only on the reference Mac (Mac mini, M4, 16 GB).

## Commands

| Purpose | Command |
|---|---|
| Install for tests | `pip install -e ".[dev,dotenv]"` (as CI does) |
| Lint | `ruff check .` |
| Unit tests | `pytest -q -p no:cacheprovider 2>&1 \| tee /tmp/pytest.out; echo "exit=$?"; grep -c FAILED /tmp/pytest.out` |
| Eval tests (reference Mac only) | `VOCALIZE_EVAL=1 pytest -q -m eval tests/eval` |
| Plan lint | `bash ~/.claude/skills/running-fix/skills/plan-project/scripts/lint-plan.sh docs/plans/2026-09-intended-mode` |

The `eval` marker is registered in `[tool.pytest.ini_options] markers` by T-12. Without `VOCALIZE_EVAL=1`, every eval test skips, so CI never needs the models.

## Phase 0 exit

| Criterion | How it is proven | Passing when |
|---|---|---|
| Control-token fix present | `grep -n "tokenizer.decode(prompt_ids)" vocalize/local/llm_worker.py` | no match (exit 1) |
| Ids reach `generate` | `pytest -q tests -k "generate_receives_ids or im_end"` | exit 0, at least 1 passed |
| Cancelled take never delivers | `pytest -q tests -k "cancel_before_delivery"` | exit 0, at least 1 passed |
| Nothing else broke | Unit tests | exit 0, 0 FAILED |

## Phase 1 exit

| Criterion | How it is proven | Passing when |
|---|---|---|
| Prompt pinned | `pytest -q tests -k "cleanup_prompt"` | exit 0 |
| Guard proven on recorded outputs | `pytest -q tests -k "faithful"` | exit 0 |
| Cleanup quality, real model | `VOCALIZE_EVAL=1 pytest -q -m eval tests/eval/test_cleanup_eval.py` | at least 21 of the 26 S2 cases pass; 0 obeyed instructions; 0 answered questions; 0 added-content outputs and 0 adversarial meaning changes that the guard lets through |
| Nothing else broke | Unit tests and lint | exit 0, 0 FAILED |

## Phase 2 exit

| Criterion | How it is proven | Passing when |
|---|---|---|
| Settings validated | `pytest -q tests -k "vocabulary"` | exit 0 |
| Prompt passed on every call | `pytest -q tests/test_whisper_worker.py -k "initial_prompt"` | exit 0 |
| No leak, real model | `VOCALIZE_EVAL=1 pytest -q -m eval tests/eval/test_whisper_leak.py` | exit 0: no vocabulary word in any output where it was not spoken, over 50 runs |
| Nothing else broke | Unit tests and lint | exit 0, 0 FAILED |

## Phase 3 exit

| Criterion | How it is proven | Passing when |
|---|---|---|
| Protocol bounds | `pytest -q tests -k "warm_protocol"` | exit 0 |
| Server lifecycle | `pytest -q tests -k "serve and (lease or idle or cancel or shutdown)"` | exit 0 |
| Client races and fallback | `pytest -q tests -k "warm_client"` | exit 0 |
| Wiring | `pytest -q tests -k "warm and (dictate or listen or notes)"` | exit 0 |
| Speed and memory, real models | `VOCALIZE_EVAL=1 pytest -q -m eval tests/eval/test_warm_timing.py` | the thresholds in DEC-047 hold, including the 1 s take; footprint never shows two LLM processes at once; the canary from take A never appears in take B |
| No orphans | after the eval run: `pgrep -fl -- "--serve"` | no output once the eval's `warm_minutes` has passed |
| Nothing else broke | Unit tests and lint | exit 0, 0 FAILED |

## Phase 4 exit

| Criterion | How it is proven | Passing when |
|---|---|---|
| Writer contract | `pytest -q tests -k "clipboard_js or two_type"` | exit 0 |
| Swap rules | `pytest -q tests -k "swap"` | exit 0 |
| Quick Action installed | `pytest -q tests/test_integrate.py` | exit 0 |
| Privacy docs | `grep -n "io.github.vocalize-cli.said" docs/dictation.md && grep -n "visible in" docs/dictation.md` | both match |
| Nothing else broke | Unit tests and lint | exit 0, 0 FAILED |

## Phase 5 exit

| Criterion | How it is proven | Passing when |
|---|---|---|
| CHANGELOG names every change | `grep -n "warm_minutes\|vocabulary\|Swap in What I Said" CHANGELOG.md` | three matches under Unreleased |
| Whole suite | Unit tests and lint | exit 0, 0 FAILED |
| Every eval gate, on one commit | `VOCALIZE_EVAL=1 pytest -q -m eval tests/eval` | exit 0 |
| Plan lint | Plan lint | exit 0 |

## Manual checks

Performed by Mat on his Mac after he applies the T-51 config block:

1. Dictate the S1 jargon paragraph through the hotkey. Check the 12 words.
2. Dictate "Um, so move the standup to, uh, Tuesday, no, Wednesday at nine thirty." Check that you get "Move the standup to Wednesday at 9:30." or equivalent.
3. Run the "Swap in What I Said" Quick Action, press Command-Z, then Command-V. Check the raw words appear. Swap back.
4. Dictate twice within a minute. Check the second is noticeably faster to land.
5. Cancel a take while it is transcribing, copy something else, and wait 15 s. Check the clipboard still holds what you copied.
