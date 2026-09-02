# Config portal / GUI — design analysis (A vs B vs C)

2026-09-01. Design only; nothing built. Companion to `docs/next-features-analysis.md`.

## Load-bearing facts in the vocalize code

- `vocalize/wizard.py`: `_render_config_text(data)` is a pure TOML serializer (flat keys first, then `[providers.*]` tables; refuses unknown table shapes rather than corrupting the file); `_write_config(path, data)` writes atomically (`.tmp` + `os.replace`). `cli.py`'s `chain` command already writes through these two functions from outside the wizard — any GUI must do the same, never re-implement TOML.
- `vocalize/config.py`: `resolve_chain`, `resolve_provider_settings`, `provider_table`, `budget_for`, `validate_speed` are a clean library layer with no CLI coupling.
- `vocalize/auth.py`: `login(key, provider)` validates against the provider and stores in one call — exactly the shape a POST handler needs. `masked()` and `key_source()` give safe status without the raw key.
- `vocalize/providers/__init__.py`: `get(name)` lazy-imports a provider; every provider exposes `list_voices()`, `synthesize()`, `DEFAULTS`, `AUDIO_EXT` — a generic "pick a voice, preview it" UI works for every provider.
- `vocalize/local/install.py`: `download_file(..., progress=callback)` already reports progress through a callback; a GUI feeds a shared dict instead of stdout.
- `vocalize/ledger.py`: `status()` / `all_status()` for a Usage view.
- Tests: the suite is `CliRunner` + monkeypatch; nothing in the repo speaks HTTP today.
- Reference: voicebox's `DictationReadinessChecklist.tsx` — one row per gate: icon + short title + one-line status + inline action, rendered as a persistent sidebar. The most transferable idea; cheap in plain HTML/CSS.

Assumptions: single-user Mac, never network-exposed, used for setup/tuning rather than as a daemon, Python ≥ 3.10.

## Option A — extend the wizard (terminal)

- Can: keychain unchanged; voice preview exists for ElevenLabs (`_voice_step`) and generalizes by routing through `providers.get(name).synthesize()`; Kokoro progress via the existing printer.
- Cannot: no persistent checklist (each step clears the screen); no overview — paged, sequential.
- Security: smallest surface; no new process or socket.
- Tests: existing key-script fakes; no new infrastructure.
- Maintenance: `wizard.py` (565 lines) and its tests roughly double once chain reorder + per-provider loops land.
- Effort: chain reorder 0.5–1 d, generalized voice/model/speed 1–1.5 d, budgets 0.25 d, multi-provider loop 0.5 d, tests 1–1.5 d, docs 0.25 d → **4–5 agent-days (30–40 h)**.

## Option B — local web portal, stdlib only (recommended)

- Can: everything A can, plus a persistent readiness sidebar, real visual density control (icons, color, spacing), non-blocking previews via `<audio>`, and a real progress bar for Kokoro install (background thread → locked dict → polling). Keychain calls stay in the same Python process as today.
- Cannot: not a menu-bar presence — the user runs `vocalize portal` (same pattern as `vocalize config`). Up/down buttons instead of drag for a 2–6 item list.
- Security design: bind `127.0.0.1`, port from binding 0; `secrets.token_urlsafe()` per launch, checked on every request and carried in a request header set by the page's own `fetch()` (never in a URL, so never in logs or `Referer`); no `Access-Control-Allow-Origin` (browser same-origin policy is the deny); a Content-Security-Policy header; GET strictly read-only, every mutation POST; keys never echoed (reuse `auth.masked()`); shutdown when the page's periodic `/api/ping` stops for N intervals.
- Tests: keep `BaseHTTPRequestHandler` as a 5-line shim over one pure `route(method, path, headers, body) -> (status, content_type, body)`; `tests/test_portal.py` calls `route()` directly — no sockets, threads or browser.
- Maintenance: one module `vocalize/portal.py` (http.server, secrets, threading, json, webbrowser) + one static `assets/portal.html` (vanilla JS/CSS, no build step, loaded with `importlib.resources`). Zero new dependencies.
- Effort: server skeleton 0.5–1 d; read endpoints 0.5 d; write endpoints 1–1.5 d; Kokoro streaming 0.5 d; the HTML page 1.5–2 d; hardening + tests 0.5 d; handler tests 1–1.5 d; docs 0.25 d → **5.5–7.5 agent-days (45–60 h)**.

## Option C — native macOS (SwiftUI menu bar, or Tauri like voicebox)

- Can: native drag reorder, progress bars, VoiceOver/Dynamic Type. But all logic lives in Python; the shell either shells out to the CLI per action (needs a `--json` mode first, ~1 d) or re-implements config/provider logic in a second language (rejected).
- Keychain: fine only if every keychain operation still goes through the Python process — a standing temptation to call Security.framework directly.
- Tooling: SwiftUI needs Xcode (10+ GB on an 8 GB machine); distribution beyond a self-built binary needs signing/notarization. Tauri brings Rust + npm + webview and still needs the local Python server underneath — a second toolchain for no new capability over B.
- Tests: XCTest UI automation (slow, flaky); none of the pytest suite transfers.
- Effort: SwiftUI **7–10 agent-days**; Tauri **8–12 agent-days**.

## Recommendation: B

Matches the house style (stdlib before deps) and actually delivers the scannable, all-at-once view the terminal wizard structurally cannot. C buys polish this feature doesn't need at 1.5–2× the effort and a toolchain to keep alive forever.

### Build plan
1. Reuse `wizard._render_config_text` / `_write_config`; build the merged dict the way `_walk()` does.
2. `vocalize/portal.py`: token, port from `127.0.0.1:0`, pure `route()` + 5-line handler shim.
3. `GET /api/state`: chain, per-provider settings, budgets, ledger, key status (masked), Kokoro `installed()`.
4. Writes: `POST /api/chain`, `POST /api/provider/<name>`, `POST /api/auth/login`, `POST /api/voices/<name>/preview` (returns audio bytes with the right content type).
5. Kokoro install in a background thread; `GET /api/local/install/status` polls.
6. `assets/portal.html`: persistent readiness sidebar + tabs Chain / Providers / Keys / Usage / Local; high contrast, large targets, short labels.
7. `vocalize portal` in `cli.py`: token, server loop, `webbrowser.open()`, print the URL as fallback.
8. `tests/test_portal.py`: wrong/missing token, GET-vs-POST enforcement, chain/provider round-trips against a tmp config, install polling shape, preview with a mocked provider. README subsection.

**Lazier alternative:** `vocalize status` — a `click.secho`-colored, single-screen readiness report (chain, per-provider key, budget, Kokoro). About a day. Build the portal only if config changes are still being avoided because the terminal output reads as a wall of text.
