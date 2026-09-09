"""The menu-bar app: build it, load it, ask it how it is (0.13.0).

`Vocalize.app` is a compiled Swift bundle under
`~/Library/Application Support/vocalize`, kept alive by a LaunchAgent at
`~/Library/LaunchAgents/cards.arda.vocalize.app.plist`. This module is
everything the CLI needs to put both there, take both away, and report
what the app is doing without asking the app anything: `vocalize app
install / uninstall / status / restart` are thin wrappers over the
functions below, and `status_dict()` is what readiness and the portal
read.

Three rules this module does not bend:

* **No tool ever runs through a shell.** Every `launchctl`, `tccutil`,
  `pgrep` and `defaults` call goes through `_run()`: an absolute path
  from a module constant, a list argv, `check=False`, captured output
  and a timeout. Nothing here interpolates a path into a command string.
* **`tccutil` runs only on a rebuild.** Resetting the Accessibility grant
  is destructive — the user has to walk back into System Settings — and
  it is only *needed* when the bundle's ad-hoc signature changed, which
  is exactly what `build_bundle` calls "rebuilt". A "built" or "current"
  install must never touch it.
* **`app.status` is untrusted input.** It is written by the app, in the
  user's own cache, at a guessable path (DEC-028): it is read with
  `O_NOFOLLOW`, `O_NONBLOCK` and a regular-file check, capped, and
  parsed word by word against a closed vocabulary. Anything else — a
  value we do not know, a file that is too big, a symlink or a FIFO at
  the path, no file at all — reads as "unknown", never as an answer and
  never as text passed on to a caller.
"""

from __future__ import annotations

import os
import plistlib
import re
import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path

#: The LaunchAgent label, the bundle identifier and the `defaults` domain
#: are one string on purpose: the Accessibility grant is keyed to it, and
#: renaming it orphans both the grant and the plist (DEC-028).
LABEL = "cards.arda.vocalize.app"

APP_DIR = Path.home() / "Library" / "Application Support" / "vocalize"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
CACHE_DIR = Path.home() / ".cache" / "vocalize"
SERVICES_WORKFLOW = Path.home() / "Library" / "Services" / "Dictate with Vocalize.workflow"

#: Where the app looks for the CLI, in its own order (VocalizeApp.swift's
#: `binaryCandidates`). A vocalize somewhere else needs the `VocalizeBinary`
#: override, or the app finds a different one — or none.
BINARY_CANDIDATES = (
    Path.home() / ".local" / "bin" / "vocalize",
    Path("/opt/homebrew/bin/vocalize"),
    Path("/usr/local/bin/vocalize"),
)
BINARY_OVERRIDE_KEY = "VocalizeBinary"

# Absolute paths, and module constants so a test can point them at a fake
# script under tmp_path. Nothing here ever resolves a tool through PATH.
LAUNCHCTL = "/bin/launchctl"
TCCUTIL = "/usr/bin/tccutil"
PGREP = "/usr/bin/pgrep"
DEFAULTS = "/usr/bin/defaults"

_TIMEOUT = 30
#: A liveness read is polled — by the doctor and by the portal, whose own
#: budget is two seconds — so it may not sit on the install budget. A
#: `launchctl list` that has not answered in five seconds reads as
#: "unknown", which is the honest answer anyway.
_LIST_TIMEOUT = 5

#: `build_bundle`'s subprocess seam, so the tests drive the compile and the
#: signature through a fake toolchain (`install.build_bundle`'s own
#: `runner=`). Read at call time, not bound at import.
BUILD_RUNNER = subprocess.run

#: What `app install` must say when the build answers "rebuilt": the new
#: ad-hoc signature is a new identity, `tccutil` has just dropped the old
#: grant, and until the user re-grants it no hotkey fires.
APP_REGRANT_WARNING = (
    "Vocalize.app was rebuilt — its Accessibility grant was reset; re-grant it in "
    "System Settings › Privacy & Security › Accessibility the next time it asks"
)


def _install():
    """Imported inside the functions, like `cli`'s local group: `vocalize
    speak` must never pay for the builder."""
    from .local import install

    return install


# --- paths ------------------------------------------------------------


def bundle_path() -> Path:
    return _install().bundle_path(_install().APP_SPEC, APP_DIR)


def app_binary() -> Path:
    return _install().bundle_binary(_install().APP_SPEC, APP_DIR)


def stamp_path() -> Path:
    return _install().bundle_stamp_path(_install().APP_SPEC, APP_DIR)


def plist_path() -> Path:
    return LAUNCH_AGENTS_DIR / f"{LABEL}.plist"


