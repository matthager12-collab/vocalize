# For reviewers

Vocalize is a macOS command-line tool with two capabilities, text to speech
and speech to text, both run on the device. This page points at the parts of
the code that matter most for review.

| Area | Where to look | What to check |
|---|---|---|
| Text to speech | `vocalize/preprocess.py`, `vocalize/providers/`, `vocalize/chain.py` | Markdown is rewritten for listening; only local engines are accepted; a fallback is announced |
| Audio cache | `vocalize/cache.py` | Private directory, regular-file checks, atomic replacement, size limits |
| Dictation | `vocalize/dictate.py`, `vocalize/recorder/` | Session and delivery locks, cancel, pause, the recorder's microphone permission, fixed notification text |
| Cleanup | `vocalize/llm.py`, `vocalize/local/llm_worker.py` | Local or off only; the transcript is treated as data; the faithful guard keeps raw text when the model strays |
| Transcription | the `transcribe` command in `vocalize/cli.py` and the module it calls | Bounded worker output, timeouts, private temporary files, skipping sources already processed |
| Settings page | `vocalize/portal.py`, `vocalize/assets/` | Loopback only, one-time code, session header, Origin and Host checks, escaped content, compare-and-swap saves |
| Warm servers | `vocalize/local/warm.py`, `vocalize/local/warm_protocol.py` | Private Unix sockets, peer identity, bounded frames |
| Downloads | `vocalize/local/install.py` and the `*_manifest.py` files | Pinned hosts, HTTPS, pinned revisions, sha256 and size checks |

Two small Swift apps hold the macOS permissions: **Vocalize Recorder** owns
the microphone and **Vocalize.app** (the menu-bar app) owns the keyboard
shortcuts. macOS ties each permission to the app's signature, so their
source rarely changes.

## Tests

CI runs on Python 3.10, 3.12 and 3.14. The suite uses fakes for every model
and for the recorder, needs no microphone, and runs with network access
turned off. Tests against real models are separate and opt-in; see
[local-model-evaluation.md](local-model-evaluation.md). Passing tests do not
prove audio quality, real latency or the two macOS permissions; those need a
person at a Mac.

`docs/plans/` and `docs/research/` hold design records. Older records there
describe features that 1.0 removed; the code and the guides in `docs/`
describe current behavior.
