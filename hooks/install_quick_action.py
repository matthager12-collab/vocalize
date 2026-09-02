#!/usr/bin/env python3
"""Copy the vocalize Quick Actions into ~/Library/Services/.

Unlike install_hook.py there is no shared settings file to merge into —
each Service is a self-contained bundle, so "install" is: copy the bundle
with this machine's absolute vocalize path baked in. Re-running simply
overwrites the same two bundles.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

TEMPLATES_DIR = Path(__file__).resolve().parent / "quick_actions"
SERVICES_DIR = Path.home() / "Library" / "Services"
BUNDLE_NAMES = (
    "Speak with Vocalize.workflow",
    "Stop Vocalize.workflow",
    "Speak Latest Plan.workflow",
    "Dictate with Vocalize.workflow",
)
PLACEHOLDER = "__VOCALIZE_BIN__"
CLAUDE_PLACEHOLDER = "__CLAUDE_BIN__"
CLAUDE_EXTRA_PATH_PLACEHOLDER = "__CLAUDE_EXTRA_PATH__"
HELPER_PLACEHOLDER = "__HELPER__"
PBS = "/System/Library/CoreServices/pbs"

# Baked values land inside "..." in the Quick Action's zsh script. These
# characters could escape those quotes, so refuse rather than try to quote.
_UNSAFE_PATH_CHARS = set('"\\`$')


def _resolve_vocalize_bin() -> Path:
    repo_venv = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "vocalize"
    if repo_venv.is_file():
        return repo_venv
    on_path = shutil.which("vocalize")
    if on_path:
        return Path(on_path).resolve()
    print(
        "Could not find a vocalize binary (checked .venv/bin/vocalize next to "
        "this repo, then PATH). Install vocalize first.",
        file=sys.stderr,
    )
    sys.exit(1)


def _resolve_helper() -> Path:
    helper = Path(__file__).resolve().parent / "speak_options.py"
    if not helper.is_file():
        print(f"Could not find the picker helper at {helper}.", file=sys.stderr)
        sys.exit(1)
    return helper


def _resolve_claude() -> tuple[str, str]:
    """Return (claude_path, extra_PATH). Empty strings when claude is absent.

    claude.exe may need `node` resolved via PATH even when invoked by its
    absolute path, and a bare Services environment has neither on PATH, so
    bake claude's and node's directories for the helper to prepend.
    """
    found = shutil.which("claude")
    if not found:
        return "", ""
    claude_path = Path(found).resolve()
    dirs = [str(claude_path.parent)]
    node = shutil.which("node")
    if node:
        node_dir = str(Path(node).resolve().parent)
        if node_dir not in dirs:
            dirs.append(node_dir)
    return str(claude_path), os.pathsep.join(dirs)


def _install_one(name: str, substitutions: dict) -> None:
    src = TEMPLATES_DIR / name
    dest = SERVICES_DIR / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)

    wflow = dest / "Contents" / "Resources" / "document.wflow"
    text = wflow.read_text(encoding="utf-8")
    for placeholder, value in substitutions.items():
        text = text.replace(placeholder, xml_escape(value))
    wflow.write_text(text, encoding="utf-8")


def main() -> int:
    bin_path = str(_resolve_vocalize_bin())
    helper_path = str(_resolve_helper())
    claude_path, claude_extra_path = _resolve_claude()

    substitutions = {
        PLACEHOLDER: bin_path,
        HELPER_PLACEHOLDER: helper_path,
        CLAUDE_PLACEHOLDER: claude_path,
        CLAUDE_EXTRA_PATH_PLACEHOLDER: claude_extra_path,
    }
    for value in substitutions.values():
        if _UNSAFE_PATH_CHARS & set(value):
            print(
                f"Refusing to install: a resolved path contains characters that "
                f"would break the Quick Action script: {value!r}",
                file=sys.stderr,
            )
            return 1

    SERVICES_DIR.mkdir(parents=True, exist_ok=True)
    for name in BUNDLE_NAMES:
        _install_one(name, substitutions)
        print(f"Installed {name} -> {SERVICES_DIR / name}")

    # Nudge the Services registry so the new entries appear without a logout.
    subprocess.run([PBS, "-update"], check=False)

    print(f"\nUsing vocalize at: {bin_path}")
    if claude_path:
        print(f"Summaries via claude at: {claude_path}")
    else:
        print("claude not found; Quick Actions will offer speak-all / truncate only.")
    print('Highlight text in any app, then right-click -> Services -> "Speak with Vocalize".')
    print("Keyboard shortcuts: System Settings -> Keyboard -> Keyboard Shortcuts -> Services.")
    print("If the actions don't appear, re-open the Services submenu once, or log out and in.")
    print("Run this installer from a normal terminal (its PATH is what gets baked in).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
