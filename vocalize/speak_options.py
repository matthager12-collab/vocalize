#!/usr/bin/env python3
"""The asking front-end for GUI contexts (macOS Quick Actions).

Reads text from stdin. When the resolved overflow mode is "ask" and the
text is over the cap, it offers a macOS picker: speak all, speak a
truncated version, or cancel to speak nothing.

It falls back to a plain `vocalize speak-file - --ask-dialog` whenever
anything can't be determined confidently (settings won't parse or the
command fails), so the worst case is vocalize's own dialog ask.

Stdlib only, on purpose: this runs under Apple's system /usr/bin/python3
(no venv, no third-party packages, no vocalize import) as well as the
repo's own interpreter. Everything reaches vocalize through its CLI.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

_SETTINGS_TIMEOUT = 10
_PICKER_TIMEOUT = 40  # choose-from-list has NO "giving up after"; this is the only enforcement


def _read_stdin_text() -> str:
    # Decode from raw bytes once, so a Services environment that doesn't
    # default to UTF-8 can't corrupt or crash the read.
    return sys.stdin.buffer.read().decode("utf-8", errors="replace")


def _resolve_vocalize_bin():
    return os.environ.get("VOCALIZE_BIN") or shutil.which("vocalize")


def _read_settings(vocalize_bin: str):
    """Return (mode, cap) or None. None on ANY deviation — fail closed."""
    try:
        result = subprocess.run(
            [vocalize_bin, "settings"],
            capture_output=True, text=True, timeout=_SETTINGS_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    mode = None
    cap = None
    for line in result.stdout.splitlines():
        if line.startswith("overflow="):
            mode = line[len("overflow="):].strip()
        elif line.startswith("max_chars="):
            raw = line[len("max_chars="):].strip()
            if raw == "unset":
                cap = None
            else:
                try:
                    cap = int(raw)
                except ValueError:
                    return None
    if mode not in ("truncate", "ask", "never"):
        return None
    return mode, cap


def _build_picker_options(cap: int):
    """Ordered [(key, label)]. No thousands-separator comma in labels.

    The picker's own Cancel button is the third choice: it speaks nothing.
    """
    return [("all", "Speak all"), ("truncate", f"Truncate to {cap} characters")]


def _run_picker(input_chars: int, cap: int, options, default_key: str):
    """Return a chosen key, or None to speak nothing (cancel/timeout/error)."""
    labels = [label for _key, label in options]
    label_to_key = {label: key for key, label in options}
    default_label = next(label for key, label in options if key == default_key)
    quoted = ", ".join(f'"{lbl}"' for lbl in labels)
    prompt = f"Input is {input_chars:,} characters; the cap is {cap:,}. What should vocalize do?"
    script = (
        f"choose from list {{{quoted}}} "
        f'with title "vocalize" with prompt "{prompt}" '
        f'default items {{"{default_label}"}} without multiple selections allowed'
    )
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True, text=True, timeout=_PICKER_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None  # timeout kills osascript, dismissing the picker with it
    if result.returncode != 0:
        return None
    chosen = result.stdout.strip()
    if chosen == "false" or not chosen:  # Cancel/Esc yields the literal false
        return None
    return label_to_key.get(chosen)  # unknown label -> None, fail closed


def _speak(vocalize_bin: str, text: str, *, max_chars=None, overflow=None) -> int:
    argv = [vocalize_bin, "speak-file", "-", "--play", "--ask-dialog"]
    if overflow is not None:
        argv += ["--overflow", overflow]
    if max_chars is not None:
        argv += ["--max-chars", str(max_chars)]
    try:
        return subprocess.run(argv, input=text, text=True, check=False).returncode
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"speak_options: could not run vocalize: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    text = _read_stdin_text()

    vocalize_bin = _resolve_vocalize_bin()
    if not vocalize_bin:
        print("speak_options: vocalize binary not found (set VOCALIZE_BIN).", file=sys.stderr)
        return 1

    # Empty input: let vocalize raise its own clean "nothing to speak" error.
    if not text.strip():
        return _speak(vocalize_bin, text)

    settings = _read_settings(vocalize_bin)
    if settings is None:
        # Couldn't read settings — hand off with vocalize's own dialog ask.
        return _speak(vocalize_bin, text)
    mode, cap = settings

    # Fast paths: nothing to ask. Let vocalize resolve its own config.
    if mode != "ask" or cap is None or len(text) <= cap:
        return _speak(vocalize_bin, text)

    options = _build_picker_options(cap)
    key = _run_picker(len(text), cap, options, default_key="truncate")

    if key is None:
        return 0  # cancelled / timed out: speak nothing
    if key == "all":
        return _speak(vocalize_bin, text, overflow="never")
    return _speak(vocalize_bin, text, max_chars=cap, overflow="truncate")  # truncate


if __name__ == "__main__":
    sys.exit(main())
