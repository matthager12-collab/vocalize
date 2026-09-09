# For reviewers

vocalize reads your screen aloud and types what you say, without sending
either one anywhere. It is a macOS command line tool with two small Swift
apps attached, and it is the piece of my own work I point at when someone
asks what I actually build.

This page is for people evaluating the project rather than installing it. If
you want to run it, start at [getting-started.md](getting-started.md).

## The problem worth solving

Screen readers are good at voices and bad at structure.

Point one at a markdown report and it reads a table cell by cell. You get
"Q1. 4.2 million. Q2. 5.1 million" with no sense of which row you are in.
Headings, bullets and inline code get read exactly as typed, backticks and
all.

I find it helpful to listen and read at the same time, so I run a lot of
documents through a reader. That failure costs me time every week. I fixed
the half of it that can be fixed without a vision model.

`vocalize/preprocess.py` rewrites markdown into declarative sentences before
any voice sees it. Tables become "for Q1, revenue is 4.2 million". Bullets
become "First. Second." Links keep their text and drop the URL. Code blocks
become a spoken placeholder instead of a minute of punctuation.

It is a text transform, so it tests without a network or an API key.

## Three decisions I would defend

**Local by default, cloud by choice.** The on-device voice runs first and the
cloud providers sit behind it in an ordered fallback chain. Six providers,
one interface, and a machine with no keys still speaks. I got this backwards
in the first version. ElevenLabs was the primary and everything else was a
fallback, which meant the tool was useless offline and metered by the
character.

**The permission model is the architecture.** macOS will not give a
microphone to a command line tool. So the recorder is a separate signed Swift
bundle whose only job is to be a thing macOS can say yes to. The menu-bar app
exists for the same reason, on the Accessibility side. Neither one reads your
audio or your clipboard. They start a subprocess and get out of the way.

**Notifications are an allowlist, not a format string.** A dictation
transcript must never reach Notification Center, because notifications are
readable by other software and survive on the lock screen. So `_notify()`
refuses any string that is not in a fixed frozen set. There is no code path
that can interpolate a transcript into a notification, because there is no
interpolation at all.

## The bug I want you to read

macOS pins a keychain item to the binary that created it.

Version 0.9 wrote API keys through Python's `keyring` library, which called
the Security framework from whatever Python was running. That Python got
pinned. Upgrade the tool, get a new virtual environment, and every key read
now sits behind a password dialog forever. The tool looks broken and the
error tells you nothing useful.

The fix is in `vocalize/auth.py`. Reads and writes go through Apple's own
`/usr/bin/security` binary instead of the framework. That binary is the same
on every machine and never changes identity, so the pin holds across every
upgrade after this one.

The part I like is the migration. A `-U` update keeps the old pinning, so
`set_password` deletes the item and adds it fresh. The delete is what
migrates it. The comment explaining it runs several times the length of the code,
because the next person to read it will otherwise "simplify" it back into
the bug.

I hit the old version of this bug on my own machine two days ago, on a tool I
wrote, and it still took me twenty minutes to diagnose. The comment is
longer than the code for a reason.

## What I would do differently

The planning documents outnumber the source files. There are 116 of them
under `docs/plans` and `docs/research`, and they are public because the
process is part of what I am showing. A reviewer looking for the code has to
wade past them. If I were starting again I would keep the same discipline and
put it in a separate repository.

I would also pin a minimum macOS version. I never did, and I cannot tell you
today what the real floor is.

## Where to look

| File | Why |
|---|---|
| `vocalize/preprocess.py` | The markdown-to-speech transform. The original idea. |
| `vocalize/auth.py` | The keychain migration above. |
| `vocalize/providers/` | Six providers behind one interface. |
| `vocalize/dictate.py` | The notification allowlist and the session state machine. |
| `vocalize/menubar/VocalizeApp.swift` | The hotkey owner. Two possible backends, one picked at compile time, with the measurement that decided it in the file. |
| `docs/app.md` | Documentation written to be used, including the failure modes. |

## The numbers

One that matters: **24,556 lines of tests against 13,978 lines of code.**

CI runs on Python 3.10, 3.12 and 3.14 and is green on the current release.
It runs on Linux, so it covers the platform-independent half. The macOS
paths are covered by fakes and verified by hand.

Built in the open between 25 August and 9 September 2026. MIT licensed, on
PyPI as `vocalize-cli`.

## What it is not

It is not cross-platform. Dictation depends on AVFoundation, LaunchServices
and the TCC permission database, and there is no equivalent path on Linux or
Windows. The speech half mostly works elsewhere. I have not tested it and I
would not claim it.

It is also not a product. It is a tool I use every day, documented well
enough that someone else can too.
