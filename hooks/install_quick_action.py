#!/usr/bin/env python3
"""Copy the vocalize Quick Actions into ~/Library/Services/.

Unlike install_hook.py there is no shared settings file to merge into —
each Service is a self-contained bundle, so "install" is: copy the bundle
with this machine's absolute vocalize path baked in. Re-running simply
overwrites the same two bundles.
"""

from __future__ import annotations

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
)
PLACEHOLDER = "__VOCALIZE_BIN__"
PBS = "/System/Library/CoreServices/pbs"

# The path lands inside BIN="..." in the Quick Action's zsh script. These
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


def _install_one(name: str, bin_path: str) -> None:
    src = TEMPLATES_DIR / name
    dest = SERVICES_DIR / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)

    wflow = dest / "Contents" / "Resources" / "document.wflow"
    text = wflow.read_text(encoding="utf-8")
    wflow.write_text(text.replace(PLACEHOLDER, xml_escape(bin_path)), encoding="utf-8")


def main() -> int:
    bin_path = str(_resolve_vocalize_bin())
    if _UNSAFE_PATH_CHARS & set(bin_path):
        print(
            f"Refusing to install: the resolved vocalize path contains characters "
            f"that would break the Quick Action script: {bin_path!r}",
            file=sys.stderr,
        )
        return 1

    SERVICES_DIR.mkdir(parents=True, exist_ok=True)
    for name in BUNDLE_NAMES:
        _install_one(name, bin_path)
        print(f"Installed {name} -> {SERVICES_DIR / name}")

    # Nudge the Services registry so the new entries appear without a logout.
    subprocess.run([PBS, "-update"], check=False)

    print(f"\nUsing vocalize at: {bin_path}")
    print('Highlight text in any app, then right-click -> Services -> "Speak with Vocalize".')
    print("Keyboard shortcuts: System Settings -> Keyboard -> Keyboard Shortcuts -> Services.")
    print("If the actions don't appear, re-open the Services submenu once, or log out and in.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
