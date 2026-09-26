# Run 2: Phase 2 (T-20 to T-22): hear the jargon

Part of [the plan](../plan.md); order and handoffs in [choreography.md](../choreography.md). Executed with implement-spec, one task at a time, on a branch cut from `main`. Starts from main after run 1 merged.

## Entry criteria

The entry block of [validate-exit.sh](validate-exit.sh) passes.

## Tasks

| # | Task | Files | Acceptance (exact test file) |
|---|---|---|---|
| T-20 | `[stt] vocabulary` in config, validation with the DEC-043 grammar, and the portal form | `config.py`, `portal.py` | `tests/test_stt_vocabulary.py` rejects a non-list, 51 items, 41 characters, five words, a newline, `<|im_end|>`, a trailing ".", and an 800+ character prompt; the portal round-trips a list |
| T-21 | `whisper_worker.py --initial-prompt`, passed with `no_context=True` on every call; `worker_argv` builds it (DEC-040) | `whisper_worker.py`, `dictate.py` | `tests/test_whisper_initial_prompt.py`: the stub Model sees `initial_prompt` and `no_context=True` on every call, `""` when empty |
| T-22 | Leak eval `tests/eval/test_whisper_leak.py` | `tests/eval/` | With `VOCALIZE_EVAL=1`: 0 unspoken vocabulary words over 50 quiet or short runs |

Every builder and reviewer brief says: set `HOME` and `XDG_CONFIG_HOME` to a scratch directory for any command that reads or writes vocalize state. Reviews come from another model family (plan.md § Roles).

## Exit criteria

`bash validate-exit.sh` exits 0, with the real checkout's `pytest` output kept. Then the branch is pushed, CI is green, and it is squash-merged (Published code class: tests kept, scrub clean).
