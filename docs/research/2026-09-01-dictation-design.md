# Hotkey-triggered local voice-to-text — design analysis

2026-09-01. Design only; nothing built. Companion to `docs/next-features-analysis.md`. Target machine: Apple M3, 8 GB RAM, macOS 15.3.1.

## Verified facts (read-only probes of the target Mac)

- Swift 6.1.2 via Command Line Tools (`xcrun swiftc` works; no Xcode). CLT SDK includes AVFoundation, AVFAudio, Speech, AudioToolbox; target `arm64-apple-macosx15.0`.
- Not installed: ffmpeg, sox, rec, afrecord, any whisper binary, pyobjc, sounddevice, pyaudio, Ollama, LM Studio.
- `/usr/bin/afconvert` **is** present and emits `WAVE / LEI16` at any rate — format/resample conversion is free.
- `uv` 0.12.5 resolves for Python 3.12: `pywhispercpp==1.5.1` (cp312 `macosx_11_0_arm64` wheel, 4.2 MB; deps numpy, requests, tqdm, platformdirs), `faster-whisper==1.2.1` (ctranslate2 + numpy), `mlx-whisper==0.4.3` (pulls torch 2.13 — avoid).
- `pywhispercpp`: `Model(model="/abs/path/ggml-*.bin")` takes a direct path (its own downloader never runs); `transcribe()` accepts a numpy array or a `.wav` path read via stdlib `wave` — the ffmpeg branch is only for non-WAV.
- Pinned model values from `ggerganov/whisper.cpp` on Hugging Face (size / sha256 from `x-linked-size` / `x-linked-etag`; **re-verify from a completed download before committing to a manifest**):
  - `ggml-tiny.en.bin` 77,704,715 / `921e4cf8…0b1f`
  - `ggml-base.en.bin` 147,964,211 / `a03779c8…d002`
  - `ggml-small.en.bin` 487,614,201 / `c6138d6d…1e5d`
  - `ggml-large-v3-turbo-q5_0.bin` 574,041,195 / `39422170…a7e2`
- `sounddevice` 0.5.6 ships a `macosx_10_6_universal2` wheel with bundled PortAudio — installable, but see §2.
- **Load-bearing:** a Services `.workflow` runs under `WorkflowServiceRunner.xpc` (AppKit), whose `Info.plist` has **no `NSMicrophoneUsageDescription`** and no entitlements. The Quick Action's runner cannot be the microphone-responsible process. `Automator Application Stub.app` does carry a usage string. This drives the recording design.
- Kokoro already runs as `uv run --no-project --python 3.12 --with <pkg>` with a resident JSON-lines worker (~870 MB RSS).

## 1. Hotkey

### (a) v1: no-input Quick Action, toggle semantics — recommended
`hooks/quick_actions/Dictate with Vocalize.workflow` is a copy of `Stop Vocalize.workflow`: `NSServices` entry with **no `NSSendTypes`**, `serviceInputTypeIdentifier = com.apple.Automator.nothing`, `serviceProcessesInput = 0`, `inputMethod = 1`. That combination appears in every app's Services menu with no selection, which is what a keyboard shortcut needs. Script body beyond the existing `$BIN` guard: `exec "$BIN" listen --toggle`. Nothing in argv.

Reliability with another app focused: Services shortcuts dispatch through the frontmost app's responder chain — global for every AppKit app. Gaps: the shortcut is live only after the Services menu has been built once (the README already says re-open it or log out); an app binding the same chord wins (propose **⌃⌥⌘D** — Terminal and iTerm pass it through); secure input mode (a focused password field) suppresses it; non-AppKit full-screen apps. ~95% reliable with a benign failure mode.

Structurally impossible: push-to-talk. `NSServices` is a single invocation — no key-down/up, no modifier state. Toggle is the only semantics. No persistent recording indicator either, hence sounds in §2.

