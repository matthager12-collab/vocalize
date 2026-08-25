#!/usr/bin/env python3
"""Safely wire claude_stop_hook.py into ~/.claude/settings.json.

Merges a Stop hook entry into the existing settings file rather than
overwriting it, and writes a timestamped backup first. Safe to re-run —
it won't add a duplicate entry if one already points at this hook script.
"""

from __future__ import annotations

import json
import shlex
import shutil
import sys
import time
from pathlib import Path

HOOK_SCRIPT = (Path(__file__).parent / "claude_stop_hook.py").resolve()
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def _load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Refusing to touch {SETTINGS_PATH}: it isn't valid JSON ({exc}).", file=sys.stderr)
        sys.exit(1)


def _already_installed(settings: dict) -> bool:
    for group in settings.get("hooks", {}).get("Stop", []):
        for hook in group.get("hooks", []):
            if str(HOOK_SCRIPT) in hook.get("command", ""):
                return True
    return False


def main() -> int:
    settings = _load_settings()

    if _already_installed(settings):
        print("vocalize Stop hook is already installed. Nothing to do.")
        return 0

    if SETTINGS_PATH.exists():
        backup = SETTINGS_PATH.with_suffix(f".json.bak.{int(time.time())}")
        shutil.copy2(SETTINGS_PATH, backup)
        print(f"Backed up existing settings to {backup}")

    command = f"python3 {shlex.quote(str(HOOK_SCRIPT))}"
    settings.setdefault("hooks", {}).setdefault("Stop", []).append(
        {
            "matcher": "",
            "hooks": [{"type": "command", "command": command}],
        }
    )

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"Installed vocalize Stop hook into {SETTINGS_PATH}")
    print("Restart Claude Code (or start a new session) for it to take effect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
