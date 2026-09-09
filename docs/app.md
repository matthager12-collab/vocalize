# The menu-bar app

`Vocalize.app` is a small Swift menu-bar app that owns the dictation and
speak-selection hotkeys system-wide. It replaces both the Hammerspoon
config and the Quick Action as the way to trigger vocalize from the
keyboard — it runs all the time, as a LaunchAgent, so a hotkey works from
any app without a terminal or a Service menu.

It does one job: listen for its three chords, then run the matching
`vocalize` command (`dictate`, `clip`, `stop`) as a subprocess. It never
touches your microphone or clipboard itself and never reads the audio or
the transcript — `vocalize` does that work, same as if you'd typed the
command.

## Install

```bash
vocalize app install
```

This:

1. Checks that the `vocalize` you're running right now is one the app can
   find. The app only looks in `~/.local/bin`, `/opt/homebrew/bin` and
   `/usr/local/bin`; if yours isn't one of those, install prints a
   `defaults write` line (see [Pointing the app at a different
   vocalize](#pointing-the-app-at-a-different-vocalize) below) and stops —
   pass `--yes` to print the same line and continue anyway.
2. Warns if Hammerspoon is running, or if the "Dictate with Vocalize"
   Quick Action has a keyboard shortcut on one of the app's chords —
   either can steal it (see
   [Conflicts](#conflicts-hammerspoon-and-the-old-quick-action)).
3. Builds `Vocalize.app` under `~/Library/Application Support/vocalize`
   if it isn't already current — see [The re-grant
   rule](#the-re-grant-rule) for what happens when a build replaces one
   that was already there.
4. Writes the LaunchAgent plist at
   `~/Library/LaunchAgents/cards.arda.vocalize.app.plist` and loads it,
   so the app starts now and again at every login.

It asks to confirm first, listing exactly what it's about to write.
`--yes` skips that prompt.

It also needs `swiftc`, from Apple's command line tools, to build the bundle.
If `xcode-select --install` has never been run on the machine, do that first.

## Then grant Accessibility

**Install is not finished when the command exits.** The app is running and
its chords are registered, and not one of them will fire until you grant
Accessibility by hand.

**System Settings → Privacy & Security → Accessibility → enable Vocalize.**

```bash
vocalize app restart
vocalize app status      # want: accessibility: granted
```

macOS neither prompts for this nor logs the refusal. A keypress simply never
arrives. That silence is why this is the most common "the install worked but
nothing happens" report, and why it gets its own heading here rather than a
line inside the install steps.

`status` can read `hotkeys: ok` and `accessibility: not granted` at the same
time. The chords registered with the OS; the OS is not delivering them. The
accessibility row is the one that decides whether anything works.

No installer can do this step. Anything able to grant itself the right to
watch your keyboard would be a keylogger, so Apple reserves the switch for a
human at the machine. See also [the re-grant rule](#the-re-grant-rule) — a
rebuild resets the grant and you come back here.

## Status

```bash
vocalize app status
vocalize app status --json
```

Reports what's built, whether the LaunchAgent is loaded, and what the
app itself last wrote about its hotkeys and Accessibility grant:

| Row | Reports |
|---|---|
| `bundle` | `current`, `stale`, or `not built` — read-only, this never triggers a build |
| `agent` | `loaded`, `not running`, or `unknown` — from `launchctl`; a result it can't parse reads as `unknown`, never `not running` |
| `accessibility` | `granted`, `not granted`, or `unknown` |
| `hotkeys` | `ok`, `failed:<name>`, or `unknown` — which chord, if any, failed to register |
| `hotkey_backend` | `carbon` or `monitor` — which macOS API is handling the keys |
| `vocalize` | the path the app resolved, or `none` |

The last four come from the app's own status file and can be stale
between runs — the app only rewrites it when something changes.

`--json` prints the same six keys as one object. Exit code is 0 when
`bundle` is `current` and `agent` is `loaded`, 1 otherwise, so it
composes with `&&` in a script.

## Chords

Three chords, set under `[app]` in `~/.config/vocalize/config.toml`:

| Key | What it does | Default |
|---|---|---|
| `dictate` | start/stop dictation | `ctrl+alt+cmd+d` |
| `dictate_mode` | `toggle` or `hold` (`hold` arrives in 0.13.1) | `toggle` |
| `speak` | speak the selection | `ctrl+alt+cmd+s` |
| `stop` | stop whatever is playing | `ctrl+alt+cmd+x` |

```toml
[app]
dictate = "ctrl+alt+cmd+d"
dictate_mode = "toggle"
speak = "ctrl+alt+cmd+s"
stop = "ctrl+alt+cmd+x"
```

Tokens join with `+`. Modifiers are `ctrl`, `alt`, `cmd`, `shift` (or the
long `control`, `option`, `command`); the key is one of `a`-`z`, `0`-`9`,
`f1`-`f12`. Every chord needs `ctrl` or `cmd`, because macOS refuses to
register the rest. The three must differ from each other; `""` disables
one.

A chord the parser rejects is a config error, and config is read by
almost every command — so a typo here stops `vocalize speak` and
`vocalize status` too, with a message naming the key and the legal keys.
`vocalize doctor` still runs, and reports it as a failed `config file`
row. The running app keeps the chords it already registered until the
file is valid again and you run `vocalize app restart`.

## Restart

```bash
vocalize app restart
```

Stops and starts the running app without touching the LaunchAgent or
rebuilding anything — the equivalent of quitting it from its own menu
and having it come back. Use this after a config change (a new chord
under `[app]`) instead of a full `install`.

## Uninstall

```bash
vocalize app uninstall
```

Unloads the app and removes what `install` put on this machine: the
bundle, the LaunchAgent plist, the app's status and log files, and the
`VocalizeBinary` override if one was set. It lists exactly what it's
about to remove and asks first; `--yes` skips the prompt.

The Accessibility entry itself stays in System Settings — macOS keeps a
record of every app that ever asked, uninstalled or not — remove it
there if you want it gone too. Dictation and the recorder's own
microphone grant are a separate install (`vocalize local install --stt`)
and are untouched by this command.

## The re-grant rule

The app's Accessibility permission is tied to its ad-hoc code signature,
the same way the recorder's microphone permission is (see
[docs/dictation.md](dictation.md#rebuilds-and-re-granting-the-microphone)).
`install` only rebuilds the bundle — and only resets the grant — when one
of these is true:

- the Swift source changed since the bundle was last built, or
- the bundle or its stamp file is missing (deleted by hand, or by
  `app uninstall`, or by clearing `~/Library/Application Support`
  yourself).

Either one produces a new signature, so `install` runs
`tccutil reset Accessibility cards.arda.vocalize.app` before loading it
and prints:

```
Vocalize.app was rebuilt — its Accessibility grant was reset; re-grant it in
System Settings › Privacy & Security › Accessibility the next time it asks
```

A plain `install` on an already-current bundle never touches `tccutil` —
most upgrades don't touch the Swift source, so most re-installs cost you
nothing.

## Pointing the app at a different vocalize

The app doesn't search your `PATH` — it only checks three fixed
locations. If `install` reports your `vocalize` isn't one of them, run
the line it prints:

```bash
defaults write cards.arda.vocalize.app VocalizeBinary /path/to/vocalize
```

Uninstalling removes this override automatically.

## The Setup tab

`vocalize portal` has a **Setup** tab, after Local. It walks the nine
setup steps — toolchain, building both bundles, the login item, chords,
the model download, the microphone, `/speak` and Quick Actions,
Accessibility, and a self-test — as a checklist built from the readiness
rows the sidebar shows (step 1 points at `vocalize doctor`), so it's always
current with what's actually on the machine. Its "Install the app" button drives this same `app install`
sequence (build, plist, bootout, bootstrap, and the Accessibility reset on
a rebuild) through the portal instead of the CLI; the GUI-only steps still
print from `vocalize integrate claude`, not the page.

## Quit stays quit

The app's own menu has a Quit item, and quitting it stays quit — it does
not relaunch itself the next time it crashes on purpose or the way a
typical LaunchAgent does on every exit. It comes back at your next login,
or the next time you run `vocalize app restart` or `install`.

## Conflicts: Hammerspoon and the old Quick Action

Two older ways of binding the same keys can steal a chord out from under
the app, so its hotkey silently does nothing:

- **Hammerspoon**, if it's still running and still has the old
  `hs.hotkey.bind` lines for dictate/speak. `install` checks for a
  running Hammerspoon and, if found, tells you to remove the
  `ctrl-alt-cmd-S` and `ctrl-alt-cmd-X` bind lines from
  `~/.hammerspoon/init.lua` and reload Hammerspoon.
- **A Services keyboard shortcut** on the "Dictate with Vocalize" or
  "Stop Vocalize" Quick Action. `vocalize integrate claude` installs both
  on purpose, and keeping them is fine — they stay reachable from the
  Services menu. What conflicts is *assigning* ctrl-alt-cmd-D or
  ctrl-alt-cmd-X to one in System Settings › Keyboard › Keyboard
  Shortcuts › Services: the assignment wins and the app's hotkey never
  fires. `install` says so when the Quick Action is present.

Neither check blocks the install; they're warnings, not a hard stop.
