"""Quick Action installer and `vocalize integrate claude` (T-81).

The Quick Action logic moved here from `hooks/install_quick_action.py` (now
a thin wrapper) so `vocalize integrate claude` can call it directly,
alongside the `/speak` skill installer. `hooks/speak_options.py` moved the
same way, to `vocalize/speak_options.py`; its installed path is what gets
baked into the Quick Actions as `__HELPER__`.

Baked paths are the STABLE ones, never resolved: `shutil.which` finds a
symlink (a Homebrew formula's, a `uv tool`'s shim), and a resolved target
(the Cellar or Caskroom path underneath it) breaks the moment that formula
upgrades. `_resolve_vocalize_bin` and `_resolve_claude` return exactly what
`which` found — or, for vocalize, `sys.argv[0]` when it is not on PATH at
all (e.g. a repo `.venv` console script run directly).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

TEMPLATES_DIR = Path(__file__).resolve().parent / "assets" / "quick_actions"
SKILL_SRC = Path(__file__).resolve().parent / "assets" / "claude" / "speak" / "SKILL.md"
SERVICES_DIR = Path.home() / "Library" / "Services"
SKILL_DEST = Path.home() / ".claude" / "skills" / "speak" / "SKILL.md"

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

# The tools `vocalize integrate claude`'s pre-check and the Quick Actions
# depend on: the CLI itself, the summarizer, and what the summarizer needs.
_PATH_TOOLS = ("vocalize", "claude", "node", "python3")


def _resolve_vocalize_bin() -> str:
    """The PATH lookup, unresolved. Falls back to how this process itself
    was invoked when vocalize is not on PATH at all."""
    return shutil.which("vocalize") or sys.argv[0]


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
    dirs = [str(Path(found).parent)]
    node = shutil.which("node")
    if node:
        node_dir = str(Path(node).parent)
        if node_dir not in dirs:
            dirs.append(node_dir)
    return found, os.pathsep.join(dirs)


class RefusedDestination(RuntimeError):
    """A destination under HOME that is not ours to replace: a symlink (whose
    target we would otherwise rewrite or delete) or a plain file where a
    bundle directory belongs."""


def _install_one(name: str, substitutions: dict) -> None:
    src = TEMPLATES_DIR / name
    dest = SERVICES_DIR / name
    if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
        raise RefusedDestination(
            f"Refusing to replace {dest}: it is a symlink or not a directory. "
            "Remove it yourself and rerun."
        )
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)

    wflow = dest / "Contents" / "Resources" / "document.wflow"
    text = wflow.read_text(encoding="utf-8")
    for placeholder, value in substitutions.items():
        text = text.replace(placeholder, xml_escape(value))
    wflow.write_text(text, encoding="utf-8")


def main() -> int:
    """Copy the four Quick Action bundles into ~/Library/Services with this
    machine's vocalize/claude/helper paths baked in. 0 on success, 1 on a
    refusal (an unsafe path)."""
    bin_path = str(_resolve_vocalize_bin())
    helper_path = str(_resolve_helper())
    claude_path, claude_extra_path = _resolve_claude()

    substitutions = {
        PLACEHOLDER: bin_path,
        HELPER_PLACEHOLDER: helper_path,
        CLAUDE_PLACEHOLDER: claude_path,
        CLAUDE_EXTRA_PATH_PLACEHOLDER: claude_extra_path,
    }
    # `sys.argv[0]` is a fallback, not a promise: under `python -m vocalize`
    # it is a non-executable `__main__.py`, and through the legacy
    # `hooks/install_quick_action.py` route it is whatever relative path the
    # user typed, resolved later against the Service's own cwd. Either bakes
    # four Quick Actions that fail their own `[[ -x "$BIN" ]]` guard.
    if not (Path(bin_path).is_absolute() and os.access(bin_path, os.X_OK)):
        print(
            f"Refusing to install: {bin_path!r} is not an executable absolute "
            f"path. Install the console script (uv tool install vocalize-cli) "
            f"and rerun.",
            file=sys.stderr,
        )
        return 1

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
        try:
            _install_one(name, substitutions)
        except RefusedDestination as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Installed {name} -> {SERVICES_DIR / name}")

    # Nudge the Services registry so the new entries appear without a logout.
    try:
        subprocess.run([PBS, "-update"], check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass  # the registry catches up at the next login; the bundles are in place

    print(f"\nUsing vocalize at: {bin_path}")
    if claude_path:
        print(f"Summaries via claude at: {claude_path}")
    else:
        print("claude not found; Quick Actions will offer speak-all / truncate only.")
    print('Highlight text in any app, then right-click -> Services -> "Speak with Vocalize".')
    print("Keyboard shortcuts: System Settings -> Keyboard -> Keyboard Shortcuts -> Services.")
    print("If the actions don't appear, re-open the Services submenu once, or log out and in.")
    return 0


# --- `vocalize integrate claude` --------------------------------------


def _path_precheck() -> dict[str, str]:
    """{tool: absolute path, or "missing"} for the tools the skill and the
    Quick Actions depend on."""
    return {name: (shutil.which(name) or "missing") for name in _PATH_TOOLS}


def _install_skill(*, force: bool) -> str:
    """Write the /speak skill to ~/.claude/skills/speak/SKILL.md.

    "installed": written (nothing was there, or the same content already
    was). "kept": a different file was already there and `force` is False.
    "overwritten": a different file was there and `force` is True.
    """
    SKILL_DEST.parent.mkdir(parents=True, exist_ok=True)
    shipped = SKILL_SRC.read_bytes()
    if SKILL_DEST.is_symlink():
        # Writing through it would rewrite whatever it points at; a link
        # here is the user's arrangement, not ours to replace.
        return "kept"
    if SKILL_DEST.exists():
        if SKILL_DEST.read_bytes() == shipped:
            return "installed"
        if not force:
            return "kept"
        SKILL_DEST.write_bytes(shipped)
        return "overwritten"
    SKILL_DEST.write_bytes(shipped)
    return "installed"


def integrate_claude(*, yes: bool) -> int:
    """`vocalize integrate claude`: PATH pre-check, install the `/speak`
    skill and the four Quick Actions, print the GUI-only steps.

    Returns 1 only on the Quick Action installer's own refusal (an unsafe
    baked path) — every "needs you" item below is reported, not treated as
    a failure, since none of them can be finished non-interactively.
    """
    from . import app as app_module

    print("PATH check:")
    for tool, found in _path_precheck().items():
        print(f"  {tool}: {found}")
    print()

    skill_result = _install_skill(force=yes)
    if skill_result == "kept":
        print(f"Kept existing {SKILL_DEST} (a different file is there — pass --yes to overwrite).")
    elif skill_result == "overwritten":
        print(f"Overwrote {SKILL_DEST} with the shipped /speak skill.")
    else:
        print(f"Installed the /speak skill -> {SKILL_DEST}")
    print("(~/.claude/commands/ is never touched.)")
    print()

    quick_actions_result = main()
    if quick_actions_result != 0:
        return quick_actions_result

    needs_you = ["assign the two keyboard shortcuts below"]
    print()
    print("GUI-only, in System Settings:")
    print(
        "  Keyboard > Keyboard Shortcuts > Services: assign ctrl-alt-cmd-P to "
        '"Speak Latest Plan" and ctrl-alt-cmd-V to "Speak with Vocalize".'
    )
    print(
        "  Do not assign the D or X chords — the menu-bar app owns those; "
        "Dictate and Stop stay reachable from the Services menu."
    )
    if app_module.status_dict()["accessibility"] != "granted":
        print(
            "  Privacy & Security > Accessibility: grant it to Vocalize.app when it "
            "asks (needed for speak-the-selection and paste)."
        )
        needs_you.append("grant Accessibility to Vocalize.app")

    print()
    print("Summary:")
    print(f"  installed: /speak skill ({skill_result}), 4 Quick Actions")
    if skill_result == "kept":
        print("  skipped: /speak skill (rerun with --yes to overwrite the existing file)")
    print(f"  needs you: {'; '.join(needs_you)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
