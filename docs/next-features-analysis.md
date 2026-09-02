# What it would take: config portal + hotkey dictation

Analysis only, 2026-09-01. Nothing built. Sources: two design passes against the vocalize 0.9.0 code, read-only probes of this Mac, and the vetted voicebox checkout (design lessons only — we rebuild from principles, never copy).

## TL;DR

| Feature | Recommendation | Effort | Lazier alternative |
|---|---|---|---|
| **1. Config portal** | A local web page served by vocalize itself, stdlib only (`vocalize portal`) | ~50 h | `vocalize status`: one colored, all-at-once readiness screen, ~1 day |
| **2. Hotkey dictation** | Quick Action toggle → tiny signed Swift recorder → whisper.cpp (`small.en`) via the Kokoro-style uv worker → clipboard + sounds | ~20 h after a 3 h spike | macOS built-in Dictation + a "Clean Up Dictation" Quick Action, ~2 h |

**Suggested order:** dictation first (speaking beats typing for long input), starting with the 3-hour spike that decides the engine. Portal second, or its 1-day lazy version.

**Decisions for you:** go on the spike? portal or `status`? hotkey chord (proposed ⌃⌥⌘D)?

---

## 1. Config portal / GUI

### What exists already
- `vocalize config` — ElevenLabs-only wizard on the terminal.
- `vocalize chain a b c`, `vocalize auth login --provider X`, `vocalize usage`, `vocalize local status`.
- Hand-edited TOML for `[providers.*]` tables.
- The TOML writer already preserves every table (`wizard._render_config_text`), so any GUI writes through it — no new file format.

### Three options

| | A. Extend the wizard (terminal) | B. Local web portal (stdlib) | C. Native app (SwiftUI or Tauri like voicebox) |
|---|---|---|---|
| Sees everything at once | No — paged screens | Yes — tabs + persistent checklist | Yes |
| Voice preview per provider | Yes (via afplay) | Yes (`<audio>` tag, non-blocking) | Yes |
| Kokoro install progress | Text lines | Real progress bar | Real progress bar |
| New dependencies | None | **None** (http.server, secrets, threading) | Xcode, or Rust+npm+webview |
| Testing | Existing key-script fakes | Call the route function directly, no browser | UI automation, slow |
| Keychain | Same process as today | Same process as today | Only if it shells out to vocalize |
| Effort | 30–40 h | **45–60 h** | 56–96 h + signing/notarization |

### Recommendation: B
It is the only option that gives a scannable, all-at-once view without adding a dependency or a second toolchain. Tauri (what voicebox uses) would still need a local Python server underneath — it just wraps one in Rust and React.

### Shape
- `vocalize portal` binds `127.0.0.1` on a random port, mints a per-launch secret, opens the browser.
- One HTML file, vanilla JS. Tabs: **Chain** (up/down reorder), **Providers** (voice dropdown from `list_voices`, preview, speed, budget), **Keys** (masked status, a form that stores via `auth.login` and never shows the key again), **Usage** (ledger vs budgets), **Local** (Kokoro status + install with progress).
- A **readiness checklist** always visible beside the tabs — the one idea worth taking from voicebox: a red gate (no key, uv missing, model absent) can never hide behind a green toggle.
- Security: loopback only, secret checked on every request and sent in a header (never a URL), GET is read-only, mutations are POST, no CORS, a CSP header, keys never echoed, server exits when the tab stops pinging.
- Tests call the pure `route(method, path, headers, body)` function — no sockets.

### Build plan (8 steps)
1. Reuse the wizard's TOML writer; no new config code.
2. `vocalize/portal.py`: token, port, `route()` dispatch, 5-line handler shim.
3. Read endpoint assembling chain, settings, budgets, ledger, key status, Kokoro status.
4. Write endpoints: chain, provider table, key login, voice preview.
5. Kokoro install as a background thread feeding a progress dict.
6. `assets/portal.html` — the visual work.
7. `vocalize portal` command + README.
8. `tests/test_portal.py`.

**Lazier alternative:** `vocalize status` — one screen, colored, every gate and setting at once. A day. Build the portal only if you still find yourself avoiding config changes.

---

## 2. Voice-to-text on a hotkey, through a local model

### Facts that shape it (verified on this Mac)
- Swift 6.1 compiler is present via Command Line Tools (no Xcode needed). `afconvert` is built in, so no ffmpeg.
- No whisper, sox, ffmpeg, pyobjc, sounddevice, or Ollama installed.
- `uv` resolves `pywhispercpp==1.5.1` (whisper.cpp with Metal, 4 MB wheel, numpy only) for Python 3.12 — the same isolation Kokoro already uses. `mlx-whisper` drags in torch; skip it.
- **The Services Quick Action runner has no microphone permission string.** A bare recorder launched from a Quick Action can't be granted mic access. This forces one design choice below.

