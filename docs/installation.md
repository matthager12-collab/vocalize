# Installing vocalize — CLI, providers, Claude Code, and macOS integration

This is the full installation guide, written after a complete from-scratch
install on a real machine (macOS, 2026-09-01). Every command in the main flow
was actually executed during that install; anything not exercised is labeled
**untested**. It supersedes the scattered install notes in the README by
treating the system as what it actually is: **six layers that install
separately**. Layer 5 (dictation) was added for 0.10.0 (2026-09-02), after
the original from-scratch narrative below — its command-level behavior
(config resolution, `--check`/`--list-devices` exit codes, `--wav`) was
verified the same way as the rest of this doc; the live microphone grant and
a real hotkey press need the owner physically present, the same ceiling
described in [What still can't be automated](#what-still-cant-be-automated).

| Layer | What you get | Installed by | Scope |
|---|---|---|---|
| 0. CLI | `vocalize` binary | `pipx install vocalize-cli` | user-global |
| 1. Providers | Kokoro (local), ElevenLabs, `say` | `vocalize local install`, `vocalize auth login`, `vocalize chain` | user-global |
| 2. `/speak` | slash command / skill inside Claude Code | **not shipped yet** — create by hand (below) | user-global |
| 3. Stop hook | every Claude Code response auto-spoken | `hooks/install_hook.py` (repo) | user-global, new sessions |
| 4. Quick Actions | right-click → Services, hotkey-able | `hooks/install_quick_action.py` (repo) | user-global + 2 GUI steps |
| 5. Dictation | hotkey → speech-to-text → clipboard | `vocalize local install --stt` + layer 4's installer | user-global + 1 GUI step (hotkey) + 1 permission prompt |

The single most common confusion: **the PyPI package ships layer 0 only.**
Layers 3–5 live in the git repo, and layer 2 currently ships nowhere — the
README references `/speak`, but no artifact creates it. If you installed via
pipx and wonder where `/speak` is: that's why.

---

## Layer 0 — the CLI

```bash
pipx install vocalize-cli
```

- pipx installs are **user-global**: one install serves your terminal, Claude
  Code desktop, IDE terminals — everything. Never "reinstall per app."
- Binary lands at `~/.local/bin/vocalize` (a symlink into
  `~/.local/pipx/venvs/vocalize-cli/`). Verify from the environment that will
  actually use it — shells inside Electron apps can differ from your terminal:

```bash
command -v vocalize && vocalize --version
```

## Layer 1 — providers and the chain

The chain is ordered fallback: first provider that works, speaks.

```bash
vocalize chain kokoro elevenlabs say
```

writes `~/.config/vocalize/config.toml` — **one config, every consumer** (CLI,
`/speak`, Stop hook, Quick Actions all shell out to the same binary). Env vars
override config per-run; `vocalize settings` always shows what resolved and
from where.

- **Kokoro (local, recommended primary):** `vocalize local status` to check;
  `vocalize local install` to fetch models (~340 MB, needs `uv`) — *untested
  in this install; models were already present.* Expect a short cold-start on
  the first utterance (~5 s total for a short line).
- **ElevenLabs:** `vocalize auth login` stores the key in the OS keychain
  (*untested in this install*). Known gotcha: a key stored from your terminal
  may not be visible to keyring lookups from an Electron app's shell — if
  ElevenLabs mysteriously reports "no key" only inside Claude Code, use
  `ELEVENLABS_API_KEY` in `~/.zshenv` instead.
- **`say`:** zero-config macOS last resort. Keep it in the chain; it's what
  makes a keyless fresh machine still speak.

Smoke test (also proves stdin + preprocessing):

```bash
echo "vocalize is alive" | vocalize speak-file -
```

## Layer 2 — the `/speak` command in Claude Code

Until the repo ships this (tracked as an open gap), create it by hand at
`~/.claude/skills/speak/SKILL.md`. A skill (not a `~/.claude/commands/` file)
is deliberate: skills demonstrably surface in Claude Code **desktop**, get
natural-language triggering ("read that aloud"), and — observed live — the
desktop harness **hot-loads a new skill into running sessions**, so it works
without a restart (the typed `/speak` autocomplete may still need a new
conversation).

Required semantics (mirror the README's promises):

- `/speak` (no args) → speak Claude's previous response
- `/speak clip` → `vocalize clip` (the clipboard — see Electron caveat below)
- `/speak stop` → `vocalize stop`
- `/speak <path>` → `vocalize speak-file <path>`
- `/speak <text>` → speak the text

Two implementation rules that matter:

1. **Never interpolate spoken text into a shell command.** Write the raw
   markdown to a unique temp file and run `vocalize speak-file <tmp>` — the
   preprocessor wants raw markdown, and quoting bugs (backticks, `$`, quotes)
   disappear entirely.
2. **Run playback in the background.** A long read on a foreground shell call
   gets killed at the tool timeout, mid-sentence.

## Layer 3 — the Stop hook (auto-speak every response)

```bash
git clone https://github.com/matthager12-collab/vocalize ~/code/vocalize
python3 ~/code/vocalize/hooks/install_hook.py
```

> **The clone location becomes load-bearing.** The hook entry written into
> `~/.claude/settings.json` is
> `python3 <clone>/hooks/claude_stop_hook.py` — delete or move the clone and
> the hook dies silently. Treat `~/code/vocalize` as installed software, not
> a scratch checkout.

The installer is safe: idempotent, merges rather than overwrites, and writes
a timestamped backup (`settings.json.bak.<epoch>`) first.

Behavior and controls:

- Fires in **new sessions only** — hooks are snapshotted at session start.
  The session you install from stays silent.
- Applies to terminal **and** desktop — they share `~/.claude/settings.json`
  and the same transcript layout (verified: the hook script finds
  desktop-session transcripts).
- Default cap ~500 chars per response (`VOCALIZE_MAX_CHARS` to change) — a
  Stop hook fires every turn, so an uncapped hook burns quota/time fast.
- **Uninstall:** delete the vocalize entry from the `Stop` array in
  `~/.claude/settings.json`.

Verify **before** any real Stop event, using the script's on-demand mode:

```bash
VOCALIZE_MAX_CHARS=150 python3 ~/code/vocalize/hooks/claude_stop_hook.py --latest
```

If that speaks your latest Claude response, the whole pipeline (transcript
discovery → preprocessing → provider chain) is proven.

> **Reference-install postscript:** the hook was installed, lived for about
> an hour, and was removed the same day. With several Claude Code sessions
> running, every one of them narrating every response was noise, not
> signal — on-demand speech (`/speak`, Quick Actions, hotkeys) proved to be
> the sustainable default. Treat this layer as opt-in for single-session
> workflows, not part of "full" setup. Removal really is just deleting the
> vocalize entry from the `Stop` array — and note that sessions opened
> while the hook was live keep speaking until they end (hooks snapshot at
> session start, in both directions).

## Layer 4 — macOS Quick Actions (right-click + hotkeys)

**Pre-check first — this is the one silent-failure mode.** The installer
bakes `shutil.which()`-resolved absolute paths into the workflows
permanently. Run:

```bash
command -v vocalize claude node python3
```

Every path must be a **stable** location (`~/.local/bin`,
`/opt/homebrew/bin`). If anything resolves into a session-scoped or
version-pinned directory, run the installer with a sanitized PATH
(*fallback, untested in this install — stable paths made it unnecessary*):

```bash
PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin" python3 ~/code/vocalize/hooks/install_quick_action.py
```

Otherwise, plainly:

```bash
python3 ~/code/vocalize/hooks/install_quick_action.py
```

Installs four workflows into `~/Library/Services/` and refreshes the
registry: **Speak with Vocalize** (selected text), **Stop Vocalize**,
**Speak Latest Plan** (newest `~/.claude/plans/` file — made for the
plan-approval moment), and **Dictate with Vocalize** (the dictation
hotkey — see [Layer 5](#layer-5--dictation-speech-to-text) below; it needs
no input and shows no window).

Known rot: even with the pre-check, `.resolve()` follows symlinks, so
`claude` bakes to a **version-pinned Caskroom path** (e.g.
`/opt/homebrew/Caskroom/claude-code/2.1.223/claude`). After
`brew upgrade claude-code`, the summary depths in the picker die silently
(speak-all/truncate keep working) until you re-run the installer. One
command, idempotent — re-run it after upgrades.

### The two steps no script can do (GUI-only, by design)

1. **Hotkeys:** System Settings → Keyboard → Keyboard Shortcuts →
   **Services** → assign shortcuts to the four actions (Dictate with
   Vocalize is under the **Text** category there — the other three are
   under **General**). Give **Stop Vocalize** its own shortcut — it's your
   mute button from anywhere; Dictate with Vocalize needs one too, since a
   Service with no shortcut can only be triggered by name from the menu.
   Scripting this via `defaults write pbs` is off-limits territory (system
   settings) and fragile besides — don't automate it. You *can* verify it:
   `defaults read pbs NSServicesStatus` shows a `key_equivalent` per
   assigned service; zero entries means nobody assigned anything, whatever
   they remember doing.
2. **First-use permission prompts:** macOS asks once per app the first time
   a Quick Action runs. Approve them as they appear.

**Electron caveat** (Claude Code desktop included): these apps don't expose
the Services menu for text selected in their own windows. There, copy the
text and use `/speak clip`.

---

## Layer 5 — Dictation (speech to text)

The reverse direction: a hotkey records your voice and transcribes it
on-device with whisper.cpp, then puts the transcript on the clipboard.
Full detail lives in [docs/dictation.md](./dictation.md); this is the
install-layer summary in the same shape as layers 0–4.

```bash
vocalize local install --stt
```

Downloads one whisper.cpp model (~465 MB for the default `small.en`),
verifies it against a pinned sha256, compiles and ad-hoc signs the
**Vocalize Recorder** bundle (the thing that actually holds the microphone
permission — `xcrun swiftc`, a few seconds, not a download), then warms the
runtime — paying whisper.cpp's one-time Metal shader compile here rather
than during a real dictation.

This layer depends on layer 4's installer for the "Dictate with Vocalize"
Quick Action and its hotkey — install both, in either order; each is a
no-op if the other isn't done yet, but you need both before ⌃⌥⌘D (or
whatever shortcut you assign) actually does anything.

**Sequence:**

1. `vocalize local install --stt` (this layer's model + recorder + runtime)
2. `python3 hooks/install_quick_action.py` (layer 4, if not already run —
   installs the Dictate Quick Action bundle alongside the other three)
3. Assign a shortcut: System Settings → Keyboard → Keyboard Shortcuts →
   Services → Text → **Dictate with Vocalize**
4. Press it once. macOS prompts for microphone access naming **"Vocalize
   Recorder"** — this is the first-use permission prompt this doc's ceiling
   section already names as GUI-only; approve it. The press waits while the
   dialog is up (nothing is recording yet) and starts recording, with its
   Tink, the moment you click Allow.
5. Verify: `vocalize listen --check` — exit 0 means ready; any other exit
   names the next step (see [docs/dictation.md § Troubleshooting](./dictation.md#troubleshooting)
   for the full table).

**Known rot, same shape as layer 4's:** Vocalize Recorder's microphone
permission is tied to its ad-hoc code signature. A vocalize upgrade that
changes `vocalize/recorder/VocalizeRecorder.swift` forces a rebuild, which
changes that signature and silently revokes the grant — `local install
--stt` prints a re-grant warning when this happens, and it's worth
believing rather than assuming last install's grant still holds.

**Command-level behavior verified in a scratch environment** (not this
doc's real-machine narrative, since dictation postdates it): `vocalize
listen --check`, `--list-devices`, `--wav` on a synthesized clip, `status`,
`settings`, and `resume` with nothing to resume all produce the messages
and exit codes documented here and in docs/dictation.md. **Not verified by
this pass, and owner-present work per this project's own release process:**
the real microphone grant dialog, a real-voice dictation through the actual
hotkey, and `vocalize stop` racing a live dictation — TCC prompts and a
physical key press can't be scripted, the same ceiling as layer 4's GUI
steps.

---

## Post-install verification checklist

All verifiable from a shell (run each; every one was exercised on the
reference install):

```bash
vocalize settings                       # chain + resolved config
vocalize local status                   # "Kokoro is ready."
echo test | vocalize speak-file -       # audible, names the provider used
python3 -c "import json;print(json.load(open('$HOME/.claude/settings.json'))['hooks']['Stop'])"
ls ~/Library/Services/                  # four .workflow bundles
ls ~/.claude/skills/speak/SKILL.md      # /speak exists
VOCALIZE_MAX_CHARS=150 python3 ~/code/vocalize/hooks/claude_stop_hook.py --latest
defaults read pbs NSServicesStatus      # hotkeys assigned? (GUI step)
vocalize listen --check                 # dictation: model + recorder + microphone + device
```

Not verifiable from a shell: first-use TCC prompts, and whether audio is
actually audible (headphones, volume). Test with ears once.

---

## The automation prompt

Paste this into Claude Code on any fresh Mac to run the whole install
agentically. It encodes every safeguard the reference install needed.

```text
Set up vocalize (github.com/matthager12-collab/vocalize, PyPI: vocalize-cli)
with FULL Claude Code + macOS integration on this machine. Work in layers,
verify each before the next, and never skip a pre-check because a step
"looks obvious."

0. ORIENT. Check what already exists before installing anything:
   `pipx list`, `command -v vocalize`, `ls ~/.claude/skills/speak/`,
   `python3 -c ...` to read hooks from ~/.claude/settings.json,
   `ls ~/Library/Services/`, `vocalize local status`, `vocalize settings`.
   pipx installs are user-global — never reinstall per app. Report what's
   already done and only do the gaps.

1. CLI: `pipx install vocalize-cli` if absent. Verify with
   `command -v vocalize && vocalize --version`.

2. PROVIDERS: If `vocalize local status` says Kokoro isn't ready, run
   `vocalize local install` (~340 MB download — tell me before starting it).
   Set local-first fallback: `vocalize chain kokoro elevenlabs say`.
   Do NOT handle any ElevenLabs API key yourself — if I want ElevenLabs,
   tell me to run `vocalize auth login` myself. Smoke test:
   `echo "setup test" | vocalize speak-file -` and report which provider
   actually spoke.

3. /speak SKILL: If the repo ships a Claude Code skill for /speak, install
   that. Otherwise create ~/.claude/skills/speak/SKILL.md with these
   semantics: no args = speak my previous response; "clip" = vocalize clip;
   "stop" = vocalize stop; an existing file path = vocalize speak-file on
   it; any other text = speak it. Implementation rules: write spoken text
   to a unique temp file and speak-file it (never interpolate text into a
   shell line); run playback in the background; feed raw markdown (the
   preprocessor handles structure).

4. STOP HOOK (ask me first — it makes EVERY new Claude Code session speak
   every response, terminal and desktop): clone the repo to ~/code/vocalize
   and run `python3 ~/code/vocalize/hooks/install_hook.py`. Warn me that
   the clone is now load-bearing (the hook points into it — moving or
   deleting it breaks the hook). Verify by re-reading ~/.claude/settings.json
   and confirming the Stop entry merged (and that the backup file the
   installer prints actually exists). Then prove the pipeline with:
   `VOCALIZE_MAX_CHARS=150 python3 ~/code/vocalize/hooks/claude_stop_hook.py --latest`
   Note: the hook only fires in NEW sessions — say so in the report.

5. QUICK ACTIONS: BEFORE running the installer, check
   `command -v vocalize claude node python3` — every result must live in a
   stable directory (~/.local/bin, /opt/homebrew/bin). If anything resolves
   into a session-scoped or temp path, run the installer with
   PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin" prefixed.
   Then: `python3 ~/code/vocalize/hooks/install_quick_action.py`.
   Confirm the three .workflow bundles landed in ~/Library/Services/.
   Warn me: the baked claude path is version-pinned, so after
   `brew upgrade claude-code` I must re-run this installer (summary modes
   break silently otherwise).

6. HAND OFF the two GUI-only steps to me explicitly — do NOT attempt them:
   (a) assign hotkeys in System Settings → Keyboard → Keyboard Shortcuts →
   Services (Stop Vocalize deserves its own), (b) approve macOS's first-use
   permission prompts. Never write to the `pbs` defaults domain. You MAY
   verify my claim later by READING `defaults read pbs NSServicesStatus`
   and checking for key_equivalent entries.

7. FINAL REPORT in three states: verified-done (with the command output
   that proves each), done-but-unverifiable-from-shell (audio audibility,
   TCC prompts), and not-done (anything I still owe, like hotkeys).
   Remind me: /speak may need a new conversation to appear in autocomplete,
   and the Stop hook speaks starting with my next new session.
```

---

## What still can't be automated

Being honest about the ceiling — an installer that claims 100% is lying:

- **Hotkey assignment** — GUI-only. Scriptable in theory via `defaults write
  pbs`, but that's system-settings territory: fragile across macOS versions
  and not something an agent should touch. Readable, though — verification
  is automatable even where the action isn't.
- **First-use permission prompts (TCC)** — macOS asks the human, once per
  app, on purpose.
- **Audible confirmation** — a shell can prove the provider rendered audio;
  only ears prove the speaker played it.

## Lessons learned from the reference install (2026-09-01)

### What went well

- **pipx's user-global model** collapsed the "install it on this instance
  too" request to zero work — the terminal install was already serving the
  desktop app. The real task was discovering *that*, not reinstalling.
- **The provider chain design paid off immediately**: the first smoke test
  had no ElevenLabs key reachable and degraded gracefully to `say` instead
  of failing; switching to local-first later was one command
  (`vocalize chain kokoro elevenlabs say`) that instantly covered every
  consumer, because the chain lives in one config file.
- **Skill over command file** for `/speak`: skills provably render in the
  desktop app, and the harness hot-loaded the new skill into the *live*
  session — zero-restart install, which no one expected.
- **The repo's installers held up**: idempotent, merge-don't-overwrite,
  timestamped backup, non-interactive, accurate printed instructions. Small
  scripts, no surprises.
- **Verify-before-the-real-event**: `claude_stop_hook.py --latest` proved
  the entire hook pipeline (including that desktop-session transcripts are
  found — previously only assumed for terminal/IDE) without waiting for a
  Stop event that the installing session can never fire.
- **PATH-stability pre-check before baking** caught the one silent-failure
  mode *before* it happened — the installing shell carried a dozen
  session-scoped plugin dirs that would have died with the session.
- **Remote-first repo inspection** (`gh api .../git/trees`) answered "does
  the repo ship /speak?" without cloning; the clone happened only when it
  was about to become load-bearing.

### What didn't go well / gotchas found

- **Docs promised what no artifact ships**: the README references `/speak`
  (twice), but neither the PyPI package nor the repo creates it. Cost: a
  full diagnostic dig on a machine owned by the tool's own author. Fix is
  tracked: ship the skill + an installer step in the repo.
- **The PyPI/repo split is invisible at install time**: `pipx install`
  succeeds, the CLI works, and nothing tells you layers 2–4 exist elsewhere.
- **`.resolve()` bakes rot**: the Quick Actions installer resolved
  `/opt/homebrew/bin/claude` through its symlink into a version-pinned
  Caskroom path (`.../claude-code/2.1.223/...`). Every `brew upgrade
  claude-code` will silently break summary modes until re-run. Repo fix:
  bake the stable symlink (or resolve at runtime), not the fully-resolved
  target.
- **Keychain visibility differs by shell**: an ElevenLabs voice/model was
  configured, but no key was reachable from the desktop app's shell — a
  terminal-stored keychain item isn't guaranteed visible there. Test from
  the environment that will actually run, not the one you installed from.
- **Session-start snapshotting confuses everyone once**: the installing
  session never auto-speaks (hooks) and may not show `/speak` in
  autocomplete (commands) — "it doesn't work" is often "open a new session."
- **"I think that's done" is checkable — check it**: the human believed the
  hotkey step was complete; `defaults read pbs NSServicesStatus` showed
  zero `key_equivalent` entries machine-wide. Where a GUI step has a
  readable side effect, read it instead of trusting the recollection.
- **GitHub Releases drifted five versions behind main** (v0.4.0 vs 0.9.0,
  five release commits in one day) — versioning lived in commit messages
  and PyPI only. A `gh release create` step in CI would keep them honest.
- **Small shell traps**: zsh globbed the unquoted `?` in a
  `gh api "...?recursive=1"` URL ("no matches found"); and macOS's default
  bash 3.2 has no associative arrays. Quote API URLs; don't assume bash 4.
- **Auto-speak-everything didn't survive contact with real usage**: with
  multiple Claude Code sessions open, every session narrating every
  response produced overlapping voices within the hour, and the hook came
  back out. Two lessons in one: (a) the sustainable default is on-demand
  speech, hook opt-in for single-session workflows; (b) the overlap exposed
  that vocalize had **no playback mutex** — `play()` docstrings openly
  said concurrent reads talk over each other. Fixed in 0.9.1: playback now
  queues machine-wide on an exclusive flock (`play.lock`), whole sequences
  hold one slot so chunks never interleave, and a killed waiter can't
  leave a stale lock. Verified by timing: two concurrent reads = solo time
  + one extra playback, not parallel.
- **`uv run --extra dev pytest` shows 2 phantom failures** — the dotenv
  tests need the optional `dotenv` extra too. Run with
  `--extra dev --extra dotenv` for a truthful suite (806 pass).

## Making the repo do this in one command (future work)

1. **Ship `/speak`** as `claude/skills/speak/SKILL.md` + installer step
   (open task).
2. **One entry point** — `python3 hooks/install_all.py` or a
   `vocalize integrate claude` subcommand: runs the PATH pre-check, installs
   skill + hook + Quick Actions, runs the capped `--latest` smoke test, then
   prints exactly the two GUI steps and the three-state report.
3. **Fix path baking** — prefer stable symlinks over `.resolve()` targets.
4. **`vocalize doctor`** — detect rot: baked paths that no longer exist,
   hook entry pointing at a missing clone, chain providers that can't
   initialize, releases drift.
5. **CI release sync** — tag → `gh release create`, so GitHub Releases stop
   lagging PyPI.
