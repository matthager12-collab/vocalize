# Run 3: Phase 3 part 1 (T-30 to T-33): the servers and the client, not yet wired in

Part of [the plan](../plan.md); order and handoffs in [choreography.md](../choreography.md). Executed with implement-spec, one task at a time, on a branch cut from `main`. Starts from main after run 2 merged. Nothing in `dictate.py` changes in this run.

## Entry criteria

The entry block of [validate-exit.sh](validate-exit.sh) passes.

## Tasks

| # | Task | Files | Acceptance (exact test file) |
|---|---|---|---|
| T-30 | `vocalize/local/warm_protocol.py`, stdlib only: framing, caps, read deadline, error codes, fingerprint, lease cap, peer-uid and socket-path checks (DEC-044, DEC-049) | new file | `tests/test_warm_protocol.py` covers oversize, slow sender, bad JSON, unknown op, ninth lease, unknown cancel id, symlinked path, path over 100 bytes, wrong owner |
| T-31 | `whisper_worker.py --serve` per DEC-044 (watchdog, WAV opened once and passed as an array) | `whisper_worker.py` | `tests/test_whisper_serve.py`: idle exit (10 s at 0), lease holds, abandoned lease expires, vanished client survived, FIFO, symlink and 201 MB refused, no request text on stderr, watchdog exit |
| T-32 | `llm_worker.py --serve`: ids to `stream_generate`, deadline and cancel between tokens, `max_tokens` at most 1,024 | `llm_worker.py` | `tests/test_llm_serve.py`: T-31's cases plus cancel mid-generate within one token |
| T-33 | `vocalize/local/warm.py` client: flock spawn, hello, respawn on mismatch, lease, release, request with one deadline; `shutdown` before fallback | new file | `tests/test_warm_client.py`: one spawn under a race, respawn on mismatch, `loading` waited on, every failure returns None within the deadline after `shutdown` |

Every builder and reviewer brief says: set `HOME` and `XDG_CONFIG_HOME` to a scratch directory for any command that reads or writes vocalize state. Reviews come from another model family (plan.md § Roles).

## Exit criteria

`bash validate-exit.sh` exits 0, with the real checkout's `pytest` output kept. Then the branch is pushed, CI is green, and it is squash-merged (Published code class: tests kept, scrub clean).