def status_path() -> Path:
    return CACHE_DIR / "app.status"


def log_path() -> Path:
    return CACHE_DIR / "app.log"


def copied_path() -> Path:
    return CACHE_DIR / "dictate.copied"


# --- running the four tools -------------------------------------------


def _run(argv: list[str], timeout: int = _TIMEOUT) -> subprocess.CompletedProcess | None:
    """One door for every tool call. None means the tool did not run at
    all — missing, unexecutable, or out of time — which is never the same
    answer as a tool that ran and said no."""
    try:
        return subprocess.run(
            argv, check=False, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError):
        return None


def gui_domain() -> str:
    return f"gui/{os.getuid()}"


def gui_target() -> str:
    return f"{gui_domain()}/{LABEL}"


def bootout() -> subprocess.CompletedProcess | None:
    """Unload the agent. The caller ignores the result: "it was not
    loaded" is a failure here and is exactly the state we wanted."""
    return _run([LAUNCHCTL, "bootout", gui_target()])


#: launchd tears a booted-out service down asynchronously; a bootstrap that
#: lands inside that window fails with "Bootstrap failed: 5: Input/output
#: error" and succeeds a second later (seen live, 2026-09-08). One retry
#: after a short pause is the whole fix.
_BOOTSTRAP_RETRY_PAUSE = 1.0


def bootstrap() -> subprocess.CompletedProcess | None:
    argv = [LAUNCHCTL, "bootstrap", gui_domain(), str(plist_path())]
    result = _run(argv)
    if result is not None and result.returncode != 0:
        time.sleep(_BOOTSTRAP_RETRY_PAUSE)
        result = _run(argv)
    return result


def bootstrap_command() -> str:
    """The bootstrap argv as the user would type it, for the error that
    names what failed."""
    return f"{LAUNCHCTL} bootstrap {gui_domain()} {plist_path()}"


def kickstart() -> subprocess.CompletedProcess | None:
    return _run([LAUNCHCTL, "kickstart", "-k", gui_target()])


def reset_accessibility() -> subprocess.CompletedProcess | None:
    """Drop the app's Accessibility grant. Only ever called on a rebuild —
    see this module's docstring."""
    return _run([TCCUTIL, "reset", "Accessibility", LABEL])


def forget_binary_override() -> subprocess.CompletedProcess | None:
    return _run([DEFAULTS, "delete", LABEL, BINARY_OVERRIDE_KEY])


def hammerspoon_running() -> bool:
    result = _run([PGREP, "-x", "Hammerspoon"])
    return result is not None and result.returncode == 0


# --- the bundle -------------------------------------------------------


def build() -> tuple[str, Path]:
    """Build the app bundle. Returns `build_bundle`'s (status, path).

    A status of "rebuilt" is the one the caller must act on: the
    signature changed, so the grant is gone whether or not we reset it.
    """
    install = _install()
    return install.build_bundle(install.APP_SPEC, APP_DIR, runner=BUILD_RUNNER)


def bundle_state() -> str:
    """"current" | "stale" | "not built" — read-only, and never a build.

    `status` is asked by readiness, by the portal's poll and by the
    doctor; none of them may compile Swift as a side effect of being
    looked at.
    """
    install = _install()
    try:
        if not bundle_path().is_dir():
            return "not built"
        # The same two facts `build_bundle` decides "current" from: the stamp
        # still describes the binary, and the Info.plist that carries the
        # identity is there. A bundle missing its plist is one the next
        # install rebuilds, so it is "stale" here too.
        plist = bundle_path() / "Contents" / "Info.plist"
        if install.bundle_is_current(install.APP_SPEC, APP_DIR) and plist.is_file():
            return "current"
    except OSError:
        return "not built"
    return "stale"


# --- liveness ---------------------------------------------------------

_PID_LINE = re.compile(r'^\s*(?:"PID"\s*=\s*)?(\d+)\b')
_NO_SERVICE = "could not find service"


def agent_state() -> str:
    """"loaded" | "not running" | "unknown".

    Only two answers are ever asserted: exit 0 with a PID in the output
    is a running agent, and the documented "Could not find service" is a
    plist that is not loaded. Everything else — a launchctl that would
    not run, an exit code we do not recognise, output we cannot parse —
    is "unknown". A parse failure reported as "not running" would send
    the user to reinstall a working app (review R12).
    """
    result = _run([LAUNCHCTL, "list", LABEL], timeout=_LIST_TIMEOUT)
    if result is None:
        return "unknown"
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    if result.returncode == 0:
        if any(_PID_LINE.match(line) for line in (result.stdout or "").splitlines()):
            return "loaded"
        return "unknown"
    return "not running" if _NO_SERVICE in output.lower() else "unknown"