### The pipeline
```
hotkey (⌃⌥⌘D, Services shortcut)
  → vocalize dictate            press 1: start   press 2: stop
  → "Vocalize Recorder.app"     ~60 lines Swift, ad-hoc signed, LSUIElement — owns the Microphone permission
  → 16 kHz mono WAV in a 0700 temp dir (deleted after)
  → whisper worker              uv run --no-project --with pywhispercpp, one-shot --transcribe
  → clipboard + a sound         (Tink on start, Pop on stop, Glass when text lands)
  → optional --cleanup          transcript only → claude -p haiku, tools denied (already our pattern)
```

### Decisions and why
| Choice | Why |
|---|---|
| **Toggle, not hold-to-talk** | Services shortcuts can't see key-up. Hold-to-talk needs a keyboard event tap and Input Monitoring permission (voicebox wrote its own Rust crate for this). Defer to v2. |
| **Signed Swift recorder bundle** | Only a bundle with its own usage string gets a stable "Vocalize Recorder" entry in Privacy → Microphone. Compiled at install time by `vocalize local install --stt`. |
| **whisper.cpp, `small.en` default** | Best accuracy-per-MB jump; Metal on the M3. Manifest pins four models with sizes and sha256 (tiny 78 MB, base 148 MB, small 488 MB, turbo-q5 574 MB). |
| **One-shot worker, not resident** | A dictation is one request; a resident worker would hold ~800 MB between presses on an 8 GB Mac. Pre-warm only if the spike shows >2.5 s overhead. |
| **Clipboard first, auto-paste later** | Paste needs Accessibility; when added it goes in the same recorder bundle (⌘V via CGEvent). |
| **Cleanup pass opt-in, default off** | The only step that leaves the machine (transcript only, never audio). Reuses the summary code's `--disallowedTools '*'` call. High value for hands-off use; loud in the docs. |
| **Silence guard** | Whisper invents "Thank you." on silence. Below-threshold audio → "Heard nothing" and stop. |
| **No `/dictate` slash command** | Clipboard + ⌘V already works in Claude Code; `vocalize listen \| claude -p` covers pipes. |

### Permissions
| Permission | Needed for | How we ask |
|---|---|---|
| Microphone | always | once, to "Vocalize Recorder", forced during install while you're at a terminal; `vocalize listen --check` afterwards |
| Input Monitoring | hold-to-talk only (v2) | not requested in v1 |
| Accessibility | auto-paste only (v2) | not requested in v1 |

### Spike first (3 h) — decides everything
1. Does a recorder launched from a Quick Action get a mic prompt? Test bare CLI, then the bundle. (Highest risk.)
2. Latency and RAM for base / small / turbo on a 30 s clip, cold and warm.
3. Accuracy on **your** voice with real jargon (paths, flags, "Kokoro", "MCP") — Whisper vs Apple's on-device recognizer. If Apple wins on your speech, the whole Whisper branch is deleted.
4. RAM with Claude Code and a browser already open — the real 8 GB condition.

**Go/no-go:** stop → text in clipboard ≤ 3 s for a 20 s utterance, peak RAM ≤ 1.2 GB, no swap.

### Build plan
| # | Step | Hours |
|---|---|---|
| 0 | Spike | 3 |
| 1 | Whisper manifest + `local install --stt` (generalize the Kokoro installer) | 2.5 |
| 2 | One-shot whisper worker + import-discipline test | 1.5 |
| 3 | Swift recorder, bundle template, build-at-install, `--check`, silence guard | 4 |
| 4 | Toggle state machine, temp-dir lifecycle, sounds, clipboard | 2.5 |
| 5 | `vocalize listen` / `dictate`, `[stt]` config | 2 |
| 6 | "Dictate with Vocalize" Quick Action + installer wiring | 1.5 |
| 7 | `--cleanup` | 1 |
| 8 | Docs + end-to-end on your Mac | 2 |
| | **Total** | **20 (16–28)** |

Deferred: hold-to-talk (+6 h), auto-paste (+2 h), Apple engine mode (+1.5 h), menu-bar agent (+12 h), local LLM cleanup.

**Lazier alternative:** turn on macOS Dictation (press ⌘ twice, on-device, types anywhere) and ship only a "Clean Up Dictation" Quick Action that pipes the clipboard through the existing Haiku cleanup call. ~2 h. Weaker on dev jargon.

---

## What voicebox teaches (and what we don't take)
- Settings as flat rows of title / description / control, in sub-tabs — take the shape.
- A persistent readiness checklist beside any toggle with a prerequisite — take it.
- Real push-to-talk needs an OS event tap, not an app-level shortcut API — noted for v2.
- Dictation's hard part is plumbing (formats, permissions), not the model — our design spends its hours there.
- Its stack (Tauri + React + FastAPI + SQLite, ~50k lines) exists to ship a cross-platform app. For a personal CLI on one Mac it is the wrong trade.
