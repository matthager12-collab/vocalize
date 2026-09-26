# Run 1: Phase 0 (T-02) and Phase 1 (T-10 to T-12): say what was meant

Part of [the plan](../plan.md); order and handoffs in [choreography.md](../choreography.md). Executed with implement-spec, one task at a time, on a branch cut from `main`. Phase 0 T-01 is done by branch `llm-worker-control-token-fix` before this run. This run does T-02 and Phase 1.

## Entry criteria

The entry block of [validate-exit.sh](validate-exit.sh) passes.

## Tasks

| # | Task | Files | Acceptance (exact test file) |
|---|---|---|---|
| T-02 | Delivery holds an exclusive `flock` on the session file across the nonce re-read and the clipboard write; `cancel` takes the same lock (DEC-045) | `dictate.py` | `tests/test_dictate_delivery_lock.py`: cancel before delivery means no pbcopy call, no copied marker, no success notification; a cancel during delivery waits. Fails on the old code |
| T-10 | `llm.CLEANUP_PROMPT` becomes the P2-revised text (DEC-039) | `llm.py` | `tests/test_cleanup_prompt.py` pins the data rule first, four worked examples, and `VERBATIM_PROMPT` unchanged |
| T-11 | `llm.faithful` with the negation rule, called in `cleanup_transcript` for every backend; `stop_reason` allowlist (DEC-041, review C10) | `llm.py` | `tests/test_faithful.py`: S2's recorded outputs give 0 unfaithful-and-PASS, the meta-commentary is caught, a dropped "not" is caught, spoken forms accepted, a canary `stop_reason` never reaches stderr |
| T-12 | Eval set `tests/eval/cleanup_cases.json` (26 S2 cases plus at least 8 adversarial) and `tests/eval/test_cleanup_eval.py`, marker `eval` registered in pyproject | `tests/eval/`, `pyproject.toml` | With `VOCALIZE_EVAL=1`: at least 21 of the 26 pass; 0 obeyed instructions, answered questions, or added-content or meaning changes past the guard |

Every builder and reviewer brief says: set `HOME` and `XDG_CONFIG_HOME` to a scratch directory for any command that reads or writes vocalize state. Reviews come from another model family (plan.md § Roles).

## Exit criteria

`bash validate-exit.sh` exits 0, with the real checkout's `pytest` output kept. Then the branch is pushed, CI is green, and it is squash-merged (Published code class: tests kept, scrub clean).
