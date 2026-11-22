# Decisions: vocalize, local only

Decided by the maintainer under the owner's standing approval of
2026-11-21, which covers every choice up to and including publishing the
new version. Each entry names the evidence it rests on. The owner can
reverse any of them; the Review line says what to look at.

| # | Question | Status | Decision | Round |
|---|---|---|---|---|
| DEC-001 | What is the base of the work? | Decided | B: the earlier local-only commit `90b5dbc`, with `main` merged in | R1 |
| DEC-002 | What does "just speech to text and text to speech" keep? | Decided | B: transcript cleanup stays inside dictation; summaries go | R1 |
| DEC-003 | What runs transcript cleanup? | Decided | A: the pinned on-device worker | R1 |
| DEC-004 | One shared speech runtime, or one worker per engine? | Decided | A: one pinned worker per engine | R1 |
| DEC-005 | What happens to a 0.15 config file on upgrade? | Decided | B: warn, ignore, migrate | R1 |
| DEC-006 | How does the UI come to cover every setting? | Decided | A: one settings registry drives everything | R1 |
| DEC-007 | How does file transcription survive without notes? | Decided | B: `transcribe`, with `notes` kept as an alias | R1 |
| DEC-008 | What is the version number? | Decided | A: 1.0.0 | R1 |
| DEC-009 | Which third-party packages need an intake review first? | Decided | A: all of them, the five existing pins included | R1 |

---

## Round 1

### DEC-001: What is the base of the work?

**Question**: What is the base of the work?

**Date**: 2026-11-21
**Decided by**: the maintainer, under the owner's standing approval for this release
**Status**: Decided

**Context**: `main` is 0.15.0 with every cloud path in place. An earlier,
never-pushed line of work removed the cloud providers and credentials and
then added call-capture features on top. Commit `90b5dbc` on that line is
the last one before the call-capture work.

| Option | Description | Trade-offs |
|---|---|---|
| A | Start from `main`, merge the Parakeet branch, remove the cloud code again by hand | Cleanest history. Repeats about 11,000 lines of removal and test rewriting that already exist. |
| B | Start from `90b5dbc`, merge `main`, delete the call-capture module | Reuses the finished removal. Carries one call-capture module, one command group and two documents that must be deleted. |
| C | Start from the tip of the call-capture line and strip it | Also carries app-audio capture, a third Swift bundle and call orchestration: about 2,600 more lines to strip. |

**Recommendation**: B. Measured on 2026-11-21 at `90b5dbc`: `ruff check .`
passes, the suite reports 2360 passed and 6 skipped, the runtime
dependencies are `click` and `tomli` only, and merging `main` conflicts in
`CHANGELOG.md` and `README.md` alone.

**Decision**: B.

**Consequences**: The branch history contains commits whose messages mention
a call-capture foundation; `main` receives one squashed commit, so that history
does not land there. Parakeet and Piper arrive with the base. Anything the
later call-capture commits fixed in shared code is not included and has to be
found by tests.

**Applied to**:
- [design.md](./design.md) § Context
- [plan.md](./plan.md) § Phase 0, T-01 to T-03

**Review**: 2026-12-21. Check that no call-capture code or wording remains
anywhere in the released tree.

---

### DEC-002: What does "just speech to text and text to speech" keep?

**Question**: What does "just speech to text and text to speech" keep?

**Date**: 2026-11-21
**Decided by**: the maintainer, under the owner's standing approval for this release
**Status**: Decided

**Context**: Two features use a language model. Transcript cleanup rewrites
a dictated take into what was meant; the owner asked for it in September
and the owner's own config turns it on. Summaries turn a transcript or a long selection into
a shorter text. Neither is transcription or synthesis in the narrow sense.

| Option | Description | Trade-offs |
|---|---|---|
| A | No language model anywhere | Smallest product. Dictation loses self-correction and typed numbers: the repo's own eval scores 5 of 26 without a model, 11 of 26 with simple rules, 25 of 26 with the model. |
| B | Cleanup stays as a stage of dictation; summaries go | Dictation output stays as good as it is today. Keeps a language model in the product, on device. |
| C | Keep cleanup and local summaries | No loss of features. Summaries are a third capability, and one note template exists only for recorded calls. |

**Recommendation**: B. Cleanup changes how well speech becomes text;
summaries make a different document.

**Decision**: B. Removed: the notes templates, the summarizer, the
`summarize-text` command, the summary depths in the Speak Quick Action, and
the web and file digest modes of the `/speak` skill with their URL gate.

**Consequences**: A program that reads the transcripts and expected a local
summary in each note keeps the transcripts and loses the summary. Summarizing becomes a job for whatever reads the
transcripts. The long-selection picker offers speak all or truncate.

**Applied to**:
- [design.md](./design.md) § Approach, § Contracts
- [plan.md](./plan.md) § Phase 1, T-10 to T-12

**Review**: 2026-12-21. Ask the owner whether losing summaries in the
transcript pipeline is acceptable, and whether cleanup should stay.

---

### DEC-003: What runs transcript cleanup?