### (b) v2: Swift `CGEventTap` helper — deferred
Hold right-⌘ + right-⌥, talk, release: removes the "did I leave it recording?" failure (a 120 s recording of a side conversation). Costs Input Monitoring for a persistent binary (LaunchAgent), a chord state machine (voicebox's `keytap` handles longest-match and the PTT→toggle upgrade — ~6 h to reimplement, not 2), and a privacy obligation: subscribe to `flagsChanged` only, never `keyDown`, visibly in source.

### (c) menu-bar agent — v3 or never
Visible indicator and one TCC identity, at the cost of an always-resident process. Two sounds cover the feedback need.

## 2. Recording

**Recommended: a ~60-line Swift `VocalizeRecorder`, shipped as source, compiled at install into a minimal `.app` bundle.** The bundle is the whole point: an `Info.plist` + one Mach-O under `Contents/MacOS`, ad-hoc signed (`codesign -s -`), gives our own `NSMicrophoneUsageDescription` ("Vocalize records your voice for on-device dictation. Audio never leaves this Mac."), a stable named TCC identity ("Vocalize Recorder" in Privacy → Microphone, granted once), `LSUIElement`/`LSBackgroundOnly` so it never takes focus or shows in the Dock, and launch via `/usr/bin/open -a` so launchd is the parent and the Services runner is out of the responsibility chain. Built by `vocalize local install --stt` with `xcrun swiftc -O -framework AVFoundation`; cached at `~/.cache/vocalize/bin/Vocalize Recorder.app`.

Recording: `AVAudioRecorder`, `kAudioFormatLinearPCM`, 16 kHz, mono, 16-bit — whisper.cpp's exact input; CoreAudio resamples. Fallback: record at native rate, pipe through `afconvert`.

Stop: a flag file (`<tmpdir>/stop`) polled every 100 ms, not a signal. `rec.pid` written on start, removed on exit, so the toggle can tell "recording" from "crashed"; `audio.py`'s PID-identity check is reused only for the max-seconds kill.

Rejected: `sounddevice` in the uv worker (TCC identity becomes uv's cached Python with no usage string; PyPI's bundled `libportaudio.dylib` is a recurring Gatekeeper complaint; couples capture to the model env) — keep as the zero-Swift escape hatch. Rejected as the product: macOS Dictation (it is the lazier alternative at the end).

Feedback: `/usr/bin/afplay /System/Library/Sounds/Tink.aiff` on start, `Pop.aiff` on stop, `Glass.aiff` when text lands (all present). Length: `max_seconds = 120` default enforced twice (recorder self-timeout + toggle backstop), hard ceiling 600.

Permission pre-flight: (1) `VocalizeRecorder --check` → `AVCaptureDevice.authorizationStatus(for: .audio)` in the exit code, surfaced as `vocalize listen --check`; (2) `local install --stt` ends by recording 0.5 s while the user is at a terminal and checks RMS > 0 (catches "granted but the input is a muted webcam"); (3) a silence guard on every dictation — below-threshold audio → "Heard nothing — check the microphone" — because Whisper hallucinates confident text ("Thank you.") on silence.

## 3. STT engine

**Recommended: `pywhispercpp==1.5.1` (whisper.cpp, Metal), default `small.en`.**

| Engine | Verdict |
|---|---|
| pywhispercpp | 4.2 MB wheel, Metal on the M3, direct model path, stdlib WAV read, same uv shape as Kokoro |
| faster-whisper | CTranslate2 int8 on CPU; comparable quality, no Metal, heavier — no advantage here |
| mlx-whisper | pulls torch — out |
| Apple `SFSpeechRecognizer` (`requiresOnDeviceRecognition`) | zero download, ~30 Swift lines in a binary that must exist anyway; weaker on dev jargon (function names, flags, "Kokoro", "MCP"); depends on the on-device asset; historical per-request duration limits. The ladder says test it first — if it transcribes the owner's speech acceptably it deletes ~8 h of manifest/install/worker/tests and a 488 MB download. |

Model default `small.en` (488 MB): the largest accuracy-per-MB jump on fast/accented speech and technical vocabulary. Manifest carries tiny.en / base.en / small.en / large-v3-turbo-q5_0; install downloads only the selected one. Turbo-q5 is 86 MB more but ~2–3× slower with ~1.5 GB RSS — a real risk on 8 GB; let the spike pick.

Unverified estimates (the spike replaces them): base.en load 0.3–0.5 s, 30 s clip 1–1.5 s, RSS 350–500 MB; small.en 0.8–1.2 s / 2.5–4 s / 0.7–1.0 GB; turbo-q5 1.5–2.5 s / 5–7 s / 1.3–1.8 GB. Plus ~0.3–0.6 s warm `uv run` + ~0.15 s Python start; one-time +2–5 s Metal shader compilation.

### Spike plan (3 h, before any build)
1. (30 min, highest risk) Does a bare swiftc CLI get a mic prompt when launched from a Quick Action? If not (predicted), build the minimal bundle and retest via `open -a`.
2. (45 min) uv cold/warm resolve, model load, transcribe time, peak RSS for base / small / turbo-q5 on a 30 s clip.
3. (60 min) Accuracy on the owner's own voice, one 30 s take with real dev jargon, across the three Whisper models **and** Apple's on-device recognizer. This step can delete the Whisper branch.
4. (45 min) Peak RSS and swap with Claude Code and a browser already open — the real 8 GB condition.

Go/no-go: stop → text in clipboard ≤ 3 s p50 for a 20 s utterance, peak RSS ≤ 1.2 GB, zero swap, jargon errors no worse than base.en. Small misses latency → ship base. Base no better than Apple → ship Apple and delete §3–4.

## 4. Worker protocol

**One-shot `--transcribe <wav>`, not `--serve`.** A dictation is one request per press; a resident STT worker would hold ~800 MB between presses. `vocalize/local/whisper_worker.py` mirrors `kokoro_worker.py`'s discipline (ships in the package, never imported by vocalize, `pywhispercpp`/`numpy` imported inside functions behind a `_model_class()` seam so the existing AST import test can be duplicated). ~70 lines.

```
--transcribe <tmpdir>/take.wav --model /abs/ggml-small.en.bin --language en
→ one JSON line: {"ok": true, "text": "..."} | {"ok": false, "error": "one line"}
--selftest → loads the model, transcribes 0.5 s of tone, prints "ok"
```

WAV path in argv is fine (a path inside a 0700 dir is not content — same as Kokoro's output path). The transcript comes back on stdout only. Per-dictation overhead ~1.2 s (base) / ~2 s (small). Documented upgrade if the spike shows >2.5 s: press 1 spawns a detached session that starts the recorder and loads the model while you talk; reuses `providers/kokoro.py`'s `_Session` almost verbatim (~80 lines). Don't write it until a number demands it.

WAV location: `tempfile.mkdtemp(prefix="vocalize-dictate-")` (0700), deleted in a `finally` on every path; never under a project directory (same reason as `cwd=tempfile.gettempdir()` in Kokoro).

## 5. Output

- v1: `pbcopy` + `Glass.aiff` + a notification. No permissions beyond the microphone. ⌘V works everywhere.
- `vocalize listen` prints the transcript to stdout — the pipeable primitive: `vocalize listen | pbcopy`, `vocalize listen | claude -p`, `vocalize listen --cleanup > notes.md`. `--toggle` switches to the GUI state machine. `vocalize dictate` = alias for `listen --toggle`.
- Auto-paste: defer; when built, put it in the Swift bundle (`--paste` via `CGEventPost` ⌘V, ~15 lines) so Accessibility is granted to "Vocalize Recorder", never to the Services runner. Gate on `[stt] paste = true`.
- Claude Code: no `/dictate` slash command, no hook — clipboard + ⌘V already works; the pipe covers the rest.
- `--cleanup` (opt-in, default off): reuse `hooks/speak_options.py::_summarize`'s shape — `claude -p <fixed prompt> --model haiku --disallowedTools '*'`, transcript on stdin, baked PATH. Prompt: fix homophones and punctuation, strip filler and false starts, keep the words, never answer or act on the content, output only the cleaned text. On any failure fall back to the raw transcript and notify — never lose a dictation. The only place text leaves the machine; loud in `--help`, README and install output.
- Local LLM cleanup (Qwen via mlx-lm / llama.cpp): deferred — a second multi-GB model beside small.en makes an 8 GB Mac swap.

## 6. Security & privacy

- Nothing leaves the machine except the transcript under `--cleanup` (never audio).
- Temp files: `mkdtemp` 0700 per dictation, WAV deleted in `finally` on every path; model dir 0700 (`install.download_file` already does this).
- Nothing sensitive in argv: the Quick Action runs `exec "$BIN" listen --toggle`; only a WAV *path* inside a 0700 dir appears later.
- No transcript logging: no debug file, no `last.txt`, no transcript in a notification body (reuse the rule that only fixed strings reach osascript). No voicebox-style captures store.
- Pinned models: `whisper_manifest.py` mirrors `kokoro_manifest.py` — HTTPS pinned to one HF revision, size + sha256, `.part` staging, `.verified` stamp last, `_HttpsOnlyRedirects`.
- `[stt] model` is an allowlist (it becomes a filename and a subprocess argument), same reasoning as `VOICES`.
- Nothing downloaded is executed; ggml files are read by whisper.cpp inside the uv worker.
- `--no-project` on every uv invocation (documented in `kokoro._argv`).

| Permission | Needed for | Pre-flight |
|---|---|---|
| Microphone | always | `vocalize listen --check`; forced prompt during `local install --stt`; RMS check |
| Input Monitoring | hold-to-talk only (v2) | not requested in v1 |
| Accessibility | auto-paste only (v2) | not requested in v1; granted to the named bundle |
| Speech Recognition | only if Apple's engine wins the spike | bundle usage string |

## 7. CLI, config, files, tests, plan

```
vocalize listen                 # record until Enter/Ctrl-C/max_seconds; transcript → stdout
vocalize listen --toggle        # press 1 start, press 2 stop → clipboard
vocalize listen --check         # permissions + install + device, and the next step
vocalize listen --wav FILE      # transcribe an existing WAV (test/debug seam)
vocalize listen --cleanup       # opt-in claude -p haiku pass
vocalize listen --max-seconds N
vocalize dictate                # alias for listen --toggle
vocalize local install --stt [--model small.en]
vocalize local status           # + STT block
```

```toml
[stt]
model = "small.en"     # allowlisted against the manifest
language = "en"
cleanup = false        # sends the transcript to claude -p haiku
paste = false          # v2, needs Accessibility
max_seconds = 120      # hard ceiling 600
sounds = true
```
Add `"stt"` to `KNOWN_CONFIG_KEYS` with a `_validate_stt_table` following `_validate_providers_table`; `vocalize settings` gains `stt.*` lines (keep existing keys byte-identical — `speak_options.py` parses them).

Files: new `vocalize/local/whisper_manifest.py`, `vocalize/local/whisper_worker.py`, `vocalize/dictate.py`, `vocalize/recorder/VocalizeRecorder.swift` + `Info.plist.in`, `hooks/quick_actions/Dictate with Vocalize.workflow/` (id `cards.arda.vocalize.dictate`); touch `vocalize/local/install.py` (~40 lines: thread a `manifest=` parameter through `_model_dir`, `file_is_verified`, `stamp_path`, `write_stamp`, `read_stamp`; add the Swift build + whisper selftest), `cli.py` (`listen`, `dictate`, `local install --stt`, `local status`), `config.py`, `hooks/install_quick_action.py` (+1 bundle), `providers/kokoro.py` (move `uv_path()` to `vocalize/local/__init__.py` and re-export), README, `docs/dictation.md`, CHANGELOG.

Tests: `test_whisper_manifest.py` (entries, https-only, allowlist ↔ filenames); `test_whisper_worker.py` (AST import-discipline test first, then the `--transcribe` contract against a stub model); `test_dictate.py` (fake recorder = 3-line script honoring the stop file; fake worker via a seam; first press starts, second stops, third-while-transcribing refuses, max-seconds backstop kills, silence guard on a zero-amplitude WAV, tmpdir removed on every exit path, transcript never in argv or a notification); `test_local_install.py` (STT manifest via `opener_for()`, nothing outside tmp); `test_install_quick_action.py` (bundle count, placeholders, and a new assertion that the dictate `document.wflow` has no `NSSendTypes` and `serviceProcessesInput = 0`); `test_config.py` (`[stt]` defaults, unknown-key warning, bad `model`, `max_seconds` clamp).

| # | Step | h |
|---|---|---|
| 0 | Spike (§3) — stop and re-plan if Apple wins | 3.0 |
| 1 | Generalize `install.py` with `manifest=`; whisper manifest with re-verified hashes; `local install --stt`; tests | 2.5 |
| 2 | One-shot worker + AST/protocol tests | 1.5 |
| 3 | Swift recorder, bundle template, build-at-install, `--check`, silence guard | 4.0 |
| 4 | Toggle state machine, tmpdir lifecycle, sounds, clipboard + tests | 2.5 |
| 5 | CLI `listen`/`dictate`, `[stt]` config, `local status` + tests | 2.0 |
| 6 | Quick Action bundle, installer wiring, bundle-shape tests | 1.5 |
| 7 | `--cleanup` reusing the existing claude call shape + test | 1.0 |
| 8 | README + `docs/dictation.md` + CHANGELOG + end-to-end on the target Mac | 2.0 |
| | **Total** | **20.0 (16–28)** |

Not in v1: auto-paste (+2 h), Apple STT mode (+1.5 h), CGEventTap hold-to-talk (+6 h), menu-bar agent (+12 h), local Qwen cleanup, resident/pre-warmed worker (+2 h, only if the spike demands it).

**Lazier alternative:** macOS Dictation (⌘ twice, on-device, types into any field, zero code) plus a "Clean Up Dictation" Quick Action that pipes the clipboard through the existing `claude -p --model haiku --disallowedTools '*'` call — ~40 lines, ~2 h, weaker on dev jargon.