# --- app.status, parsed as untrusted words ----------------------------

_STATUS_MAX = 64 * 1024
_STATUS_UNKNOWN = {
    "accessibility": "unknown",
    "hotkeys": "unknown",
    "hotkey_backend": "unknown",
    "vocalize": "unknown",
}
_STATUS_WORDS = {
    "accessibility": ("granted", "not granted"),
    "hotkey_backend": ("carbon", "monitor"),
}
# `hotkeys: failed:<name>[:taken]`, one name from the app's own three
# actions plus the "settings not read yet" placeholder it writes at launch.
_HOTKEY_FAILED = re.compile(r"^failed:(dictate|speak|stop|settings)(:taken)?$")
_MAX_PATH = 512


def _status_text() -> str:
    """The file's bytes, or "" for anything we will not read: no file, a
    symlink or a FIFO at the path, an unreadable file, or one far bigger
    than the four lines the app writes.

    `O_NOFOLLOW` refuses a symlink but not a FIFO or a device node, and a
    read-only open of a FIFO waits for a writer for ever: `O_NONBLOCK`
    makes the open return and the `S_ISREG` check makes the read
    terminate. A status file nobody may read must never hang the doctor
    or the portal's poll.
    """
    try:
        fd = os.open(status_path(), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return ""
            raw = handle.read(_STATUS_MAX + 1)
    except OSError:
        return ""
    if len(raw) > _STATUS_MAX:
        return ""
    return raw.decode("utf-8", "replace")


def _checked_value(key: str, value: str) -> str:
    if key in _STATUS_WORDS:
        return value if value in _STATUS_WORDS[key] else "unknown"
    if key == "hotkeys":
        return value if value == "ok" or _HOTKEY_FAILED.match(value) else "unknown"
    # `vocalize`: the path the app resolved, or "none". Never trusted as a
    # path to run — this is only ever printed and compared — but a value
    # that is not one is still not an answer.
    if value == "none":
        return value
    if (
        value.startswith("/")
        and len(value) <= _MAX_PATH
        and value.isprintable()
    ):
        return value
    return "unknown"


def read_status() -> dict[str, str]:
    """The four lines the app writes, or "unknown" for each of them.

    Every value is checked against the vocabulary the Swift writer uses;
    a line we do not know is dropped, and a value we do not know reads as
    "unknown" rather than reaching a caller as text.
    """
    values = dict(_STATUS_UNKNOWN)
    for line in _status_text().splitlines():
        key, sep, value = line.partition(":")
        if not sep or key not in values:
            continue
        values[key] = _checked_value(key, value.strip())
    return values


def status_dict() -> dict[str, str]:
    """Everything `app status --json`, the doctor rows and the portal
    read: what is on disk, what launchctl says, and the app's own four
    lines."""
    return {"bundle": bundle_state(), "agent": agent_state(), **read_status()}


def status_lines() -> list[str]:
    return [f"{key}: {value}" for key, value in status_dict().items()]


# --- the plist --------------------------------------------------------


def write_plist() -> Path:
    """Write the LaunchAgent, 0644, with the five keys the design names.

    `O_NOFOLLOW` because `~/Library/LaunchAgents` is a directory anything
    running as this user can write to: a symlink planted at our own name
    would otherwise make this truncate whatever it points at. The
    explicit `chmod` is not redundant — `O_CREAT`'s mode is masked by the
    umask, and an existing file keeps the mode it already had.
    """
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "Label": LABEL,
        "ProgramArguments": [str(app_binary())],
        "RunAtLoad": True,
        # The app is a menu-bar process the user can Quit from its own
        # menu, and Quit has to stay quit: relaunching only on a crash is
        # `SuccessfulExit: false`.
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
    }
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as handle:
        plistlib.dump(data, handle)
    os.chmod(path, 0o644)
    return path


# --- binary discovery -------------------------------------------------


def cli_path() -> Path:
    """The vocalize the user just ran, through any symlinks."""
    return Path(sys.argv[0]).resolve()


def discoverable(path: Path) -> bool:
    """Whether the app, looking only where it looks, would find `path`."""
    for candidate in BINARY_CANDIDATES:
        try:
            if candidate.resolve() == path:
                return True
        except OSError:
            continue
    return False


def override_command(path: Path) -> str:
    """The one line that points the app at a vocalize it cannot find,
    quoted for the shell it will be pasted into."""
    return f"defaults write {LABEL} {BINARY_OVERRIDE_KEY} {shlex.quote(str(path))}"