**Question**: What runs transcript cleanup?

**Date**: 2026-11-21
**Decided by**: the maintainer, under the owner's standing approval for this release
**Status**: Decided

**Context**: Cleanup runs today in a pinned worker: one hash-checked model,
`mlx-lm`, started offline, with the prompt built as token ids so dictated
text cannot end a turn. The alternative raised in research is a local
server with a standard chat API, such as Ollama.

| Option | Description | Trade-offs |
|---|---|---|
| A | Keep the pinned on-device worker | Measured (25 of 26, 8 of 8 adversarial). Provably local: vocalize starts it offline from files it verified. Keeps about 700 lines. One small model. |
| B | Call a loopback-only local server | Deletes the worker and manifest; any model size. A loopback server can forward to a hosted model, and vocalize cannot tell, so "local only" would rest on another program's configuration. Loses the token-id protection. |
| C | No model cleanup | See DEC-002 option A. |

**Recommendation**: A. The requirement is that nothing leaves the machine,
and only A lets vocalize show that itself.

**Decision**: A.

**Consequences**: Cleanup quality and speed stay as measured. The cleanup
model stays one pinned choice; a larger one for the Studio needs its own
manifest and an eval run. The worker and its warm server stay in the tree.

**Applied to**:
- [design.md](./design.md) § Approach, § Local-only contract
- [plan.md](./plan.md) § Phase 3, T-35

**Review**: 2026-12-21. Re-run the cleanup eval on the Studio and record
the latency of a take.

---

### DEC-004: One shared speech runtime, or one worker per engine?

**Question**: One shared speech runtime, or one worker per engine?

**Date**: 2026-11-21
**Decided by**: the maintainer, under the owner's standing approval for this release
**Status**: Decided

**Context**: Each engine is installed from a pinned, hash-checked manifest
and runs in its own process under `uv run`, with a small socket server that
keeps a model loaded. Research found one maintained project, `mlx-audio`,
that hosts both directions and could replace that machinery with one
long-lived server.

| Option | Description | Trade-offs |
|---|---|---|
| A | Keep one pinned worker per engine; add engines the same way | Each engine pins its own package and weights and runs offline. More files per engine. Lean runtimes for the defaults. |
| B | One `mlx-audio` server for every engine | Deletes about 640 lines of warm-server code. Weekly releases, a heavy dependency tree, a server that listens on TCP, and model pinning and offline behaviour not yet verified. |

**Recommendation**: A. The two research notes on engines both recommend
lean per-engine runtimes for the defaults. B's unknowns sit exactly on the
local-only proof.

**Decision**: A. `mlx-audio` is considered only as the runtime of an
optional large-machine engine, after an intake review and a measured spike.

**Consequences**: The warm-server code stays. Adding an engine means a
manifest, a worker and settings entries. The question reopens if a spike
shows `mlx-audio` running pinned, offline and on a Unix socket.

**Applied to**:
- [design.md](./design.md) § Structure, § Contracts
- [plan.md](./plan.md) § Phase 3, T-30 to T-33

**Review**: 2027-02-21. Revisit after the intake review of `mlx-audio`.

---

### DEC-005: What happens to a 0.15 config file on upgrade?

**Question**: What happens to a 0.15 config file on upgrade?

**Date**: 2026-11-21
**Decided by**: the maintainer, under the owner's standing approval for this release
**Status**: Decided

**Context**: Published users have config files naming cloud providers. The
base commit rejects such a file with an error on every command, including
the ones the menu-bar app runs, where no error text is visible.

| Option | Description | Trade-offs |
|---|---|---|
| A | Reject retired settings with an error | Nothing retired is ever silently read. Every command fails after upgrading until the file is edited by hand. |
| B | Warn once, ignore retired keys, migrate what has a local meaning | Upgrades keep working. A cloud cleanup value becomes `off`, never a different engine. Retired keys linger until the next save removes them. |

**Recommendation**: B.

**Decision**: B. Malformed values for keys that still exist remain errors.

**Consequences**: The loader carries a list of retired keys. The first save
from the settings page removes them, after the page says so.

**Applied to**:
- [design.md](./design.md) § Contracts, settings registry
- [plan.md](./plan.md) § Phase 1, T-13

**Review**: 2026-12-21. Load a real 0.15.0 config and read the warnings.

---

### DEC-006: How does the UI come to cover every setting?

**Question**: How does the UI come to cover every setting?

**Date**: 2026-11-21
**Decided by**: the maintainer, under the owner's standing approval for this release
**Status**: Decided

**Context**: The settings page edits the chain, one provider and six of
thirteen dictation keys. Hotkeys, reading rules, long-read limits and most
install choices have no control. Lists of keys are written out by hand in
the validator, the writer, the `settings` command, the page and the README,
and have drifted apart.

| Option | Description | Trade-offs |
|---|---|---|
| A | One declarative registry of settings that generates validation, the writer, `vocalize settings`, the README table and the page, with tests that fail when a key has no control | Coverage is checked by a test, not by care. A sizeable migration. Keeps the existing page server and its security model. |
| B | Add the missing controls by hand | Less work now. Drifts again with the next key. |
| C | A UI framework, or a native settings window | Frameworks add ten or more dependencies and a web server; a native window means changing the signed app, which costs every user a permission re-grant. |

