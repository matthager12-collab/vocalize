# Getting started

This guide sets up Vocalize on a Mac with Apple Silicon, one step at a time.
Each step says what to type and what you should see. You can stop after any
step; later steps are optional.

## 1. Install the tools Vocalize needs

Open Terminal and run:

```bash
xcode-select --install
```

This installs Apple's command line tools. If they are already installed, the
command says so. Then install [uv](https://docs.astral.sh/uv/) by following
its instructions. Vocalize uses uv to install each speech engine.

## 2. Install Vocalize

```bash
uv tool install vocalize-cli
vocalize --version
```

You should see a version number.

## 3. Install a voice

```bash
vocalize local install
```

This downloads the Kokoro voice (337 MB). It asks first and checks every
file before keeping it. Then try it:

```bash
vocalize speak "Vocalize is working."
```

You should hear the sentence. If Kokoro is not ready, the macOS `say` voice
speaks instead and Vocalize tells you so.

## 4. Check the machine

```bash
vocalize doctor
```

This prints one row per check. Each row that is not ready says what command
fixes it.

## 5. Set up dictation (optional)

```bash
vocalize local install --stt
```

On Apple Silicon this downloads the Parakeet speech-to-text model (2.3 GB)
and builds **Vocalize Recorder**, a small app that holds the microphone
permission. Then run:

```bash
vocalize dictate
```

The first time, macOS asks whether Vocalize Recorder may use the
microphone. Click Allow. Speak, then run `vocalize dictate` again to stop.
The text is now on your clipboard. See [dictation.md](dictation.md).

## 6. Install the menu-bar app (optional)

```bash
vocalize app install
```

This adds keyboard shortcuts that work in every app. They do nothing until
you grant Accessibility:

1. Open System Settings › Privacy & Security › Accessibility.
2. Turn on **Vocalize**.
3. Run `vocalize app restart`, then `vocalize app status`. You want to see
   `accessibility: granted`.

Now Control-Option-Command-D starts and stops dictation, and
Control-Option-Command-S speaks the selected text. See [app.md](app.md).

## 7. Change settings

```bash
vocalize portal
```

This opens the settings page in your browser. It runs on your Mac only.
At the top, a machine profile box suggests settings sized for your Mac's
memory; one button applies them. Below it are the sections Speaking,
Reading, Dictation, Transcription, Hotkeys, Engines and Advanced, with a
button for every install and removal.

## 8. Transcribe recordings (optional)

```bash
vocalize transcribe recording.m4a
```

With the default `[stt] engine = "auto"`, files are transcribed by Whisper,
so install it first with `vocalize local install --stt --engine whisper`.
To use Parakeet for files instead, set `[stt] engine = "parakeet"`, or
apply any machine profile other than small. See
[transcribe.md](transcribe.md).

## If something does not work

Run `vocalize doctor` first. For dictation, run `vocalize listen --check`.
For the shortcuts, run `vocalize app status`. Each one says what is missing
and how to fix it. [installation.md](installation.md) has the full detail.
