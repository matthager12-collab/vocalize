# Installing vocalize

Vocalize runs on macOS with Apple Silicon. Three commands cover most of the
setup:

```bash
uv tool install vocalize-cli
vocalize app install
vocalize integrate claude
```

Run `vocalize doctor` any time — before, during, or after — to see what's
still missing and the exact command to fix it.

| Command | What it does |
|---|---|
| `uv tool install vocalize-cli` | Installs the `vocalize` binary, user-global |
| `vocalize app install` | Builds and loads **Vocalize.app**, the menu-bar app that owns the dictate/speak/stop hotkeys everywhere |
| `vocalize integrate claude` | Installs the `/speak` skill and the five Quick Actions into Claude Code and macOS |

Everything below fills in the parts those three commands don't fully
automate: the speech engines, the model downloads, and two GUI-only steps macOS
reserves for a human.

New to this, or handing it to someone who is? Send them to
[docs/getting-started.md](getting-started.md) instead. Same install, spelled
out step by step, with every System Settings screen named.

## 0. Apple's command line tools

```bash
xcode-select --install
```

Both `vocalize local install --stt` and `vocalize app install` compile a
small Swift bundle on your machine, so `swiftc` has to exist. A Mac that has
never had Xcode or its command line tools installed does not have it, and the
failure arrives late, at build time. `vocalize doctor` reports it as the
`swiftc` row.

Already installed? The command says so and exits. That's a pass.

---

## 1. The CLI

```bash
uv tool install vocalize-cli
```

Installs **user-global**: one install serves your terminal, Claude Code
desktop, and any IDE terminal. Verify from the shell that will actually use
it — an Electron app's shell can differ from your terminal:

```bash
command -v vocalize && vocalize --version
```

## 2. Speech engines and the chain

The chain is ordered fallback: the first engine that works, speaks.
Default is `kokoro`, then `say`. To change it:

```bash
vocalize chain kokoro piper say
```

This writes `~/.config/vocalize/config.toml` — one config, every consumer
(CLI, `/speak`, the Stop hook, Quick Actions) reads it.

- **Kokoro (local, default primary):** `vocalize local install` fetches the
  model (337 MB, needs `uv`). Check readiness any time with
  `vocalize local status` or `vocalize doctor`.
- **Piper (optional):** `vocalize local install --piper` installs a second
  local voice. Its licence is shown before the download.
- **`say`:** zero-config macOS last resort. Keep it in the chain.

Smoke test:

```bash
echo "vocalize is alive" | vocalize speak-file -
```

Or finish all of the above from a browser instead:

```bash
vocalize portal
```

It opens the settings page through a one-time link at `127.0.0.1` (valid
for 60 seconds). The page is one page with no tabs: a machine profile box
that suggests settings sized for this Mac, then the sections Speaking,
Reading, Dictation, Transcription, Hotkeys, Engines and Advanced. Every
install and removal on this page has a button there, as do the menu-bar
app, the Quick Actions, the `/speak` skill and the Claude Code hook. The
two macOS permissions cannot be switched on by any program, so the page
links to their System Settings panes instead.

## 3. Dictation (speech to text)

A separate opt-in from Kokoro, since it downloads its own model and builds
a small recorder bundle that holds the microphone permission:

```bash
vocalize local install --stt
```

On Apple Silicon this installs Parakeet (2.3 GB). Add `--engine whisper` for
Whisper (547 MB for the default model). Which engine `vocalize transcribe`
uses is explained in [docs/transcribe.md](transcribe.md).
Setup downloads the selected model,
compiles and ad-hoc signs **Vocalize Recorder**, and warms the runtime. Full detail,
including the config table and troubleshooting, lives in
[docs/dictation.md](dictation.md).

## 4. `vocalize app install` — the hotkeys

```bash
vocalize app install
```

Builds and loads **Vocalize.app**, a menu-bar app that owns the dictate,
speak-the-selection, and stop hotkeys system-wide — no Services menu or
per-app shortcut assignment needed. Install, status, uninstall, restart,
and the re-grant rule are all covered in [docs/app.md](app.md).

The app needs dictation's recorder bundle (step 3 above) before its
dictate hotkey does anything; each install is a no-op if the other one
hasn't happened yet.

### Then grant Accessibility — the app does nothing until you do

`app install` finishes successfully, loads the LaunchAgent, and registers the
chords. None of them fire. macOS will not deliver a keypress to an app
without an Accessibility grant, and it neither prompts nor logs when it
withholds one.

**System Settings → Privacy & Security → Accessibility → enable Vocalize.**
Then:

