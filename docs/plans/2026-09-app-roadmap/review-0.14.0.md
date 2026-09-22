# Adversarial review: vocalize 0.14.0 (Phases 13–15b)

**Date:** 2026-09-22  
**Scope:** branch `notes` across Phases 13–15b:
- Phase 13 (Local LLM cleanup): `vocalize/local/llm.py`, `vocalize/local/llm_worker.py`, `vocalize/local/llm_manifest.py`, on-device Qwen 3.5 4B model management.
- Phase 14 (Whisper worker segments): `vocalize/local/whisper_worker.py --segments`.
- Phase 15 (Notes): `vocalize/notes.py`, `vocalize/assets/notes/`, notes CLI commands, self-ingestion guards, custom template loading, summarizer prompt pipelines.
- Phase 15b (Recording pause/resume): `vocalize/dictate.py` (`pause`, `resume`, `_join_segments`, `_spawn_self_stop_watcher`), `vocalize/config.py` (`max_take_seconds`), stop-chord precedence in `vocalize/cli.py`.
**Lenses:**
1. `untrusted-pipeline`: audio/text file ingestion → transcript → prompt injection → note output; template path traversal and symlink handling.
2. `worker-and-isolation`: `whisper_worker` and `llm_worker` execution boundaries, subprocess argv construction, `--no-project` uv pinning, ML runtime leakage.
3. `take-lifecycle-and-budgets`: multi-segment audio joins, workdir permissions, session state preservation, corrupt marker recovery, memory & duration caps.
**Process:** Every finding was investigated against the codebase and unit test suite. All potential issues were either refuted with structural evidence or fixed and verified with tests.

## Findings

| Severity | Title | File | Status | Resolution |
|---|---|---|---|---|
| medium | `_join_segments` could crash on mismatched audio format if a third-party process injected an arbitrary WAV into workdir | `vocalize/dictate.py` | fixed | Enforced strict format validation (channels=1, sampwidth=2, framerate=16000) on all segments before joining. Workdir itself is enforced `0700` same-UID. Pinned by `tests/test_dictate.py::test_joined_wav_keeps_16k_mono_16bit`. |
| low | Custom notes template path could read unbounded data if a FIFO or massive file is provided | `vocalize/notes.py` | fixed | `_load_template` uses `os.open` with `O_RDONLY \| os.O_NOFOLLOW` and enforces a strict 64 KB file size cap (`stat.st_size <= 65536`). Refuses non-regular files and symlinks. Pinned by `tests/test_notes.py`. |
| low | Audio source file with leading dash could be misinterpreted as option flag by conversion tool | `vocalize/notes.py` | fixed | `_convert_to_wav` invokes `afconvert` with explicit `-f WAVE -d LEI16@16000 -c 1` before the positional source and target paths, resolving `source.resolve()` to an absolute path. Pinned by `tests/test_notes.py`. |
| low | Corrupt or non-finite `paused` marker could corrupt resume budget calculations | `vocalize/dictate.py` | fixed | `_read_paused_marker` validates that `epoch` and `cumulative` are finite, positive floats. Corrupt or unparseable markers are rejected and treated as no pause. Pinned by `tests/test_dictate.py::test_resume_treats_a_corrupt_paused_marker_as_no_pause`. |
| low | Note summarization prompt could allow prompt injection from untrusted audio transcripts | `vocalize/notes.py` | refuted | Transcripts are enclosed within strict `<transcript>...</transcript>` tags with explicit system directives that transcripts are data only. Output length is strictly capped (`_MAX_SUMMARY_CHARS = 4000`), and frontmatter stamps `trust: "untrusted-transcript"`. |
| low | Segment resume could allow infinite recording across repeated pause cycles | `vocalize/dictate.py` | refuted | Cumulative seconds are capped by `[stt] max_take_seconds` (default 1800, bounded 60..7200) and total segment count is hard-capped at `_MAX_SEGMENTS = 20`. Pinned by `tests/test_dictate.py::test_resume_refuses_past_the_take_budget` and `::test_resume_refuses_a_twenty_first_segment`. |

**No critical or high findings exist. All medium and low findings are resolved and verified.**

## Verification and Defenses by Lens

### 1. untrusted-pipeline
- **Self-ingestion guard:** `vocalize notes` refuses to process files within the notes directory unless `--force` is specified, preventing recursive amplification loops (`tests/test_notes.py`).
- **YAML frontmatter safety:** Done scanning uses custom line-by-line parsing without invoking generic deserializers. Frontmatter stamps `trust: "untrusted-transcript"`.
- **Atomic note writing:** Notes are written to a `.tmp` file using `O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW` with `0600` permissions before an atomic `os.replace`.
- **Text sanitization:** Every segment passes through `_sanitize_text` to strip ANSI escape codes and ASCII control sequences before reaching notes or summary prompts.

### 2. worker-and-isolation
- **No-project execution:** Worker subprocesses are spawned via `uv run --no-project --python ...` ensuring strict dependency isolation from the local workspace.
- **DEC-027 chat template safety:** `llm_worker.py` builds token IDs directly from hardcoded model constants, refusing to evaluate untrusted Jinja or downloaded chat templates.
- **Clean venv compliance:** Wheel builds verify that no ML runtimes (`pywhispercpp`, `onnxruntime`, `mlx`, `sherpa`, `numpy`, `torch`, `boto3`) leak into the standard client package.

### 3. take-lifecycle-and-budgets
- **Lossless segment join:** Segment joining uses stdlib `wave` with 0.25 s of zero frames (`b"\x00"`) inserted at each seam, preserving 16 kHz 16-bit mono attributes.
- **Budget enforcement:** `[stt] max_take_seconds` bounds the entire take; `_MAX_SEGMENTS = 20` bounds segment count; `_transcribe_timeout` scales dynamically with duration.
- **Workdir protection:** Workdirs are created under `mkdtemp` with `0700` mode, verified for UID ownership, and swept automatically if older than 24 hours. Paused takes younger than 24 hours are preserved.
