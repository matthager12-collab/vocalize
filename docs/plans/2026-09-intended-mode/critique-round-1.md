1. **Candidate: both · Severity: High — Memory compliance is unproved**
   - **Scenario:** Loading or generating exceeds the hard 1.5 GB worker cap; longer inputs and repeated requests may increase peaks.
   - **Why the documents miss it:** The combined-worker rejection relies on approximately 1.9 GB total, despite contradictory measurements.
   - **Mitigation:** Require separate worker measurements across loading, maximum inputs and repeated takes; block release unless each passes.
   - **Lands in:** Common to every candidate.
   - **Proof:** Stated: spike-notes.md:13 reports a 2.02 GB peak and explicitly rejects trusting the figures.

2. **Candidate: both · Severity: High — COVERAGE GAP: prompt isolation**
   - **Scenario:** Dictated control-token strings or “ignore previous instructions” influence cleanup. The supposedly safe token sequence is decoded into a string before generation.
   - **Why the documents miss it:** DEC-027’s claimed protection is assumed. The faithfulness guard is unspecified; keeping “Tuesday” incorrectly introduces no new word.
   - **Mitigation:** Pass token IDs through generation without re-tokenization; test injection, negation, corrections and truncation. Reject uncertain cleanup. Define S2’s guard and skip criteria.
   - **Lands in:** Common to every candidate.
   - **Proof:** Cited: `llm_worker.py:169`; wrong-day result stated at spike-notes.md:26. Exploitability reasoned, not demonstrated.

3. **Candidate: both · Severity: High — Cancel can still overwrite the clipboard**
   - **Scenario:** Cancel during cleanup, then copy something else. The old take finishes and overwrites it.
   - **Why the documents miss it:** A preserves existing behavior; C never defines cancellation of delivery. Existing cancel releases the session, but copying precedes the nonce check.
   - **Mitigation:** Serialize cancellation and delivery authorization using the take’s nonce; revoked takes must never copy, mark completion or notify success.
   - **Lands in:** New decision.
   - **Proof:** Cited: `dictate.py:1541` and `dictate.py:1509`; consequence reasoned.

4. **Candidate: C · Severity: High — Stop files cannot identify intent**
   - **Scenario:** Pause or cancel writes the same stop file the finisher interprets as “deliver.” A paused segment could be transcribed prematurely.
   - **Why the documents miss it:** C specifies only stop-file observation, omitting explicit intent and ownership.
   - **Mitigation:** Define separate pause, cancel and finish commands; preserve toggle debounce, two-second cancellation, hold-release semantics and finisher-owned claims. Verify asynchronous completion against the frozen app’s existing nonce/state contract.
   - **Lands in:** Candidate C.
   - **Proof:** Cited: `dictate.py:1710`, :1481, :1413, :484; interaction reasoned.

5. **Candidate: both · Severity: High — COVERAGE GAP: clipboard execution and retention**
   - **Scenario:** Quotes become JavaScript if interpolated into the osascript program; multiline raw text executes commands when swapped and pasted into an unprotected terminal.
   - **Why the documents miss it:** “Stdin” does not establish code/data separation. A private clipboard type is neither access control nor guaranteed deletion.
   - **Mitigation:** Use fixed script code with separately parsed JSON; sanitize and flatten both types on every write/swap. Check clipboard change counts, handle partial failure, and disclose clipboard-manager retention.
   - **Lands in:** Common / Undo and Privacy wording.
   - **Proof:** Reasoned; existing protection: `dictate.py:1123`.

6. **Candidate: both · Severity: High — COVERAGE GAP: persistent transport**
   - **Scenario:** Oversized/malformed requests exhaust memory; detached stderr exposes transcript-bearing exceptions. A client trusts a substituted socket; C’s inherited pipe descriptors prevent shutdown.
   - **Why the documents miss it:** Permissions or parentage do not define a complete protocol.
   - **Mitigation:** Specify bounded schemas, framing, request IDs, deadlines, descriptor closure, request-state clearing and fixed error codes. A additionally needs client-side peer validation and safe socket-path ownership checks.
   - **Lands in:** New decision.
   - **Proof:** Reasoned; unbounded request reading and exception-text clipping exist at `llm_worker.py:248` and :65.

