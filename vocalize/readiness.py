"""Readiness aggregation: one row per provider chain link, never hanging.

`vocalize status` (and, in 0.11.0, the config portal polling it) needs to
know at a glance whether each configured provider can actually be used
right now. Some checks — a keychain read chief among them — can block on a
macOS permission dialog, so every probe runs on its own daemon thread,
joined with a timeout. A probe still stuck when the timeout elapses gets a
"still checking" row instead of hanging the caller.

Daemon threads, not a ThreadPoolExecutor: a pool's workers are joined at
interpreter exit, which would hang `vocalize status` forever on a wedged
keychain call. A plain daemon thread lets the process exit with the probe
still running.

A module-level registry keeps at most one in-flight thread per row name:
a later `readiness()` call (the portal polls this) reuses the still-running
probe instead of starting another one, so a wedged native call leaks one
thread total, never one per poll.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from . import app, auth, config, ledger
from .exceptions import VocalizeError

# Providers authenticated by a single stored/env API key — see
# auth.PROVIDER_ENV_VARS / PROVIDER_USERNAMES. Polly, say and kokoro each
# get their own probe below.
_CREDENTIAL_PROVIDERS = ("elevenlabs", "openai", "google")

#: The detail on the row a probe that is still running gets. Named because
#: callers have to tell "no answer yet" from "the probe raised" — see
#: portal._key_info.
STILL_CHECKING = "still checking — a slow probe, or a keychain dialog, may be waiting"


class Row(NamedTuple):
    name: str
    state: str  # "ok" | "warn" | "fail"
    detail: str
    action: str


class _Slot:
    """One row name's in-flight state: at most one thread at a time."""

    __slots__ = ("row", "thread")

    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.row: Row | None = None


# name -> zero-arg probe. Rebuilt for the current chain's providers on every
# call, but never cleared — a name registered by hand (a test, or a future
# caller) stays registered and keeps running alongside the chain's own rows.
# Deliberately not validated against auth.PROVIDER_NAMES: this is a plain
# name -> callable seam, not a provider registry.
_PROBES: dict[str, Callable[[], Row]] = {}

# name -> in-flight thread + result, guarded by _lock so two overlapping
# readiness() calls never start two threads for the same name.
_inflight: dict[str, _Slot] = {}
_lock = threading.Lock()


def _credential_row(name: str, file_config: dict) -> Row:
    source = auth.key_source(None, name)
    if source == "not found":
        return Row(
            name, "fail", "no API key configured",
            f"run: vocalize auth login --provider {name}",
        )

    detail = f"key from {source}"
    budget = config.budget_for(name, file_config)
    if budget is not None:
        used, exhausted = ledger.status(name)
        detail += f"; {used:,}/{budget:,} characters this month"
        if exhausted or used >= budget:
            return Row(
                name, "warn", f"{detail} (budget exhausted)",
                "raise monthly_chars in config, or wait for next month",
            )
    return Row(name, "ok", detail, "")


def _polly_row(file_config: dict) -> Row:
    profile = (
        config.provider_table("polly", file_config).get("profile")
        or os.environ.get("AWS_PROFILE")
        or "default"
    )
    status = auth.polly_credential_status(profile)
    if status == "not configured":
        return Row(
            "polly", "fail", "no AWS credentials found",
            "set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, or configure ~/.aws/credentials",
        )
    return Row("polly", "ok", f"credentials from {status}", "")


def _kokoro_row() -> Row:
    from .providers import kokoro as kokoro_provider  # lazy import, no cycle risk

    ready, reason = kokoro_provider.installed()
    if ready:
        return Row("kokoro", "ok", "installed and ready", "")
    return Row("kokoro", "warn", reason, "vocalize local install")


# --- dictation rows ---------------------------------------------------
#
# Four things have to be true before a hotkey press can produce text: a
# model on disk, a built recorder, a microphone grant, and an input device
# that exists. They are reported only once dictation is set up at all —
# on a machine that never opted in they would be four permanent failures
# for a feature nobody asked for.
#
# None of them launches the recorder *app*. The microphone grant is read
# from the file `vocalize listen --check` leaves behind, because measuring
# it for real means going through LaunchServices (DEC-010) — too heavy for
# a status screen, and far too heavy for the portal polling one.

STT_ROW_NAMES = ("stt model", "recorder", "microphone", "input device")

