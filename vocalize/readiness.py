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
import subprocess
import threading
from collections.abc import Callable
from typing import NamedTuple

from . import auth, config, ledger
from .exceptions import VocalizeError

# Providers authenticated by a single stored/env API key — see
# auth.PROVIDER_ENV_VARS / PROVIDER_USERNAMES. Polly, say and kokoro each
# get their own probe below.
_CREDENTIAL_PROVIDERS = ("elevenlabs", "openai", "google")


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
    if budget:
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


def _run_probe(name: str, probe: Callable[[], Row], timeout: float) -> Row:
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
        thread = slot.thread

    thread.join(timeout)
    if thread.is_alive():
        return Row(name, "warn", "still checking — a keychain dialog may be waiting", "")
    return slot.row if slot.row is not None else Row(name, "warn", "probe returned nothing", "")


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
    # before `_run_probe`, which takes the same lock for `_inflight`.
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

        probes = list(_PROBES.items())

    return [_run_probe(name, probe, timeout) for name, probe in probes]
