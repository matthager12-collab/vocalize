# Run 4: Phase 3 part 2 (T-34 to T-36): wire warm servers into dictation and prove speed and memory

Part of [the plan](../plan.md); order and handoffs in [choreography.md](../choreography.md). Executed with implement-spec, one task at a time, on a branch cut from `main`. Starts from main after run 3 merged. `dictate.py` is over 1,000 lines, so T-34 gets its own builder.

## Entry criteria

The entry block of [validate-exit.sh](validate-exit.sh) passes.

## Tasks

| # | Task | Files | Acceptance (exact test file) |
|---|---|---|---|
| T-34 | Wire per DEC-046: warm after the recorder launches, never wait; `transcribe` and `llm._local` try warm first; every exit releases; notes never connect | `dictate.py`, `llm.py`, `cli.py` | `tests/test_dictate_warm.py`: recorder before warm, `_start` under 250 ms with a silent server, warm take never calls the one-shot argv, a warm failure calls it once, cancel, silence and failure release, notes never connects |
| T-35 | `[stt] warm_minutes` (0 to 240) in config, the portal and docs | `config.py`, `portal.py`, `docs/dictation.md` | `tests/test_stt_warm_minutes.py` rejects -1, 241 and a string; the portal round-trips |
| T-36 | Timing, memory and canary eval `tests/eval/test_warm_timing.py` | `tests/eval/` | With `VOCALIZE_EVAL=1`: DEC-047's thresholds including the 1 s take, never two LLM processes, no canary crossing takes, every server killed |

Every builder and reviewer brief says: set `HOME` and `XDG_CONFIG_HOME` to a scratch directory for any command that reads or writes vocalize state. Reviews come from another model family (plan.md § Roles).

## Exit criteria

`bash validate-exit.sh` exits 0, with the real checkout's `pytest` output kept. Then the branch is pushed, CI is green, and it is squash-merged (Published code class: tests kept, scrub clean).
