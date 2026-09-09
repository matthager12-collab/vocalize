# Getting started

This is the front door. Follow it top to bottom and you will end up with a
working install, whatever your comfort level with a terminal. Every command
is one line you can copy. Every step says what you should see before you move
on.

**About 20 minutes, most of it downloads.** macOS only.

If you already live in a terminal, [docs/installation.md](installation.md) is
the same ground in a quarter of the space.

---

## What you get at the end

- **Press a key anywhere and talk.** Your words land on the clipboard, typed
  by nobody. The transcription happens on your own machine.
- **Select text anywhere and press a key.** It gets read aloud in a real
  voice, also from your own machine.

Nothing you say and nothing you select leaves the Mac, unless you go out of
your way to turn a cloud voice on. That is the default, not a setting.

## What you need first

| | |
|---|---|
| **A Mac** | Apple silicon or Intel. Built and tested on macOS 15; no minimum is pinned, earlier versions are untested |
| **Disk space** | About 1 GB for the two models |
| **Time** | 20 minutes, mostly waiting on downloads |
| **Admin password** | Twice, for Apple's own permission dialogs |

You do not need an account, an API key, or a credit card. The default setup
is entirely local and entirely free.

---

## Step 0 — Open Terminal

Press **Command + Space**, type `Terminal`, press **Return**.

A window opens with a line of text and a blinking cursor. That is the prompt.
Everything below gets pasted there, one block at a time, each followed by
**Return**.

When a command finishes, you get the prompt back. If it seems to hang, it is
usually downloading. Give it a minute before worrying.

> **A note on pasting.** Some steps ask for your password. The cursor will not
> move and no dots will appear as you type. That is deliberate, not a frozen
> terminal. Type it and press Return.

## Step 1 — Apple's developer tools

vocalize builds two small helper apps on your machine rather than shipping
you unsigned binaries to trust. That needs Apple's command line tools.

```bash
xcode-select --install
```

A dialog appears. Click **Install**, accept the licence, and wait. It is
about 1 GB and takes a few minutes.

**Already have them?** You will see `command line tools are already
installed`. That is a pass, not an error. Move on.

**Check it worked:**

```bash
xcrun --find swiftc
```

You want a path back, something ending in `/swiftc`. If you get
`error: unable to find utility`, the install did not finish. Run step 1
again.

## Step 2 — Install uv

`uv` is a small package installer. vocalize uses it to fetch itself and to
run the local voice.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then close Terminal and open it again, so it picks up the new command.

**Check it worked:**

```bash
uv --version
```

You want a version number. If you get `command not found`, close and reopen
Terminal once more.

## Step 3 — Install vocalize

```bash
uv tool install vocalize-cli
```

**Check it worked:**

```bash
vocalize --version
```

You want `vocalize, version 0.13.0` or higher.

> **Already use pipx?** `pipx install vocalize-cli` works exactly the same.
> Use one or the other, not both. `uv` is recommended only because step 4
> needs it anyway.

## Step 4 — Download the voice

```bash
vocalize local install
```

This fetches Kokoro, the on-device voice, about 350 MB. It takes a few
minutes and prints progress.

Skip this and vocalize falls back to the built-in macOS voice, which works
but sounds like 2009.

## Step 5 — Hear it work

```bash
vocalize speak "This is vocalize, running on your own machine."
```

