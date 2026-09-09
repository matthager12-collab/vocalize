# Installing vocalize

Three commands get you fully set up:

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
| `vocalize integrate claude` | Installs the `/speak` skill and the four Quick Actions into Claude Code and macOS |

Everything below fills in the parts those three commands don't fully
automate: providers, the model download, and two GUI-only steps macOS
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

## 2. Providers and the chain

The chain is ordered fallback: the first provider that works, speaks.
Default is `kokoro`, then `say` — nothing to configure for a keyless
machine. To change it:

```bash
vocalize chain kokoro elevenlabs say
```

This writes `~/.config/vocalize/config.toml` — one config, every consumer
(CLI, `/speak`, the Stop hook, Quick Actions) reads it.

- **Kokoro (local, default primary):** `vocalize local install` fetches the
  model (~340 MB, needs `uv`). Check readiness any time with
  `vocalize local status` or `vocalize doctor`.
- **ElevenLabs:** `vocalize auth login` stores the key in the OS keychain.
  See [docs/provider-credentials.md](provider-credentials.md) for the other
  cloud providers and the Electron keychain-visibility gotcha.
- **`say`:** zero-config macOS last resort. Keep it in the chain.

Smoke test:

```bash
echo "vocalize is alive" | vocalize speak-file -
```

Or finish all of the above from a browser instead:

```bash
vocalize portal
```

It opens a one-time link at `127.0.0.1` (valid for 60 seconds) with tabs
for providers, dictation, the app, and now **Setup** — a checklist view of
everything on this page, see [docs/app.md](app.md#the-setup-tab).

## 3. Dictation (speech to text)

A separate opt-in from Kokoro, since it downloads its own model and builds
a small recorder bundle that holds the microphone permission:

```bash
vocalize local install --stt
```

Downloads the whisper.cpp model (~547 MB by default), compiles and
ad-hoc signs **Vocalize Recorder**, and warms the runtime. Full detail,
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

`app install` finishes successfully, loads the agent, and registers the
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

1. Checks `vocalize`, `claude`, `node`, and `python3` are all on PATH and
   reports each as found or missing.
2. Installs `~/.claude/skills/speak/SKILL.md` from the package — the
   generic `/speak` skill (speak Claude's last response, a file, the
   clipboard, a web page, or pasted text). Leaves an existing file alone
   unless you pass `--yes`.
3. Installs the four Quick Actions into `~/Library/Services` and
   refreshes the Services registry: **Speak with Vocalize**, **Stop
   Vocalize**, **Speak Latest Plan**, and **Dictate with Vocalize**.
4. Prints the GUI-only steps below.

Re-run it after `brew upgrade claude-code` — the baked `claude` path is the
stable symlink, not the version-pinned Caskroom target, so this rot is
gone, but re-running after an upgrade is still the way to pick up a new
`claude` binary at that symlink.

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

## 6. The Stop hook (optional, still a repo script)

Auto-speaking every Claude Code response is a separate opt-in, and it
stays a script you run from a git clone — it isn't part of the package or
`vocalize integrate claude`:

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

Uninstall by deleting the vocalize entry from the `Stop` array in
`~/.claude/settings.json`.

> Field note from the reference install: running this in every open
> session produced overlapping narration within the hour. On-demand speech
> (`/speak`, Quick Actions, the app's hotkeys) is the sustainable default;
> treat the Stop hook as opt-in for single-session work.

---

## `vocalize doctor`

Everything `vocalize status` checks, plus the toolchain and environment:
`cli path`, `uv`, `swiftc`, `claude` on PATH, the running console script's
shebang, a Hammerspoon or old Quick Action conflict, CLI cold-start time,
app bundle state, and the notes folder's size on disk.

```bash
vocalize doctor        # formatted rows, same shape as `status`
vocalize doctor --json # a JSON list of {name, state, detail, action}
```

Exits 1 only when a row fails outright; a warn row (Hammerspoon running,
`claude` missing, a slow cold start) is worth reading but doesn't fail a
script.

## Post-install checklist

```bash
vocalize doctor                    # everything above, in one shot
vocalize settings                  # chain + resolved config
echo test | vocalize speak-file -  # audible, names the provider used
vocalize listen --check            # dictation: model + recorder + microphone
vocalize app status                # bundle built, agent loaded, ACCESSIBILITY GRANTED
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
