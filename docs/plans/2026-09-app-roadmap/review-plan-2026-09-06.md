# Plan review: local-first defaults, the menu-bar app, and recorded notes

Date 2026-09-06. Three independent reviewers who did not write the documents, given the four documents, the repo and the binding constraints: security with untrusted-input tracing (Opus-class), operational burden and upgrade rot (Sonnet), performance, memory and cost (Sonnet). 27 findings. Every accepted finding is folded into the documents named; every rejected one carries its reason. R-numbers are the review's own order.

| # | Severity | Lens | Finding | Status | Applied to |
|---|---|---|---|---|---|
| R1 | high | security | The `VocalizeBinary` override is spawned unchecked by the Accessibility-holding app | Accepted | design § App spawn contract; T-70, T-72 |
| R2 | medium | security | Notes carry untrusted transcript text into a corpus later recalled by models; the audio path never ran `sanitize` | Accepted | design § Notes step 2, § Note file (`trust` key); T-141, T-145; DEC-029 |
| R3 | medium | security | `claude -p` still loads user-scope hooks, skills and CLAUDE.md | Accepted | design § Cleanup; T-20; DEC-024 |
| R4 | medium | security | The key test route skips `_check_shape` and `scrub` | Accepted | design § Key slots; T-41, T-43 |
| R5 | medium | security | A planted note with a matching hash silently suppresses a real recording | Accepted | design § Notes step 1; T-141 |
| R6 | low | security | A forged `dictate.copied` turns the app into a paste oracle | Accepted (nonce) | design § Auto-paste, § Files; T-70, T-71, T-103 |
| R7 | low | security | A custom template is an unbounded, followable system prompt | Accepted | design § Note file; T-142 |
| R8 | high | operational | The app bundle lived in the folder the README calls safe to delete | Accepted (moved to Application Support) | design § LaunchAgent and bundle identity; T-72, T-75; DEC-028 |
| R9 | high | operational | First-time note writes were not crash-atomic | Accepted | design § Notes step 4; T-141 |
| R10 | high | operational | Build-time hotkey backend with no runtime fallback | Accepted as a documented one-way door; the self-firing test rejected (more code than the failure warrants; registration failure is already reported, a swallowed chord is caught by manual check 5) | DEC-033; design § LaunchAgent; plan § Decisions |
| R11 | medium | operational | `dictate_mode = "hold"` refused on 0.13.0 bricks a rollback | Accepted | design § `[app]`; T-62 |
| R12 | medium | operational | Parsing `launchctl print` free text | Accepted (`launchctl list`, parse failure = unknown) | design § LaunchAgent; T-72 |
| R13 | medium | operational | Any bundle or stamp loss is a double re-grant, undocumented | Accepted | design § LaunchAgent; T-75 |
| R14 | medium | operational | No hotfix path for a Swift defect after 0.13.0 | Accepted | DEC-032 |
| R15 | low | operational | `app.log` grows unbounded between launches | Accepted | design § App spawn contract; T-70 |
| R16 | low | operational | Phase 8 at ~30 h contradicts the small-chunk goal | Accepted (Phase 8a Swift, 8b Python) | plan § Phase 8a, 8b, § Suggested run boundaries; verification § 8a, 8b |
| R17 | low | operational | Anthropic budget is per Mac | Accepted (documented) | T-44; DEC-024 |
| R18 | high | memory-cost | Beam search default with no timing and no escape hatch | Accepted (`[stt] beam_size`, timings recorded) | T-10; verification § Phase 2 |
| R19 | high | memory-cost | T-121 measured warm latency, not the cold one-shot a press pays | Accepted | T-121; design § Local language-model worker |
| R20 | high | memory-cost | Bare `--cleanup` falling back to `claude-cli` is silent egress on the subscription pool | Partly accepted: the pool is documented (DEC-024, docs); the flag keeps its 0.10 meaning because the user passed it and the egress line prints | plan § Decisions; DEC-024 |
| R21 | high | memory-cost | No default budget and no dollar figure | Partly accepted: a 2,000,000-character default and a docs pointer; the dollar figure rejected because a price constant rots | T-27, T-44; design § Key slots |
| R22 | medium | memory-cost | `--force` past the RAM gate has no runtime safety net | Rejected: a failing one-shot worker is already "not usable" with the transcript kept, and a free-memory heuristic is unreliable under macOS compressed memory | plan § Decisions |
| R23 | medium | memory-cost | Concurrent Kokoro + whisper + Qwen memory never measured | Accepted | T-121, T-146; verification manual 13 |
| R24 | medium | memory-cost | Kokoro's documented RAM figure is stale | Accepted | T-12 |
| R25 | medium | memory-cost | No latency budget for the per-press Python spawn | Accepted (stopwatch numbers in manual 5; `cli start-up` doctor row) | verification manual 5; design § Readiness, doctor |
| R26 | low | memory-cost | Selftest online versus runtime `--offline` was implicit | Accepted | design § Local language-model worker; T-131 |
| R27 | low | memory-cost | `keep_audio` growth is invisible | Accepted (`notes folder` doctor row) | design § Readiness, doctor; T-80, T-145 |

## What the reviewers said the plan gets right

- The recorder stays frozen and every re-grant cause is named.
- Transcript never in argv on any backend; the egress line is printed only before a real send; the API key is stripped from the `claude -p` environment.
- The token-id prompt build closes the control-token hole; no downloaded template is evaluated.
- One run per phase with owner-gated releases and a spike before every unmeasured claim.
- The two-way doors are decided in one line each instead of opening rounds.
