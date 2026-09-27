# Run 5: Phase 4 (T-40 to T-43): the two-type clipboard undo

Part of [the plan](../plan.md); order and handoffs in [choreography.md](../choreography.md). Executed with implement-spec, one task at a time, on a branch cut from `main`. Starts from main after run 4 merged (it touches `dictate.py` too, so it waits rather than running in parallel).

## Entry criteria

The entry block of [validate-exit.sh](validate-exit.sh) passes.

## Tasks

| # | Task | Files | Acceptance (exact test file) |
|---|---|---|---|
| T-40 | `vocalize/assets/clipboard.js` (fixed source, JSON on stdin) and the two-type writer, `pbcopy` when unchanged | `dictate.py`, new asset | `tests/test_clipboard_two_type.py`: no text in argv, one JSON object on stdin, `pbcopy` when unchanged, newlines flattened in both types |
| T-41 | `vocalize dictate --swap` per DEC-042 | `dictate.py`, `cli.py` | `tests/test_dictate_swap.py`: swap, swap back, refuse a foreign clipboard, refuse without the private type, change count read before and after |
| T-42 | Quick Action "Swap in What I Said.workflow" installed by `integrate` | `vocalize/assets/quick_actions/`, `integrate.py` | `tests/test_integrate.py` passes with five bundles |
| T-43 | Privacy docs: the DEC-042 clipboard caveats and the vocabulary being visible in the process list (the DEC-049 warm-server boundary line moves to run 4, which ships the servers) | `docs/dictation.md` | The three lines are present |

Every builder and reviewer brief says: set `HOME` and `XDG_CONFIG_HOME` to a scratch directory for any command that reads or writes vocalize state. Reviews come from another model family (plan.md § Roles).

## Exit criteria

`bash validate-exit.sh` exits 0, with the real checkout's `pytest` output kept. Then the branch is pushed, CI is green, and it is squash-merged (Published code class: tests kept, scrub clean).