7. **Candidate: A · Severity: Medium — Fallback can worsen the baseline**
   - **Scenario:** A slow server times out but continues generating while fallback loads another model. Concurrent clients can also race through ping-then-spawn.
   - **Why the documents miss it:** “Today’s behaviour is the floor” ignores waiting, duplicated work and memory contention.
   - **Mitigation:** Atomic startup ownership; distinguish loading, busy and dead; impose one total deadline and acknowledge cancellation before retrying.
   - **Lands in:** Candidate A.
   - **Proof:** Reasoned from candidates.md:25–26.

8. **Candidate: A · Severity: Medium — Idle is not the warm-window contract**
   - **Scenario:** A long recording outlasts the idle timer. Conversely, cancellation, silence or skipped cleanup produces no answer, leaving a zero-window worker without its specified exit trigger.
   - **Why the documents miss it:** Timers track requests, not takes; whether ping counts as an answer is unspecified.
   - **Mitigation:** Take leases spanning recording and pause; explicit release on every outcome; bounded abandoned-lease expiry.
   - **Lands in:** Candidate A.
   - **Proof:** Reasoned from candidates.md:23 and `dictate.py:1161`.

9. **Candidate: C · Severity: Medium — Adoption and crash recovery are undesigned**
   - **Scenario:** The finisher exits while the next press hands over a workdir; its crash leaves workers or a recorder alive and the take undelivered.
   - **Why the documents miss it:** “Finds it through the session directory” specifies neither transport nor acknowledgment. That take directory is normally deleted.
   - **Mitigation:** Separate owner discovery from take storage; atomic adoption acknowledgment, verified process identity, bounded recovery and child shutdown on owner loss.
   - **Lands in:** Candidate C.
   - **Proof:** Reasoned; deletion cited at `dictate.py:621`.

10. **Candidate: both · Severity: Medium — Upgrade identity is incomplete**
    - **Scenario:** Runtime pins, tokenizer files or settings change while workers remain warm. A’s filename/hash check misses some changes; C defines none.
    - **Why the documents miss it:** Worker-file identity is treated as complete compatibility.
    - **Mitigation:** Version protocol, runtime and model fingerprints; send validated per-take settings. Drain incompatible workers without killing active unrelated requests.
    - **Lands in:** Both candidate sections.
    - **Proof:** Reasoned from candidates.md:25, :35 and whisper_worker.py:201.

11. **Candidate: both · Severity: Medium — Speed claims exclude important paths**
    - **Scenario:** First press after login or crash, and takes shorter than loading, still wait. Back-to-back takes are warm only after readiness and before expiry.
    - **Why the documents miss it:** Approximately 3.2 seconds is an unmeasured overlap estimate. A’s ordinary listen path never calls `_start`; WAV input offers no recording interval.
    - **Mitigation:** Measure cold/short/warm paths and simultaneous-load contention; add explicit preload entry points.
    - **Lands in:** The contested part.
    - **Proof:** Stated: spike-notes.md:30–38; cited: `dictate.py:1787`.

12. **Candidate: both · Severity: Medium — Defaults and scope need explicit gates**
    - **Scenario:** Cleanup-off users still load an LLM; shared prompt changes affect cloud users; A’s shared local seam also affects notes.
    - **Why the documents miss it:** Both say start both workers; shared callers exceed stated scope.
    - **Mitigation:** Preload LLM only for installed local cleanup; preserve offline launch flags and prohibit cloud fallback. Isolate notes/cloud behavior; bound warm minutes and vocabulary.
    - **Lands in:** Common / Settings.
    - **Proof:** Cited: config.py:91; llm.py:127, :139, :357. Scope effects reasoned.

**What each candidate gets right**

- **A:** Existing transcription/cleanup seams allow reuse without relocating delivery.
- **C:** Explicit parent-child pipes provide direct worker ownership without transcript sockets.

**Verdict:** Build **A**, conditional on two changes: a complete worker-lifecycle/cancellation protocol, and release gates proving memory limits plus prompt/clipboard privacy protections.

Static review only; no code run. The security skill file was inaccessible.