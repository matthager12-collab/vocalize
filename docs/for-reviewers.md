# For reviewers

Vocalize is a macOS command-line tool with two capabilities, text to speech
and speech to text, both run on the device. This page points at the parts of
the code that matter most for review.

| Area | Where to look | What to check |
|---|---|---|
| Text to speech | `vocalize/preprocess.py`, `vocalize/providers/`, `vocalize/chain.py` | Markdown is rewritten for listening; only local engines are accepted; a fallback is announced |
| Audio cache | `vocalize/cache.py` | Private directory, regular-file checks, atomic replacement, size limits |
| Dictation | `vocalize/dictate.py`, `vocalize/recorder/` | Session and delivery locks, cancel, pause, the recorder's microphone permission, fixed notification text |
| Vocabulary | `vocalize/vocabulary.py` | Exact letter-and-digit matching only, no matching by sound; the correction step leaves text with no term in it unchanged (Whisper's hint, in `vocalize/dictate.py`, can still change a transcript) |
| Cleanup | `vocalize/llm.py`, `vocalize/local/llm_worker.py` | Local or off only; the transcript is treated as data; the faithful guard keeps raw text when the model strays |
| Transcription | `vocalize/notes.py` and the `transcribe` command in `vocalize/cli.py` | Bounded worker output, timeouts, private temporary files, skipping sources already processed |
| Settings | `vocalize/settings_registry.py`, `vocalize/actions.py`, `vocalize/profiles.py` | One list of settings drives the checks, the writer, `vocalize settings`, the README table and the page; every action has a button or a written reason; a profile writes only registered keys |
| Settings page | `vocalize/portal.py`, `vocalize/assets/` | Loopback only, one-time code, session header, Origin and Host checks, escaped content, compare-and-swap saves |
| Warm servers | `vocalize/local/warm.py`, `vocalize/local/warm_protocol.py` | Private Unix sockets, peer identity, bounded frames |
| Downloads | `vocalize/local/install.py` and the `*_manifest.py` files | Pinned addresses, redirects only to HTTPS, sha256 and size checks |
| Engine environments | `vocalize/local/locks/`, `vocalize/local/runtime.py` | Hash-locked package lists, binary packages only, offline start with an allow-listed environment; Piper is pinned by version only |
| Engine workers | `vocalize/local/*_worker.py` | Refusal of relative or missing paths, of a model folder holding Python code, and of malformed recordings |

## Limits of the hash lock

The hash-locked lists cover the packages for Kokoro, Parakeet, Whisper and
the cleanup model. They do not cover:

- **Piper.** Its package is pinned by version only.
- **The Python that uv downloads** for the engine environments. uv checks
  it itself; Vocalize does not pin it.
- **A runtime chosen with `VOCALIZE_WHISPER_PYTHON` or
  `VOCALIZE_LLM_PYTHON`.** These evaluation variables point at another
  Python, which is checked by version only.
- **uv's own user configuration.** It can change the package index. The
  hashes still decide which files are accepted.

## Swift apps

Two small Swift apps hold the macOS permissions: **Vocalize Recorder** owns
the microphone and **Vocalize.app** (the menu-bar app) owns the keyboard
shortcuts. macOS ties each permission to the app's signature, so their
source rarely changes.

## Tests

CI runs on Python 3.10, 3.12 and 3.14. The suite uses fakes for every model
and for the recorder, and needs no microphone. It blocks every connection
that is not to the Mac itself. `tests/test_local_only.py` also scans the
code's imports for network clients outside four allowed modules (the
downloader, the settings page server and the two warm-server modules); the comment
above that scan lists what it cannot see. `tests/test_settings_parity.py`
fails when a setting has no control the page can draw, when code reads a
setting that is not registered, when a profile sets one, or when the
README's settings table is out of date.

Tests against real models are separate and opt-in; see
[local-model-evaluation.md](local-model-evaluation.md). Passing tests do not
prove audio quality, real latency or the two macOS permissions; those need a
person at a Mac. What was measured on real hardware is in
[plans/2026-11-local-only/measurements.md](plans/2026-11-local-only/measurements.md).

`docs/plans/` and `docs/research/` hold design records, kept in the
repository for history and left out of the source archive. Older records
there describe features that 1.0 removed; the code and the guides in
`docs/` describe current behavior. See [plans/README.md](plans/README.md).
