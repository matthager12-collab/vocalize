# Voicebox exploration — what its GUI, dictation and hotkeys actually use

Read-only exploration of a vetted checkout of jamiepine/voicebox (v0.5.0, main @ 51f49de), 2026-09-01. Prior intake verdicts: supply-chain vet = adopt with conditions; evaluation = adopt-with-changes as an optional local TTS provider behind vocalize, never a replacement. This captures the source-level facts behind vocalize's own design choices. House rule: rebuild from principles, never copy code.

## 1. GUI stack

- **Desktop shell:** Tauri 2. Rust side ~5,472 lines: `main.rs`, `lib.rs`, `hotkey_monitor.rs`, `audio_output.rs`, `audio_capture/{macos,windows,linux}.rs`, `accessibility.rs`, `input_monitoring.rs`, `synthetic_keys.rs`, `clipboard.rs`, `focus_capture.rs`, `speak_monitor.rs`, `keyboard_layout.rs`, `key_codes.rs`.
- **Frontend:** React 18 + Vite 5 + TypeScript, ~28,914 lines under `app/src/`. State: Zustand stores with `persist`. Server state: TanStack Query. Routing: TanStack Router. UI: Radix primitives + shadcn-style components + Tailwind 4. Forms: react-hook-form + zod. i18n: i18next. Waveforms: wavesurfer.js. The same `app/src` is reused by a Tauri shell (`tauri/src/main.tsx`, 436 lines) and a browser-only shell (`web/src/main.tsx`, 182 lines) via one `platform` interface.
- **Backend:** Python + FastAPI (`backend/app.py`, `backend/server.py`), PyInstaller-frozen sidecar spawned by Tauri. ~20,343 lines excluding tests (33 test files). SQLAlchemy + Alembic over SQLite.
- **Screens:** Generate, Stories, Captures (dictation history), Voices, Effects, Models, Settings.
- **Settings screen** (`app/src/components/ServerTab/`, ~3,026 lines): sub-tabs General / Generation / Captures / MCP / GPU / Logs / Changelog / About.
  - General: server URL, keep-server-running, network access mode, language, theme, Cloud login (the only credential-shaped setting — a bearer token from a browser login, stored server-side), API reference card, auto-updater.
  - Captures: global-shortcut toggle, push-to-talk and toggle-to-talk chord pickers, live pill-state preview, auto-paste toggle (gated on Accessibility), Whisper model size (base/small/medium/large/turbo), transcription language, auto-refine with a local Qwen3 LLM (0.6B/1.7B/4B) plus cleanup toggles, default playback voice, captures folder. A right-hand **readiness checklist** sidebar shows permission and model gates live.
  - No raw "voice API key" fields exist anywhere.
- **Persistence:** backend settings in SQLite (`<data_dir>/voicebox.db`, `GET/PUT /settings/captures`, `/settings/generation`); UI-only prefs in a Zustand `persist` store (webview localStorage).
- **Transport:** plain HTTP REST via `fetch` (`app/src/lib/api/client.ts`) + Server-Sent Events for generation/download progress. No WebSocket. Tauri IPC (`invoke`/`emit`/`listen`) only for non-HTTP-shaped things: hotkey chord events, synthetic paste, audio-output device control. An MCP server is exposed over HTTP at `/mcp` with a stdio shim.

## 2. Speech-to-text ("Captures") — a full shipped pipeline

- **Engine:** OpenAI Whisper, dispatched by platform: Apple Silicon → `backend/backends/mlx_backend.py` (`mlx_audio.stt`, installed `--no-deps` as `mlx-audio==0.4.1`); elsewhere → `backend/backends/pytorch_backend.py` (`transformers` Whisper from `openai/whisper-{size}`). Sizes base/small/medium/large/turbo (`turbo` = `openai/whisper-large-v3-turbo`). Not faster-whisper, not Apple Speech, not cloud.
- **Capture:** in the webview with browser APIs — `navigator.mediaDevices.getUserMedia` + `MediaRecorder` (`audio/webm;codecs=opus`) in `app/src/lib/hooks/useAudioRecording.ts`; converted to WAV client-side (`app/src/lib/utils/audio.ts: convertToWav`) to avoid ffmpeg on the backend. The Rust `audio_capture/*.rs` (~1,753 lines) is a different feature: system-audio capture via ScreenCaptureKit, not the microphone.
- **Trigger:** global hotkey chord, push-to-talk or toggle. Rust fires `dictate:start` (with a focus snapshot) to a transparent Tauri window (`DictateWindow.tsx`) that runs the `MediaRecorder` session; chord-end stops and uploads.
- **Transcribe:** `POST /transcribe` multipart (`backend/routes/transcription.py`), accepts wav/mp3/m4a/ogg/flac/aac/webm/opus; non-WAV decoded by librosa and re-saved as WAV because the MLX STT path only decodes WAV/FLAC/MP3/Vorbis.
- **Refine (optional):** local Qwen3 (`backend/backends/qwen_llm_backend.py`) for cleanup, self-correction, technical-term preservation. Entirely local.
- **Auto-paste:** `invoke('paste_final_text', {text, focus})` → synthetic paste into the app that had focus (`tauri/src-tauri/src/synthetic_keys.rs`), gated on macOS Accessibility.
- **Storage:** transcripts persisted as "captures" (SQLite + `<data_dir>/captures/`).