**You should hear it.** This is the first real checkpoint. If you hear
nothing, jump to [Nothing plays](#nothing-plays) below before going further.

## Step 6 — Download the dictation model

```bash
vocalize local install --stt
```

This is a second, separate download, about 550 MB. It fetches the
speech-to-text model and compiles **Vocalize Recorder**, a tiny app whose
only job is to hold the microphone permission. macOS will not grant a
microphone to a command line tool, so this bundle exists to be the thing
macOS can say yes to.

It also spends about eight seconds warming up a graphics shader. That is
deliberate, paid once here so no dictation stalls on it later.

## Step 7 — Install the menu-bar app

```bash
vocalize app install
```

This builds **Vocalize.app** and sets it to start when you log in. It is the
piece that listens for your hotkeys everywhere, in any application.

It will ask you to confirm, and list the two files it is about to write. Say
yes.

## Step 8 — Grant Accessibility

**Do not skip this.** The app is now running and its hotkeys are registered,
but macOS will not let it see your keypresses until you say so. Until you do,
the hotkeys silently do nothing. No error, no prompt, no clue.

1. Open **System Settings**.
2. Go to **Privacy & Security** in the left sidebar.
3. Scroll down and click **Accessibility**.
4. Find **Vocalize** in the list and turn the switch **on**.
5. Enter your password when asked.

Then, back in Terminal:

```bash
vocalize app restart
```

**Check it worked:**

```bash
vocalize app status
```

You want `accessibility: granted`. If it still says `not granted`, the switch
did not take. Toggle it off and on again, then re-run the restart.

> **Why can't the installer do this?** It can't, and neither can any other
> app. Apple reserves this switch for a human sitting at the machine, on
> purpose. Anything that could grant itself the ability to watch your
> keyboard would be a keylogger.

## Step 9 — Your first dictation

Press **Control + Option + Command + D**.

The first time, macOS asks for microphone access, naming **Vocalize
Recorder**. Click **Allow**.

Now talk. Say a sentence or two.

Press **Control + Option + Command + D** again to stop.

A few seconds later a notification says the dictation is on your clipboard.
Press **Command + V** anywhere to paste it.

## Step 10 — Check the whole machine

```bash
vocalize doctor
```

This prints a row for every part of the system and, for anything broken, the
exact command that fixes it.

Rows marked `FAIL` for `openai` and `polly` are normal and expected. Those
are optional cloud voices you have not set up, and the local setup does not
need them.

---

## Your three hotkeys

| Press | What happens |
|---|---|
| **Control + Option + Command + D** | Start dictating. Press again to stop. |
| **Control + Option + Command + S** | Read the selected text aloud. |
| **Control + Option + Command + X** | Stop whatever is being read. |

Want different keys? See [the `[app]` table](app.md#chords).

## Making it talk back to you

By default you get three short sounds: one when recording starts, one when it
stops, one when the text is ready. You can have spoken words instead.

```bash
vocalize portal
```

That opens a settings page in your browser. Or edit the config file directly:

```bash
printf '\n[stt]\ncues = "words"\n' >> ~/.config/vocalize/config.toml
```

Now you hear "start", "stopped" and "ready" instead of chimes. Use `"both"`
to get the word and then the sound.

---

## If something is wrong

### Nothing plays

Check the volume, then check which provider actually ran:

```bash
vocalize status
```

If Kokoro is not ready, re-run step 4. If everything reads ready and you
still hear nothing, the problem is macOS audio output, not vocalize. Try
another app.

### The hotkeys do nothing

Nine times out of ten this is step 8.

```bash
vocalize app status
```

`accessibility: not granted` is the answer. Go back and grant it.

If it says `granted` and the keys still do nothing, something else on your
Mac has claimed the same chord. `vocalize doctor` warns about the two common
culprits, Hammerspoon and an old Services shortcut.

### It asks for my keychain password over and over

This only happens on an upgrade, never a fresh install. Your saved API keys
were locked to the previous version's Python. Click **Always Allow**, not
**Allow**, once for each key. That adds the new reader permanently.

### Dictation produces nothing

```bash
vocalize listen --check
```

This tests the model, the recorder and the microphone separately, and names
whichever one failed.

### Something else

```bash
vocalize doctor
```

Every row that fails carries the command that fixes it. If a row still makes
no sense, open an issue with the output of `vocalize doctor --json`.

---

## Where to go next

| Document | Covers |
|---|---|
| [installation.md](installation.md) | The same install, condensed for a terminal user |
| [dictation.md](dictation.md) | Models, accuracy, the `[stt]` config table, privacy |
| [app.md](app.md) | The menu-bar app: chords, status, uninstall, rebuilds |
| [provider-credentials.md](provider-credentials.md) | Adding cloud voices, and getting keys for them |
| [../README.md](../README.md) | Everything, in full |

## Uninstalling

No hard feelings.

```bash
vocalize app uninstall
vocalize local uninstall
uv tool uninstall vocalize-cli
rm -rf ~/.config/vocalize ~/.cache/vocalize
```

The Accessibility and microphone entries stay in System Settings. macOS keeps
those on purpose. Remove them by hand if you want them gone.