_DEVICE_LIST_TIMEOUT = 1.5
# Past this, an "authorized" verdict is reported but no longer trusted: the
# grant can be revoked in System Settings at any time and nothing tells us.
MIC_STATUS_MAX_AGE = 24 * 60 * 60.0
_STT_INSTALL_ACTION = "vocalize local install --stt"


def _installed_stt_models() -> list[str]:
    from .local import install, whisper_manifest

    return [
        model
        for model in whisper_manifest.MODELS
        if install.installed(
            whisper_manifest,
            files=[whisper_manifest.file_for(model)],
            install_hint=_STT_INSTALL_ACTION,
        )[0]
    ]


def _recorder_is_built() -> bool:
    from .local import install

    return install.recorder_binary().is_file()


def stt_configured(file_config: dict) -> bool:
    """Whether dictation is worth reporting on at all."""
    if file_config.get("stt"):
        return True
    try:
        return _recorder_is_built() or bool(_installed_stt_models())
    except OSError:
        return False


def _stt_model_row() -> Row:
    models = _installed_stt_models()
    if models:
        return Row("stt model", "ok", f"{', '.join(models)} on disk", "")
    return Row("stt model", "fail", "no speech-to-text model installed", _STT_INSTALL_ACTION)


def _recorder_row() -> Row:
    if _recorder_is_built():
        return Row("recorder", "ok", "Vocalize Recorder is built", "")
    return Row("recorder", "fail", "the recorder is not built", _STT_INSTALL_ACTION)


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(int(seconds // 60), 1)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _microphone_row() -> Row:
    """What `listen --check` last measured, and how long ago.

    This row is a *cached* verdict: measuring for real means launching the
    bundle through LaunchServices (DEC-010), which a status screen — and
    the portal polling one — must not do. So it reports "authorized" even
    after the grant has been revoked in System Settings. The age is what
    makes that visible, and past a day the row stops claiming `ok`
    (DEC-014).
    """
    from . import dictate

    word = dictate.read_mic_status()
    age = dictate.mic_status_age()
    if word == "authorized":
        measured = "when last checked" if age is None else f"as of {_ago(age)}"
        detail = f"authorized for Vocalize Recorder ({measured})"
        if age is None or age > MIC_STATUS_MAX_AGE:
            return Row("microphone", "warn", detail, "run: vocalize listen --check")
        return Row("microphone", "ok", detail, "")
    if word == "denied":
        return Row(
            "microphone", "fail", "denied for Vocalize Recorder",
            "allow it in System Settings › Privacy & Security › Microphone",
        )
    if word == "notDetermined":
        return Row(
            "microphone", "warn", "macOS has not asked for it yet",
            "run: vocalize listen --check",
        )
    return Row("microphone", "warn", "unknown", "run: vocalize listen --check")


def _input_devices() -> list[str] | None:
    """The recorder's device list, or None when it could not be asked.

    A plain exec, not a LaunchServices launch: enumerating devices touches
    no permission, so this opens nothing and prompts for nothing.
    """
    from .local import install

    binary = install.recorder_binary()
    if not binary.is_file():
        return None
    try:
        result = subprocess.run(
            [str(binary), "--list-devices"], capture_output=True, text=True,
            timeout=_DEVICE_LIST_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _input_device_row(file_config: dict) -> Row:
    try:
        wanted = config.resolve_stt(file_config)["input_device"]
    except VocalizeError as exc:
        return Row("input device", "fail", str(exc), "fix [stt] input_device in the config file")

    names = _input_devices()
    if names is None:
        return Row(
            "input device", "warn", "not checked — the recorder could not be asked",
            _STT_INSTALL_ACTION,
        )
    if not wanted:
        if names:
            return Row("input device", "ok", f"system default ({len(names)} available)", "")
        return Row(
            "input device", "fail", "macOS reports no input device",
            "connect a microphone, then run: vocalize listen --list-devices",
        )
    if wanted in names:
        return Row("input device", "ok", "the configured device is present", "")
    return Row(
        "input device", "fail", "the configured input device is not connected",
        "pick one from: vocalize listen --list-devices",
    )


# --- app rows -----------------------------------------------------------
#
# The menu-bar app (`vocalize/app.py`) reports on itself: whether the
# bundle is built and current, whether launchd has it loaded, and whether
# it holds the Accessibility grant. All three read `app.status_dict()`,
# which never runs a build or launches anything — a status screen (and
# the portal polling one) may not have side effects.

APP_ROW_NAMES = ("app", "app agent", "accessibility")


def _app_row() -> Row:
    bundle = app.status_dict()["bundle"]
    if bundle == "current":
        return Row("app", "ok", "Vocalize.app is built", "")
    if bundle == "stale":
        return Row("app", "warn", "stale — run: vocalize app install", "")
    return Row("app", "fail", "not built", "vocalize app install")


def _app_agent_row() -> Row:
    status = app.status_dict()
    agent = status["agent"]
    if agent == "loaded":
        return Row("app agent", "ok", "loaded", "")
    if agent == "not running":
        # Not current: a plain restart won't fix a stale/missing bundle,
        # so point at the fuller repair. Current: the bundle is fine, a
        # restart is the narrower fix.
        action = "vocalize app install" if status["bundle"] != "current" else "vocalize app restart"
        return Row("app agent", "fail", "not running", action)
    return Row("app agent", "warn", "unknown", "")


def _accessibility_row() -> Row:
    value = app.status_dict()["accessibility"]
    if value == "granted":
        return Row("accessibility", "ok", "granted", "")
    if value == "not granted":
        return Row(
            "accessibility", "warn", "not granted",
            "grant Accessibility to Vocalize in System Settings",
        )
    return Row("accessibility", "warn", "unknown", "")


# --- doctor-only probes -------------------------------------------------
#
# `vocalize doctor` reports on the machine, not just the configured chain:
# the toolchain, the console script itself, and two things known to fight
# the app for a hotkey. None of these are polled by the portal, so — unlike
# the provider/stt/app rows above — they carry no chain-membership pruning;
# `doctor_rows` below always asks for all of them.

_XCRUN_TIMEOUT = 10
_STARTUP_TIMEOUT = 10
_STARTUP_WARN_MS = 400.0
_UV_INSTALL_HINT = "install it from https://docs.astral.sh/uv/"


def _cli_path_row() -> Row:
    found = shutil.which("vocalize")
    if found is None:
        return Row("cli path", "warn", "vocalize not found on PATH", "")
    path = Path(found)
    if path in app.BINARY_CANDIDATES:
        return Row("cli path", "ok", str(path), "")
    return Row("cli path", "warn", str(path), app.override_command(path))


def _uv_row() -> Row:
    if shutil.which("uv"):
        return Row("uv", "ok", "uv on PATH", "")
    return Row("uv", "fail", "uv not found", _UV_INSTALL_HINT)


def _swiftc_row() -> Row:
    try:
        result = subprocess.run(
            ["xcrun", "--find", "swiftc"], capture_output=True, text=True,
            timeout=_XCRUN_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return Row("swiftc", "fail", "xcrun not found", "xcode-select --install")
    if result.returncode == 0:
        return Row("swiftc", "ok", (result.stdout or "swiftc found").strip(), "")
    return Row("swiftc", "fail", "swiftc not found", "xcode-select --install")


def _claude_row() -> Row:
    if shutil.which("claude"):
        return Row("claude", "ok", "claude on PATH", "")
    return Row(
        "claude", "warn",
        "not on PATH; /speak summaries and claude-cli cleanup unavailable", "",
    )


def _shebang_row() -> Row:
    """Does the running console script's interpreter still exist.

    `uv tool install` bakes an absolute interpreter path into the script's
    `#!` line; a Python upgrade or a pruned toolchain can delete it out
    from under an already-installed script ("brew rot"). Anything that
    isn't a plain `#!/path ...` script — `python -m vocalize`, a frozen
    build, an unreadable file — has no such risk and reads as ok.
    """
    script = Path(sys.argv[0])
    try:
        with open(script, encoding="utf-8", errors="replace") as handle:
            first_line = handle.readline()
    except OSError:
        return Row("shebang", "ok", "not a console script", "")
    if not first_line.startswith("#!"):
        return Row("shebang", "ok", "not a console script", "")
    parts = first_line[2:].strip().split()
    interpreter = parts[0] if parts else ""
    if interpreter and Path(interpreter).exists():
        return Row("shebang", "ok", interpreter, "")
    return Row(
        "shebang", "fail", f"interpreter not found: {interpreter or first_line.strip()}",
        "brew rot: reinstall with uv tool install --reinstall vocalize-cli",
    )


def _hammerspoon_row() -> Row:
    if app.hammerspoon_running():
        return Row(
            "hammerspoon", "warn",
            "Hammerspoon is running — its own hotkeys may conflict",
            "remove any conflicting hs.hotkey.bind(...) call from ~/.hammerspoon/init.lua",
        )
    return Row("hammerspoon", "ok", "not running", "")


def _cli_startup_row() -> Row:
    start = time.monotonic()
    try:
        result = subprocess.run(
            ["vocalize", "--version"], capture_output=True, text=True,
            timeout=_STARTUP_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return Row("cli start-up", "warn", "could not run: vocalize --version", "")
    elapsed_ms = (time.monotonic() - start) * 1000
    detail = f"{elapsed_ms:.0f} ms"
    if result.returncode != 0:
        return Row("cli start-up", "warn", f"vocalize --version exited {result.returncode}", "")
    if elapsed_ms > _STARTUP_WARN_MS:
        return Row("cli start-up", "warn", detail, "")
    return Row("cli start-up", "ok", detail, "")


def _app_bundle_row() -> Row:
    # Same verdict as the "app" row (bundle_state() has one owner), under
    # the name the doctor's toolchain section reports it by.
    row = _app_row()
    return Row("app bundle", row.state, row.detail, row.action)


_ICLOUD_CAVEAT = "notes would sync off this machine via iCloud Drive; keep the notes folder outside it"


def _notes_folder_row(file_config: dict) -> Row:
    folder = Path(config.resolve_notes(file_config)["folder"]).expanduser()
    if folder.exists():
        try:
            total_bytes = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
        except OSError:
            total_bytes = 0
        detail = f"{total_bytes / (1024 * 1024):.1f} MB on disk"
    else:
        detail = "not created yet"

    mobile_documents = Path.home() / "Library" / "Mobile Documents"
    under_icloud = folder == mobile_documents or mobile_documents in folder.parents
    if under_icloud:
        return Row("notes folder", "warn", f"{detail} — {_ICLOUD_CAVEAT}", "")
    return Row("notes folder", "ok", detail, "")


def _make_probe(name: str, file_config: dict) -> Callable[[], Row]:
    if name in _CREDENTIAL_PROVIDERS:
        return lambda: _credential_row(name, file_config)
    if name == "polly":
        return lambda: _polly_row(file_config)
    if name == "kokoro":
        return _kokoro_row
    if name == "say":
        return lambda: Row("say", "ok", "local, no credentials needed", "")
    # Not one of the known provider shapes (a hand-edited or future config).
    # Never raise here — an unknown chain entry is a warning, not a crash.
    return lambda: Row(name, "warn", "no readiness check registered for this provider", "")


def _drop_inflight(name: str) -> None:
    """Forget a row's slot — but never one whose thread is still running.

    Dropping a live slot loses the handle without stopping the thread, so
    the same probe coming back (the portal polls with a changing config)
    starts a *second* one for that name — the exact "one thread total,
    never one per poll" property this registry exists for. Caller holds
    `_lock`.
    """
    slot = _inflight.get(name)
    if slot is None or slot.thread is None or not slot.thread.is_alive():
        _inflight.pop(name, None)


def _start_probe(name: str, probe: Callable[[], Row]) -> tuple[threading.Thread, _Slot]:
    """The in-flight thread for `name`, started if none is running."""
    with _lock:
        slot = _inflight.get(name)
        if slot is None or slot.thread is None or not slot.thread.is_alive():
            slot = _Slot()
            _inflight[name] = slot

            def target() -> None:
                try:
                    slot.row = probe()
                except Exception as exc:  # noqa: BLE001 — a probe must never crash status
                    # Never interpolate str(exc): _PROBES is an open registry
                    # (future portal/dictation probes touch subprocesses and
                    # the network), so an exception message here is untrusted
                    # and may embed credential-shaped text — the same class
                    # of leak auth.scrub() exists to guard against. Report
                    # only the exception's type, never its message.
                    slot.row = Row(name, "warn", f"probe failed: {type(exc).__name__}", "")

            slot.thread = threading.Thread(target=target, daemon=True)
            slot.thread.start()
        return slot.thread, slot


def _join_probe(name: str, thread: threading.Thread, slot: _Slot, timeout: float) -> Row:
    thread.join(max(0.0, timeout))
    if thread.is_alive():
        return Row(name, "warn", STILL_CHECKING, "")
    return slot.row if slot.row is not None else Row(name, "warn", "probe returned nothing", "")


def run_probes(probes: list[tuple[str, Callable[[], Row]]], timeout: float) -> list[Row]:
    """Every probe at once, the whole batch inside one `timeout`.

    All of them are started before any of them is joined, and they share
    one deadline. Joining each in turn instead costs `len(probes) x
    timeout` whenever a keychain dialog is up — and the portal pays that on
    every poll, not once.
    """
    started = [(name, *_start_probe(name, probe)) for name, probe in probes]
    deadline = time.monotonic() + timeout
    return [
        _join_probe(name, thread, slot, deadline - time.monotonic())
        for name, thread, slot in started
    ]


def readiness(file_config: dict, *, timeout: float = 2.0) -> list[Row]:
    """One row per provider in the resolved chain, plus any hand-registered probe.

    Never raises and never blocks longer than `timeout` per row. `file_config`
    is the already-loaded config dict (see config.load_config_file) — this
    function does not read the file itself, so callers (and tests) control
    exactly what it sees.
    """
    try:
        chain = config.resolve_chain(None, file_config)
    except VocalizeError as exc:
        # resolve_chain reads VOCALIZE_CHAIN itself and raises ConfigError on
        # an unrecognized provider name — a config problem, not a crash.
        # readiness() promises never to raise, so degrade to a single row.
        return [
            Row(
                "chain", "fail", f"invalid provider chain: {exc}",
                "fix 'chain' in the config file or the VOCALIZE_CHAIN environment variable",
            )
        ]

    # Drop providers no longer in the chain so a stale file_config from an
    # earlier call (the portal polls this with a changing chain) doesn't
    # keep showing rows for a provider that was removed. Names that aren't
    # real providers (a test's or a future caller's own probe) are a
    # deliberate seam — never pruned.
    # Under the lock, and so is the snapshot: the portal is a
    # ThreadingHTTPServer, and two handler threads mutating `_PROBES` while
    # a third iterates it raises "dictionary changed size during iteration"
    # out of a function whose contract is that it never raises. Released
    # before `_start_probe`, which takes the same lock for `_inflight`.
    with _lock:
        for stale in [n for n in _PROBES if n in auth.PROVIDER_NAMES and n not in chain]:
            del _PROBES[stale]
            _drop_inflight(stale)

        for name in chain:
            _PROBES[name] = _make_probe(name, file_config)

        if stt_configured(file_config):
            _PROBES["stt model"] = _stt_model_row
            _PROBES["recorder"] = _recorder_row
            _PROBES["microphone"] = _microphone_row
            _PROBES["input device"] = lambda: _input_device_row(file_config)
        else:
            # Dictation was never set up (or has been removed): the portal
            # polls this with a changing config, so the rows have to
            # disappear again.
            for name in STT_ROW_NAMES:
                _PROBES.pop(name, None)
                _drop_inflight(name)

        if app.bundle_state() != "not built":
            _PROBES["app"] = _app_row
            _PROBES["app agent"] = _app_agent_row
            _PROBES["accessibility"] = _accessibility_row
        else:
            # No bundle installed: nothing to report on, and a machine
            # that uninstalled the app must not keep showing its rows.
            for name in APP_ROW_NAMES:
                _PROBES.pop(name, None)
                _drop_inflight(name)

        probes = list(_PROBES.items())

    return run_probes(probes, timeout)


def doctor_rows(file_config: dict, *, timeout: float = 2.0) -> list[Row]:
    """Every row `vocalize doctor` prints: a full machine check, not just
    the configured chain.

    Unlike `readiness()`, nothing here is pruned by what is configured —
    every provider in `auth.PROVIDER_NAMES`, every dictation row and every
    app row is asked for regardless, plus the ten toolchain/environment
    checks `status` has no reason to run. Same never-raises, never-hangs-
    past-`timeout` contract, via the same `run_probes`.
    """
    probes: list[tuple[str, Callable[[], Row]]] = [
        (name, _make_probe(name, file_config)) for name in auth.PROVIDER_NAMES
    ]
    probes += [
        ("stt model", _stt_model_row),
        ("recorder", _recorder_row),
        ("microphone", _microphone_row),
        ("input device", lambda: _input_device_row(file_config)),
        ("app", _app_row),
        ("app agent", _app_agent_row),
        ("accessibility", _accessibility_row),
        ("cli path", _cli_path_row),
        ("uv", _uv_row),
        ("swiftc", _swiftc_row),
        ("claude", _claude_row),
        ("shebang", _shebang_row),
        ("hammerspoon", _hammerspoon_row),
        ("cli start-up", _cli_startup_row),
        ("app bundle", _app_bundle_row),
        ("notes folder", lambda: _notes_folder_row(file_config)),
    ]
    return run_probes(probes, timeout)
