#!/usr/bin/env python3
"""Claude Code `Stop` hook: speak Claude's last response aloud via vocalize.

Claude Code invokes Stop hooks with a JSON payload on stdin that includes
`transcript_path` — the path to a JSONL file of the conversation so far.
This script pulls the most recent assistant text message out of that
transcript and pipes it through the `vocalize` CLI (the same one used
directly from the command line), so there's exactly one code path for
"turn text into speech" whether you're calling vocalize by hand or Claude
Code is calling it for you after every response.

See hooks/install_hook.py to wire this into ~/.claude/settings.json, or
do it by hand — add to the "Stop" array:

    {
      "matcher": "",
      "hooks": [{"type": "command", "command": "python3 /path/to/claude_stop_hook.py"}]
    }
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

# Keep spoken responses short by default — a Stop hook fires after every
# turn, and a long response would eat the ElevenLabs free-tier quota fast.
# Override with VOCALIZE_MAX_CHARS in the environment.
import os

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
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                last_text_parts.append(block["text"])

        if last_text_parts:
            break  # got the most recent assistant turn; stop scanning

    return "\n".join(last_text_parts)


def main() -> int:
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

    vocalize_bin = shutil.which("vocalize")
    if not vocalize_bin:
        # Silently no-op rather than breaking the user's session if the
        # tool isn't installed / not on PATH in this shell.
        return 0

    max_chars = os.environ.get("VOCALIZE_MAX_CHARS", str(DEFAULT_MAX_CHARS))

    try:
        subprocess.run(
            [vocalize_bin, "speak", text, "--max-chars", max_chars, "--play"],
            timeout=60,
            check=False,
        )
    except Exception:
        # A speech failure should never break the coding session.
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
