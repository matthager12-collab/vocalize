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


def _is_ours(hook) -> bool:
    return isinstance(hook, dict) and str(HOOK_SCRIPT) in str(hook.get("command", ""))


def installed() -> bool:
    """Whether the Stop hook is in the settings file. Never exits: a file
    that is missing or not valid JSON simply has no hook in it."""
    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(settings, dict):
        return False
    try:
        return _already_installed(settings)
    except (AttributeError, TypeError):
        return False


def _backup() -> Path:
    backup = SETTINGS_PATH.with_suffix(f".json.bak.{int(time.time())}")
    shutil.copy2(SETTINGS_PATH, backup)
    print(f"Backed up existing settings to {backup}")
    return backup


def remove() -> int:
    """Take out exactly the Stop hook entries `main()` adds — those whose
    command runs this checkout's hook script — and leave every other hook
    and setting as it was. A Stop group left empty by that goes too, and
    so do a `Stop` list and a `hooks` table left empty. Backs the file up
    first, like `main()`."""
    settings = _load_settings()

    if not _already_installed(settings):
        print("vocalize Stop hook is not installed. Nothing to do.")
        return 0

    _backup()
    hooks = settings["hooks"]
    kept_groups = []
    for group in hooks.get("Stop", []):
        entries = group.get("hooks", [])
        remaining = [hook for hook in entries if not _is_ours(hook)]
        if len(remaining) == len(entries):
            kept_groups.append(group)
        elif remaining:
            kept_groups.append({**group, "hooks": remaining})
    if kept_groups:
        hooks["Stop"] = kept_groups
    else:
        del hooks["Stop"]
    if not hooks:
        del settings["hooks"]

    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print(f"Removed the vocalize Stop hook from {SETTINGS_PATH}")
    print("Restart Claude Code (or start a new session) for it to take effect.")
    return 0


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