```bash
vocalize app restart
vocalize app status      # want: accessibility: granted
```

This is the single most common reason a fresh install appears to do nothing.
`vocalize doctor` and `vocalize app status` both report it, and both are
worth running before you conclude anything else is wrong.

Note that `app status` can report `hotkeys: ok` while `accessibility: not
granted`. The chords registered. They will still never fire. Read the
accessibility row, not the hotkeys row.

## 5. `vocalize integrate claude` — Claude Code and Quick Actions

```bash
vocalize integrate claude
```

One command instead of a hand-rolled skill file and a separate installer
script. It:

1. Checks `vocalize` and `python3` are on PATH and
   reports each as found or missing.
2. Installs `~/.claude/skills/speak/SKILL.md` from the package — the
   generic `/speak` skill (speak Claude's last response, a file, the
   clipboard, or pasted text) aloud. Leaves an existing file alone unless
   you pass `--yes`.
3. Installs the five Quick Actions into `~/Library/Services` and
   refreshes the Services registry: **Speak with Vocalize**, **Stop
   Vocalize**, **Speak Latest Plan**, **Swap in What I Said**, and **Dictate with Vocalize**.
4. Prints the GUI-only steps below.

The Quick Actions and the skill run local `vocalize` commands. If you
installed the `/speak` skill from an older version, run
`vocalize integrate claude --yes` to replace it.

### The two GUI-only steps

Neither is scriptable — macOS reserves both for a human:

1. **Assign hotkeys.** System Settings → Keyboard → Keyboard Shortcuts →
   Services. Assign ctrl-option-cmd-P to **Speak Latest Plan** and
   ctrl-option-cmd-V to **Speak with Vocalize** (Dictate with Vocalize is
   under the **Text** category there). Don't assign D or X — the menu-bar
   app already owns those chords; Dictate and Stop stay reachable from the
   Services menu if you want a Quick Action fallback too.
2. **Accessibility.** Covered in [step 4](#then-grant-accessibility--the-app-does-nothing-until-you-do)
   — it is a requirement of `vocalize app install`, not of this step. If you
   skipped the menu-bar app, you can skip it here too.

## 6. The Stop hook (optional)

Auto-speaking every Claude Code response is a separate opt-in. It isn't
part of `vocalize integrate claude`. Install and remove it from the
settings page, in its Advanced section; that works on a PyPI install too.
From a git clone you can also run the installer script:

```bash
git clone https://github.com/matthager12-collab/vocalize ~/code/vocalize
python3 ~/code/vocalize/hooks/install_hook.py
```

The clone location becomes load-bearing — the hook entry written into
`~/.claude/settings.json` points at `<clone>/hooks/claude_stop_hook.py`.
Fires in new sessions only (hooks snapshot at session start). Verify
before a real Stop event:

```bash
VOCALIZE_MAX_CHARS=150 python3 ~/code/vocalize/hooks/claude_stop_hook.py --latest
```

To remove it, use the settings page button, or delete the vocalize entry
from the `Stop` array in `~/.claude/settings.json`.

> Field note from the reference install: running this in every open
> session produced overlapping narration within the hour. On-demand speech
> (`/speak`, Quick Actions, the app's hotkeys) is the sustainable default;
> treat the Stop hook as opt-in for single-session work.

---

## `vocalize doctor`

Everything `vocalize status` checks, plus the toolchain and environment:
`cli path`, `uv`, `swiftc`, the running console script's
shebang, a Hammerspoon or old Quick Action conflict, CLI cold-start time,
app bundle state, and the transcripts folder's size on disk.

```bash
vocalize doctor        # formatted rows, same shape as `status`
vocalize doctor --json # a JSON list of {name, state, detail, action}
```

Exits 1 only when a row fails outright; a warn row (Hammerspoon running,
a slow cold start) is worth reading but doesn't fail a
script.

## Post-install checklist

```bash
vocalize doctor                    # everything above, in one shot
vocalize settings                  # chain + resolved config
echo test | vocalize speak-file -  # audible, names the provider used
vocalize listen --check            # dictation: model + recorder + microphone
vocalize app status                # bundle built, LaunchAgent loaded, ACCESSIBILITY GRANTED
```

Not verifiable from a shell: first-use TCC prompts, and whether audio is
actually audible. Test with ears once.

## What still can't be automated

- **Hotkey assignment** — GUI-only, by design; System Settings territory,
  not something a script should touch.
- **First-use permission prompts (TCC)** — microphone and Accessibility.
  macOS asks the human, once per app, on purpose.
- **Audible confirmation** — a shell can prove a provider rendered audio;
  only ears prove the speaker played it.
