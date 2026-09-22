# Report: run 15, notes

Date 2026-09-22. Branch `notes`. Source plan in [project-plan.md](./project-plan.md).

- T-140: done — `whisper_worker.py --segments` emits progress lines and `segments[]` array, byte-identical without flag; tested in `tests/test_whisper_worker.py`.
- T-141: done — `vocalize/notes.py` with self-ingestion guard, done-scan on hash & path, `afconvert` conversion, `_transcribe_long` with progress & inactivity timeout, sanitization on every segment, destination caps on summarization, atomic `.tmp`-then-replace writer with `trust` frontmatter, run lock, and stale sweep.
- T-142: done — Templates `memo`, `meeting`, `lecture`, `journal` in `vocalize/assets/notes/`; custom template path handling (`O_NOFOLLOW`, 64 KB cap, symlink/traversal refusal).
- T-143: done — CLI `vocalize notes SOURCE...` with `--template`, `--summarizer`, `--force`, `--keep-audio`, and egress line logic.
- T-144: done — 19 comprehensive unit tests in `tests/test_notes.py` green; threads clean.
- T-145: done — Docs: `docs/notes.md`, README section, and CHANGELOG entry.
- T-146: deferred — manual real-audio pass (owner present, 3 hours).

Security gate: self-ingestion guard, `sanitize` on every segment, atomic note writing, 0600 permissions, `trust: "untrusted-transcript"` frontmatter, custom-template symlink and size refusal, argument injection `-x.m4a` guard, destination character caps verified in `tests/test_notes.py`.

Deferred: T-146 (owner real-audio pass).

validate-exit: PASS
