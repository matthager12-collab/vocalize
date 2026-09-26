# Review round 2: the finished plan, two lenses

Plan-project step 7, 2026-09-26. Two independent reviewers from other model families read the documents and the source; neither wrote them.

- **Security and privacy:** Codex, gpt-6-astra, high effort. Raw answer in the run folder `~/codex-runs/vocalize-plan-review-security/answer.md` (not in the repo).
- **Performance, lifecycle and operations:** Gemini 3.1 Pro, high. Raw answer in `~/gemini-runs/vocalize-plan-review-perf/answer.md`.

Claude checked each finding against the files before folding it in. Four claims were verified in code:

- `llm.py` echoes the API's `stop_reason` to stderr, around line 312.
- pywhispercpp's `transcribe` accepts a numpy array (model.py, `media: Union[str, np.ndarray]`).
- `no_context` defaults to True in pywhispercpp 1.5.1 (constants.py:94).
- mlx-lm 0.31.3 has `stream_generate` (generate.py:657).

## Triage

| # | Finding (severity) | Verdict | Lands in |
|---|---|---|---|
| G1 | A take shorter than the load falls back while the server keeps loading: two copies of the model at once (High) | Accepted. On `loading` the client waits up to its deadline. Before any fallback it sends `shutdown`. The timing eval adds a take shorter than the load | DEC-044, T-33, T-36 |
| G2 | Spawning and the lease handshake inside `_start` delay the microphone (High) | Accepted. Warming runs after the recorder is launched and never waits. A new server is spawned with `--lease <id>`. A lease on a live server gets 200 ms, then is skipped | DEC-044, T-34, design § Key flows |
| G3 | `warm_minutes = 0` exits at release, so default users never catch a back-to-back take (Medium) | Accepted. 0 means exit 10 s after the last release | DEC-043, DEC-044 |
| G4 | A hung generate cannot read `cancel`, so the server never exits (Medium) | Accepted. The LLM server uses `stream_generate` and checks its deadline and a cancel flag between tokens. Both servers run a watchdog thread that calls `os._exit` when a request passes its deadline plus 5 s | DEC-044, T-31, T-32 |
| C1 | A same-uid process can impersonate the socket or send `shutdown` (Medium) | Partly accepted. The trust boundary is written down in DEC-049: same-uid processes are out of scope, because they can already read the clipboard where every transcript lands. `cancel` is bound to the request id | DEC-049, T-30 |
| C2 | The raw take on the clipboard is readable by other clipboard readers and may be kept by a clipboard manager; the "nothing else holds it" wording overclaims (Medium) | Accepted for the wording. The raw-undo off switch was rejected: cleanup is opt-in, and with cleanup off the same raw text sits on the clipboard as plain text today, so the private type exposes nothing new. The docs also name the existing `claude-cli` history exception | DEC-042, T-43, design § Undo |
| C3 | No server read deadline, token ceiling, reply cap or lease cap (Medium) | Accepted. 2 s to receive a full request line, `max_tokens` at most 1,024, replies capped at 64 KB, at most 8 leases, one connection at a time | DEC-044, T-30 to T-33 |
| C4 | The whisper server would read any path, including FIFOs and swapped files (Medium) | Accepted. The server opens the path once with `O_NOFOLLOW` and `O_NONBLOCK`, requires a regular file under 200 MB, validates the format on that handle, reads the frames into an array, and transcribes the array. The client only sends a take from a vocalize workdir, or a path the user named to `listen --wav` | DEC-044, T-31 |
| C5 | The added-word guard passes instruction-driven deletions and reordering (Medium) | Accepted. The guard also keeps every negation ("not", "never", any "n't" word) that the raw take had, unless a correction marker is present. Adversarial cases go into the eval set with a zero-pass gate. The docs call it an addition check, not injection prevention | DEC-041, T-11, T-12, verification § Phase 1 |
| C6 | A vocabulary entry can hold a sentence or an instruction (Medium) | Accepted. Each entry is 1 to 4 words of letters, digits, spaces and `_ . - + / @ # :`. No `<` or `|`, and no sentence punctuation at the end | DEC-043, T-20 |
| C7 | A warm runtime could carry one take into the next (Medium) | Accepted. `no_context=True` and `initial_prompt` are passed on every call. The LLM keeps no prompt cache. A canary eval runs take A, then quiet take B | T-31, T-32, T-36 |
| C8 | Another app can forge the two-type pair, so the swap check proves nothing (Medium) | Accepted as a documented limit, not a guarantee. The swap re-sanitises both types and reads the change count before and after. Authentication was rejected: a forged pair only swaps text another app already put on the clipboard | DEC-042, T-41 |
| C9 | A cancel can land between the nonce check and the clipboard write (Medium) | Accepted. Delivery holds an exclusive `flock` on the session file across check and write. `cancel` takes the same lock before releasing | DEC-045, T-02 |
| C10 | The API's `stop_reason` is echoed to stderr unchecked (Low) | Accepted. Allowlist `max_tokens`, `stop_sequence`, `refusal` and `pause_turn`; anything else prints "unexpected" | T-11 |

## Re-read after folding

The fixes tighten contracts inside DEC-044, and add DEC-049. They do not change the architecture: warm servers, one-shot fallback, and the two-type undo all stand. Per step 7.5, no second review round was run. The implement-spec runs review each phase's diff with a reviewer from another family (plan.md § Roles).