## 3. Global hotkeys

Not Tauri's global-shortcut plugin. Uses the maintainer's own Rust crate **`keytap` 0.4** (flagged low-severity in the intake vet: four versions published within an hour, ~8.4k downloads), which owns the OS event tap and a `ChordMatcher` (momentary vs toggle, longest-match resolution). `hotkey_monitor.rs` (290 lines) maps `ChordEvent → StartRecording/StopRecording/RestartRecording` and fans them into Tauri events. Defaults: right-Cmd + right-Option on macOS, right-Ctrl + right-Shift on Windows — right-hand modifiers so common left-hand shortcuts stay untouched; left/right distinction kept down to the OS tap. Configured via a `ChordPicker` modal (tracks the *peak* set of keys held so the user can release before saving). Requires macOS Input Monitoring.

## 4. Audio-related dependencies

- Backend (`requirements.txt`, floor pins only — flagged in the vet): `torch>=2.2.0`, `torchaudio`, `librosa>=0.10.0`, `soundfile>=0.12.0`, `numba>=0.60,<0.61`, `numpy>=1.24,<2.0`, `pedalboard>=0.9.0`, `kokoro>=0.9.4`, `misaki[en,ja,zh]>=0.9.4`, `unidic-lite`, spaCy `en_core_web_sm`; MLX-only: `mlx>=0.30.0`, `miniaudio>=1.59`, `mlx-audio==0.4.1`.
- Frontend: `wavesurfer.js` 7.12.2, `react-sound-visualizer` 1.4.0; raw `MediaRecorder`, no wrapper lib.
- Rust: `cpal` 0.15.3 (output devices), `keytap` 0.4.0, `hound` 3.5.1 (WAV), `screencapturekit` 1.5.0 (system audio).

## 5. Backend architecture

Three `typing.Protocol` interfaces — `TTSBackend`, `STTBackend`, `LLMBackend` — in `backend/backends/__init__.py` (796 lines); each backend module is a plain class. Instances cached per engine behind a lock, built by an explicit `if/elif` factory keyed on an `engine` string from the request. **No provider chain / fallback exists**: platform detection picks MLX vs PyTorch once; the only retry (`utils/chunked_tts.py`) re-runs the *same* engine on smaller chunks.

`kokoro_backend.py` in five lines: `load_model(model_size)` lazily loads the 82M pipeline per language; `create_voice_prompt(...)` / `combine_voice_prompts(...)` resolve pre-built style vectors (no cloning); `generate(text, voice_prompt, language, seed, instruct)` → `(np.ndarray, sample_rate)`; `unload_model()` / `is_loaded()`.

## 6. Reusable as design (not code)

- Settings as a horizontal sub-tab strip, each tab a list of `title + description + right-aligned control` rows; each row an independent optimistic mutation.
- A persistent readiness checklist beside the toggles so a red permission/model gate can't hide behind a green switch (`CapturesPage.tsx` ~592–602).
- Chord picker records the peak key set; push-to-talk and toggle are two independently bound chords; left/right modifiers distinguished.
- A floating pill cycling recording → transcribing → refining → rest; the PTT→toggle mid-hold upgrade is handled by detecting a coincident Start/End pair and emitting one `RestartRecording`.
- Caveat: `AudioTab.tsx` (675 lines) is orphaned dead code — don't copy a design without checking it shipped.

## What voicebox teaches

1. Settings scale as flat sub-tabs of small rows, not one long form — copy the shape, not the stack.
2. A readiness checklist next to anything with an OS-permission or model prerequisite stops silent failure.
3. Real push-to-talk needs an OS event tap; app-level shortcut APIs can't see key-up.
4. Dictation's hard part is plumbing (formats, permissions), not Whisper.
5. "Provider abstraction" need not mean runtime fallback; voicebox never swaps engines.
6. Keep platform glue to a few hundred lines behind one interface; voicebox shares 28.9k lines between Tauri and a 182-line browser shell.
