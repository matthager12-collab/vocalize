#!/usr/bin/env python3
"""Speak Claude's last response aloud via vocalize. Two modes:

1. As a Claude Code `Stop` hook. Claude Code invokes Stop hooks with a JSON
   payload on stdin that includes `transcript_path` — the path to a JSONL
   file of the conversation so far — and every response gets spoken.
2. On demand, with `--latest`. Nothing is read from stdin; the script finds
   the most recently written transcript under ~/.claude/projects and speaks
   that response. Run it when you want speech instead of installing the
   hook and getting it after every turn.

Either way it pulls the most recent assistant text message out of the
transcript and pipes it through the `vocalize` CLI (the same one used
directly from the command line), so there's exactly one code path for
"turn text into speech".

See hooks/install_hook.py to wire this into ~/.claude/settings.json, or
do it by hand — add to the "Stop" array:

    {
      "matcher": "",
      "hooks": [{"type": "command", "command": "python3 /path/to/claude_stop_hook.py"}]
    }
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Keep spoken responses short by default — a Stop hook fires after every
# turn, and a long response would eat the ElevenLabs free-tier quota fast.
# Override with VOCALIZE_MAX_CHARS in the environment.
DEFAULT_MAX_CHARS = 500


def _extract_last_assistant_text(transcript_path: str) -> str:
    last_text_parts: list[str] = []
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return ""

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if entry.get("type") != "assistant":
            continue

        message = entry.get("message", {})
        content = message.get("content", [])
        if isinstance(content, str):
            # Older / alternate transcript shapes store the whole message as
            # a plain string rather than a list of content blocks.
            if content:
                last_text_parts.append(content)
        else:
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        last_text_parts.append(text)

        if last_text_parts:
            break  # got the most recent assistant turn; stop scanning

    return "\n".join(last_text_parts)


def _find_latest_transcript(projects_dir=None) -> str | None:
    """Most recently modified Claude Code transcript, or None if there are none.

    Claude Code stores one directory per project under ~/.claude/projects,
    each holding <session-id>.jsonl files, so newest mtime is "the session
    you were just talking to".
    """
    base = Path(projects_dir) if projects_dir else Path.home() / ".claude" / "projects"
    transcripts = list(base.glob("*/*.jsonl"))
    if not transcripts:
        return None
    return str(max(transcripts, key=lambda p: p.stat().st_mtime))


def main() -> int:
    if "--latest" in sys.argv[1:]:
        # On-demand mode: no hook payload on stdin, so don't read it at all.
        transcript_path = _find_latest_transcript()
    else:
        try:
            payload = json.load(sys.stdin)
        except json.JSONDecodeError:
            payload = {}
        transcript_path = payload.get("transcript_path")

    if not transcript_path:
        return 0  # nothing to do — don't block Claude Code on a hook error

    text = _extract_last_assistant_text(transcript_path)
    if not text.strip():
        return 0

    # VOCALIZE_BIN wins over PATH so a venv install still resolves when the
    # hook runs from Claude Code's environment rather than your own shell.
    vocalize_bin = os.environ.get("VOCALIZE_BIN") or shutil.which("vocalize")
    if not vocalize_bin:
        # Silently no-op rather than breaking the user's session if the
        # tool isn't installed / not on PATH in this shell.
        return 0

    max_chars = os.environ.get("VOCALIZE_MAX_CHARS", str(DEFAULT_MAX_CHARS))

    # Options first, then "--", then the text. Click treats any argv token
    # starting with "-" as an option, so a reply that opens with a bullet
    # ("- fixed the parser") or an arrow ("-> next") would otherwise make
    # vocalize exit 2 with "No such option" and speak nothing. The "--"
    # end-of-options separator makes the text unambiguously an argument.
    try:
        result = subprocess.run(
            [vocalize_bin, "speak", "--max-chars", max_chars, "--play", "--", text],
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            print(f"vocalize hook: vocalize exited {result.returncode}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 — must not crash the Stop hook
        # A speech failure should never break the coding session, so this
        # still returns 0 — but it's logged to stderr rather than swallowed
        # silently, so a broken hook is diagnosable.
        print(f"vocalize hook: speech failed: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