**Recommendation**: A.

**Decision**: A. The page stays served by the standard library on loopback,
behind the one-time code, with its Host and Origin checks and its
compare-and-swap save.

**Consequences**: Every new setting is one registry entry. Choices that are
commands (installs, uninstalls, integrations) become actions with buttons.
The menu-bar app's settings entry point stays "Open portal".

**Applied to**:
- [design.md](./design.md) § Contracts, settings registry and portal
- [plan.md](./plan.md) § Phase 2, T-20 to T-26

**Review**: 2027-02-21. Add one setting and confirm the parity tests are
the only thing that had to be satisfied.

---

### DEC-007: How does file transcription survive without notes?

**Question**: How does file transcription survive without notes?

**Date**: 2026-11-21
**Decided by**: the maintainer, under the owner's standing approval for this release
**Status**: Decided

**Context**: `vocalize notes` is the only command that takes ordinary audio
files. It couples transcription to a summary. `listen --wav` takes one
16 kHz mono WAV file.

| Option | Description | Trade-offs |
|---|---|---|
| A | Delete `notes`; widen `listen --wav` | Fewest commands. Loses the record of processed files, dated note files and timestamps, and breaks the downstream pipeline. |
| B | Rename to `vocalize transcribe`, keep everything but the summary, and keep `notes` as a hidden alias | Existing callers keep working and already-processed files stay recognised. One alias to carry. |

**Recommendation**: B.

**Decision**: B. The frontmatter keys stay, with `summarized_by: "none"` and
`left_machine: false`; `vocalize settings` keeps a `notes.summarizer=off`
line because a caller reads it.

**Consequences**: The config table becomes `[transcribe]`, and `[notes]` is
read as its old name. The alias and the compatibility line can go in a
later major version.

**Applied to**:
- [design.md](./design.md) § Contracts, transcription
- [plan.md](./plan.md) § Phase 1, T-12

**Review**: 2026-12-21. Run the alias against a folder a 0.15 run already
processed and confirm nothing is transcribed twice.

---

### DEC-008: What is the version number?

**Question**: What is the version number?

**Date**: 2026-11-21
**Decided by**: the maintainer, under the owner's standing approval for this release
**Status**: Decided

**Context**: The package is public. Its default voice chain starts with a
cloud provider, so upgrading changes what `vocalize speak` does for every
user who never configured anything.

| Option | Description | Trade-offs |
|---|---|---|
| A | 1.0.0 | Says plainly that this is a breaking release and that the product is now defined. Commits to more care with later breaks. |
| B | 0.16.0 | Allowed before 1.0. An automatic upgrade removes features with no signal. |

**Recommendation**: A.

**Decision**: A. The changelog opens with what was removed and tells cloud
users to stay on 0.15.

**Consequences**: Later removals wait for 2.0, including the `notes` alias.

**Applied to**:
- [plan.md](./plan.md) § Phase 4, T-40, T-42

**Review**: 2026-12-21. Read the published changelog as a 0.15 user would.

---

### DEC-009: Which third-party packages need an intake review first?

**Question**: Which third-party packages need an intake review first?

**Date**: 2026-11-21
**Decided by**: the maintainer, under the owner's standing approval for this release
**Status**: Decided

**Context**: The owner's rule is that a third-party package is reviewed for
supply-chain risk before it is installed or run. The product already pins
five engine runtimes: `kokoro-onnx` 0.6.1, `pywhispercpp` 1.5.1 and `mlx-lm`
0.31.3 shipped in 0.15.0; `parakeet-mlx` 0.5.2 and `piper-tts` 1.8.0 were
chosen on the pushed Parakeet branch. None has run on the Mac Studio.

| Option | Description | Trade-offs |
|---|---|---|
| A | Review all of them before anything runs on the Studio | Strictest. Five more reviews of large dependency trees before any measurement, for packages already in use on the owner's other Mac. |
| B | Review every new or upgraded package first; treat the five existing pins as already adopted | Measurement can start with the engines the product already ships. The five pins carry the trust they had, no more. |

**Recommendation**: A. The reviews run alongside the build, so they cost
no time, and none of the five pins has a review on record.

**Decision**: A, revised from B on 2026-11-21 before anything had run.
Reviewed first: `tomlkit`, `pytest-socket` (neither was adopted in the
end), `kokoro-onnx`, `pywhispercpp`,
`mlx-lm`, `parakeet-mlx` and `piper-tts`; then each candidate engine
runtime.

**Consequences**: A package that fails or is not reviewed in time is left
out of 1.0.0 and recorded as deferred. Measurement on the Studio starts
after the verdicts for the shipped engines.

**Applied to**:
- [plan.md](./plan.md) § Phase 3, T-30
- [verification.md](./verification.md) § Phase 3 exit

**Review**: 2026-12-21. Read the intake reports and their conditions.
