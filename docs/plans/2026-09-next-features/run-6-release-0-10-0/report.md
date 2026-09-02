# Run 6 report: release 0.10.0 (docs, adversarial review, live checks, publish)

Branch `next-features`, shared checkout. Source: [project-plan.md](./project-plan.md); contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Tasks

- **T-50: done** — docs and version bump. `README.md` (Dictation section, `status`), `docs/dictation.md` (new), `docs/installation.md` (dictation layer), `CHANGELOG.md` (0.10.0 entry), `vocalize/__init__.py` (`__version__ = "0.10.0"`), all in commit `60153e6`. Acceptance criterion — every documented command exists in `--help` — is green (validate-exit.sh, below).
- **T-51: done** — adversarial review. [review-0.10.0.md](../review-0.10.0.md): three lenses (security, correctness, operations, docs) over the full `git diff main...HEAD`, every finding checked by two refuters. 34 findings confirmed, 1 refuted; 31 confirmed findings closed across eight commits (`5ab7dfd`, `3754457`, `5d9ba0e`, `3e9431d`, `94e5a46`, `4513a20`, `eeb0caf`, `745ae64`); DEC-013 and DEC-014 recorded. Two critical findings remain **open** — both are the release process itself, not code, and both route to T-52/T-53 below.
- **T-52: partial** — live verification with the owner present. Not machine-checkable; owner-present manual checks 1, 2, 3, 4, 4b and 5 remain: microphone permission granted to "Vocalize Recorder" from the real Service, a real-voice hotkey dictation from TextEdit, cancel/refuse behavior, playback interaction and resume, the configured input device, and real-voice transcription accuracy against the spike's jargon paragraph. None of it can be run by an agent — it needs a real microphone, a real permission dialog, and a real voice. The no-microphone parts of this phase are already done and are cited here rather than repeated:
  - `--wav` transcription (no mic): [run-4-dictation/report.md](../run-4-dictation/report.md) T-41, verification.md Phase 4 "Transcribe an existing WAV end to end".
  - `status` (no mic): [run-1-status/report.md](../run-1-status/report.md) (the module and its exit-code contract) plus [run-4-dictation/report.md](../run-4-dictation/report.md) T-45 (the four STT readiness rows), further hardened this run (mic-verdict age, locked probe registry — see review-0.10.0.md).
  - Resume drill (no mic): [run-5-resume/report.md](../run-5-resume/report.md) T-46/T-47, with this run's review closing the gaps it left open (DEC-013's queued-read case, DEC-014f's voice/speed/chunking carry-over).
  - Note: rebuilding the recorder for the Swift permission-dialog fix (`94e5a46`) reset the microphone grant on the reference Mac to `notDetermined`, so Manual check 1 starts from a genuinely fresh state — not a regression, the state the fix exists for.
- **T-53: pending** — merge and publish. Not started. This run does not merge to main or publish; per project-plan.md that happens on the owner's word, after T-52. The orchestrator handles this after reading this report.

## Security-gate summary (every run's verdict)

| Run | Verdict |
|---|---|
| run-1-status | SECURITY: PASS |
| run-2-stt-runtime | SECURITY: PASS |
| run-3-recorder | SECURITY: PASS — no Critical or High finding open |
| run-4-dictation | SECURITY: PASS — no Critical or High finding open (16 findings from its own review round, all fixed) |
| run-5-resume | SECURITY: PASS — no Critical or High finding open; one residual accepted and documented (DEC-012e: `interrupted.txt` is new plaintext of the unread remainder, 0600, for at most an hour) |
| run-6 (this review, T-51) | **Not a clean pass.** Every Critical/High finding against the *code* is fixed (see review-0.10.0.md). Two Critical findings are open, and both are about the release *process*, not the code: T-52 (owner-present verification) and T-53 (publish) have not happened. `validate-exit.sh`'s `no open critical/high finding` check is red because of exactly these two rows — correctly. |

## Deferred / not fixed

- **No stale-tmpdir sweep for `vocalize-play-*`, `vocalize-resume-*` and `chain.py`'s TTS temp directories** (review-0.10.0.md, low, open). Deliberately deferred: the dictation sweep exists because those directories hold recorded audio (DEC-007 promises deletion on every exit path); these hold synthesized speech already cached under `~/.cache/vocalize` by design, so a leak costs disk only, and `chain.py`'s prefix is too broad to sweep safely from another process. Tracked for 0.11.0.
- **A SIGTERM'd recorder leaves an unfinalized WAV** (run-4, pre-existing, out of scope): the recorder's signal handler releases the microphone without writing a WAV header, so a recorder killed by the stop timeout reports "Dictation failed" rather than saving a partial take.
- **A live transcription's take survives `listen --cancel`** (run-4, DEC-011c, deliberate): cancel releases the session so the hotkey works again, but the running transcription keeps its own directory and still copies what it transcribed, rather than losing the dictation to free a directory a worker is using.
- **A fresh interrupt marker naming another player is left where it is** (run-5, DEC-012b, deliberate narrowing): only a *stale* marker is swept regardless of the PID it names; a fresh one belongs to another live read and consuming it would break that read's own resume.
- **T-52/T-53** (this run): see Tasks above — owner-present verification and publish, not startable by an agent.

## validate-exit.sh, real run

Run before this commit (the two files this report and the review record are landing in are not yet committed at the moment of this run — see "work committed" below).

```
=== Entry criteria ===
PASS: on branch next-features
PASS: run 5 validated
PASS: resume seam present
PASS: suite green at entry

=== Exit criteria ===
PASS: review findings file exists
FAIL: no open critical/high finding (exit 1)
PASS: every documented command exists
PASS: CHANGELOG has 0.10.0
PASS: version bumped to 0.10.0
PASS: dictation doc exists
PASS: wheel builds and pulls no STT runtime
PASS: full suite green
PASS: ruff clean
FAIL: work committed (exit 1)
FAIL: PyPI 0.10.0 published with matching digest (after the owner publishes) (exit 1)

=== Summary ===
Passed: 12 / 15
Failed: 3 / 15
SOME CHECKS FAILED
```

Three failures, and what each one means:

1. **`no open critical/high finding`** — real and expected. The two open critical findings in review-0.10.0.md are the T-52/T-53 gap itself; this check will not go green until an owner-present session closes them.
2. **`work committed`** — an artifact of ordering, not a defect: this report and the review record are written, then committed in the same commit right after this run. It reads clean on the next run.
3. **`PyPI 0.10.0 published with matching digest`** — expected to fail until the orchestrator publishes; that is T-53, explicitly out of this run's scope.

validate-exit: FAIL — no open critical/high finding, work committed, PyPI 0.10.0 published with matching digest
