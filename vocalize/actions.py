"""The configuration choices that are commands, not settings (design.md §
Settings registry): install or remove an engine or a model, install,
restart or remove the menu-bar app, install or remove the Quick Actions and
the Claude Code hook, clear the audio cache, preview a voice, apply a
machine profile, and open the two System Settings panes a person has to
grant by hand.

The settings page offers one button per `Action`. `run()` does the work
through the same functions the command line uses (vocalize/local/install.py,
vocalize/app.py, vocalize/integrate.py, the hook's install_hook.py,
vocalize/cache.py), so a button ends in the state the matching command
would. `state()` and `sources()` are read by the page on every load: they
are cheap, and they never raise.

Every message this module returns or raises is its own fixed wording, the
names of allowlisted engines, models and voices, or an installer's own
message (paths, URLs, a checksum): never text a person typed, and never a
secret.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import platform
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .auth import PROVIDER_LABELS, PROVIDER_NAMES
from .local import (
    kokoro_manifest,
    llm_manifest,
    parakeet_manifest,
    piper_manifest,
    whisper_manifest,
)

GROUP_IDS = ("speaking", "reading", "dictation", "transcription", "hotkeys", "engines",
             "advanced")
KINDS = ("job", "instant")
PARAM_TYPES = ("enum", "str")
SOURCE_NAMES = ("kokoro_voices", "piper_voices", "say_voices", "input_devices",
                "whisper_models", "tts_engines", "stt_engines", "profiles")

#: What `preview_voice` says. Fixed, short, and never the user's text.
PREVIEW_TEXT = "This is how vocalize will sound."

#: The two System Settings panes only a person can grant.
MICROPHONE_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
ACCESSIBILITY_PANE = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
)

#: Where this copy of vocalize is: `hooks_dir()` decides from it alone.
_PACKAGE_DIR = Path(__file__).resolve().parent

_UV_REASON = "needs uv (see https://docs.astral.sh/uv/)"
_ARM_REASON = "needs Apple Silicon"
_MAC_REASON = "needs macOS"
# say's own voice names: what stops one from ever being read as a flag.
_SAY_VOICE = re.compile(r"^[A-Za-z][A-Za-z0-9 ()\-]{0,63}$")
_DEVICE_TIMEOUT = 10
_OPEN_TIMEOUT = 10


class ActionError(Exception):
    """An action could not run. The message says what to do next."""


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    type: str
    choices: tuple | None = None
    choices_from: str | None = None
    required: bool = True


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    help: str
    group: str
    kind: str
    confirm: bool = False
    danger: bool = False
    params: tuple = ()


def _size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB", "MB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _total(entries) -> int:
    return sum(entry["size"] for entry in entries)


_PIPER_LICENCES = "; ".join(
    f"{voice}: {licence}" for voice, licence in piper_manifest.VOICE_LICENCES.items()
)
_PARAKEET_LICENCE = "Weights: NVIDIA parakeet-tdt-0.6b-v2, CC-BY-4.0 (attribution required)."
_WHISPER_SIZES = sorted(entry["size"] for entry in whisper_manifest.FILES)

_VOICE_PARAM = Param("voice", "Voice", "enum", tuple(piper_manifest.VOICES), "piper_voices")
_MODEL_PARAM = Param("model", "Model", "enum", tuple(whisper_manifest.MODELS), "whisper_models")

ACTIONS: tuple[Action, ...] = (
    Action("install_kokoro", "Install Kokoro",
           f"Downloads the Kokoro voice model ({_size(_total(kokoro_manifest.FILES))}) and its "
           "locked runtime (about 230 MB), so text is read aloud on this Mac.",
           "speaking", "job", confirm=True),
    Action("uninstall_kokoro", "Remove Kokoro",
           "Deletes the Kokoro model files and its runtime environment. Your settings stay.",
           "speaking", "job", confirm=True, danger=True),
    Action("install_piper", "Install a Piper voice",
           "Downloads one Piper voice (about 60 MB) and has uv fetch the Piper engine (about "
           "100 MB). Voice licences, both non-commercial: "
           f"{_PIPER_LICENCES}. The engine is GPL-3.0-or-later and runs as a separate program.",
           "speaking", "job", confirm=True, params=(_VOICE_PARAM,)),
    Action("uninstall_piper", "Remove Piper voices",
           "Deletes every downloaded Piper voice. Your settings stay.",
           "speaking", "job", confirm=True, danger=True),
    Action("preview_voice", "Preview voice",
           "Speaks one short sentence with the chosen engine and voice.",
           "speaking", "job",
           params=(Param("engine", "Engine", "enum", tuple(PROVIDER_NAMES), "tts_engines"),
                   Param("voice", "Voice", "str"))),
    Action("install_parakeet", "Install Parakeet",
           "Downloads the Parakeet speech-to-text model "
           f"({_size(_total(parakeet_manifest.FILES))}) and its locked runtime, and builds the "
           f"recorder. {_PARAKEET_LICENCE}",
           "engines", "job", confirm=True),
    Action("uninstall_parakeet", "Remove Parakeet",
           "Deletes the Parakeet model files and its runtime environment. The recorder stays.",
           "engines", "job", confirm=True, danger=True),
    Action("install_whisper", "Install a Whisper model",
           "Downloads one Whisper speech-to-text model (from "
           f"{_size(_WHISPER_SIZES[0])} to {_size(_WHISPER_SIZES[-1])}, by model) and its "
           "locked runtime, and builds the recorder.",
           "engines", "job", confirm=True, params=(_MODEL_PARAM,)),
    Action("uninstall_whisper", "Remove a Whisper model",
           "Deletes the chosen Whisper model. The runtime environment goes with the last model.",
           "engines", "job", confirm=True, danger=True, params=(_MODEL_PARAM,)),
    Action("install_cleanup", "Install the cleanup model",
           "Downloads the local language model that tidies transcripts "
           f"({_size(_total(llm_manifest.FILES))}) and its locked runtime. Needs 12 GB of "
           "memory.",
           "dictation", "job", confirm=True),
    Action("uninstall_cleanup", "Remove the cleanup model",
           "Deletes the cleanup model files and its runtime environment. Your settings stay.",
           "dictation", "job", confirm=True, danger=True),
    Action("stop_warm", "Unload the dictation models",
           "Lets go of the models kept loaded in memory between takes, so the memory is free "
           "again. The next dictation loads them again, and is slower to start.",
           "dictation", "instant"),
    Action("open_microphone_settings", "Open Microphone settings",
           "Opens System Settings at Privacy & Security › Microphone. Only you can grant the "
           "microphone to Vocalize Recorder.",
           "dictation", "instant"),
    Action("app_install", "Install the menu-bar app",
           "Builds Vocalize.app on this Mac and starts it at login, so the hotkeys work "
           "everywhere. A rebuild resets its Accessibility grant.",
           "hotkeys", "job", confirm=True),
    Action("app_restart", "Restart the menu-bar app",
           "Stops the menu-bar app and starts it again, so it reads new hotkeys.",
           "hotkeys", "job"),
    Action("app_uninstall", "Remove the menu-bar app",
           "Stops the menu-bar app and deletes what its install wrote. The Accessibility entry "
           "stays in System Settings.",
           "hotkeys", "job", confirm=True, danger=True),
    Action("open_accessibility_settings", "Open Accessibility settings",
           "Opens System Settings at Privacy & Security › Accessibility. Only you can grant "
           "Accessibility to Vocalize.app.",
           "hotkeys", "instant"),
    Action("integrate_claude", "Install Quick Actions and /speak",
           "Installs the five Quick Actions into the Services menu and the /speak skill for "
           "Claude Code. A different /speak file already there is kept.",
           "advanced", "job"),
    Action("remove_integrations", "Remove Quick Actions and /speak",
           "Removes the five Quick Actions and the shipped /speak skill. Anything else is kept.",
           "advanced", "job", confirm=True, danger=True),
    Action("hook_install", "Install the Claude Code hook",
           "Adds a stop hook to Claude Code's settings so its responses are read aloud. The "
           "settings file is backed up first.",
           "advanced", "instant"),
    Action("hook_remove", "Remove the Claude Code hook",
           "Takes the stop hook out of Claude Code's settings and leaves every other hook. The "
           "settings file is backed up first.",
           "advanced", "instant", confirm=True, danger=True),
    Action("clear_audio_cache", "Clear the audio cache",
           "Deletes the saved audio of text already read aloud. Models and engines are not "
           "touched.",
           "advanced", "job", confirm=True, danger=True),
    Action("apply_profile", "Apply a machine profile",
           "Sets the engine, model and memory settings sized for this kind of Mac. It installs "
           "nothing; it names the installs that profile recommends.",
           "engines", "instant", confirm=True,
           params=(Param("profile", "Profile", "enum", None, "profiles"),)),
)

_BY_ID = {action.id: action for action in ACTIONS}


# --- the schema -------------------------------------------------------


def _profile_names() -> tuple[str, ...]:
    from . import profiles

    return tuple(profiles.names())


def _choices(param: Param) -> tuple | None:
    """A param's choices, read when asked: the profile names come from
    vocalize.profiles, which is only imported here."""
    if param.choices is None and param.choices_from == "profiles":
        return _profile_names()
    return param.choices


def schema() -> list[dict]:
    """Every action and every field, as JSON-ready data."""
    listed = []
    for action in ACTIONS:
        data = asdict(action)
        params = []
        for param in action.params:
            fields = asdict(param)
            try:
                choices = _choices(param)
            except Exception:  # noqa: BLE001 — a schema must load even so
                choices = None
            fields["choices"] = list(choices) if choices is not None else None
            params.append(fields)
        data["params"] = params
        listed.append(data)
    return listed


# --- shared facts -----------------------------------------------------


def _install():
    from .local import install

    return install


def _uv_path() -> str | None:
    from . import local as local_module

    return local_module.uv_path()


def _apple_silicon() -> bool:
    return parakeet_manifest.supported()


def _on_mac() -> bool:
    return platform.system() == "Darwin"


def _ram_bytes() -> int | None:
    from . import local as local_module

    return local_module.physical_ram_bytes()


def _app():
    from . import app

    return app


def _integrate():
    from . import integrate

    return integrate


_hook_cache: dict = {}


def _is_vocalize_checkout(folder: Path) -> bool:
    """Whether `folder` is a source checkout of this project, by its own
    pyproject.toml. An installed package's parent is site-packages, which
    has none."""
    try:
        text = (folder / "pyproject.toml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return re.search(r'^name\s*=\s*"vocalize-cli"\s*$', text, re.MULTILINE) is not None


def hooks_dir(package_dir: Path | None = None) -> Path | None:
    """The folder holding the Claude Code hook scripts, or None.

    Decided by where the vocalize package is, never by searching: an
    installed wheel carries them at `vocalize/hooks/` (pyproject.toml's
    force-include), and a source checkout keeps them in the repository's
    `hooks/` beside the package. A `hooks/` folder beside an installed
    package is in site-packages and could belong to any other
    distribution, so it is used only when that parent is this project's
    checkout.
    """
    package = _PACKAGE_DIR if package_dir is None else package_dir
    packaged = package / "hooks"
    if (packaged / "install_hook.py").is_file():
        return packaged
    checkout = package.parent
    if (checkout / "hooks" / "install_hook.py").is_file() and _is_vocalize_checkout(checkout):
        return checkout / "hooks"
    return None


def _hook_module():
    """install_hook.py from `hooks_dir()`. The installer writes the path of
    the stop hook beside it into Claude Code's settings, so a wheel's hook
    runs the wheel's own script."""
    if "module" in _hook_cache:
        return _hook_cache["module"]
    folder = hooks_dir()
    if folder is None:
        raise ActionError(
            "This copy of vocalize does not include the Claude Code hook scripts. "
            "Reinstall vocalize to get them."
        )
    spec = importlib.util.spec_from_file_location("vocalize_install_hook", folder / "install_hook.py")
    if spec is None or spec.loader is None:
        raise ActionError("The Claude Code hook installer in this copy of vocalize could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _hook_cache["module"] = module
    return module


def _audio_cache_dir() -> Path:
    from . import tts

    return tts.DEFAULT_CACHE_DIR


def _run_open(url: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["open", url], capture_output=True, text=True,
                              timeout=_OPEN_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


@contextlib.contextmanager
def _quiet():
    """The command line's installers print to a terminal; a page has none."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


@contextlib.contextmanager
def _install_errors():
    install = _install()
    try:
        yield
    except install.InstallError as exc:
        raise ActionError(str(exc).strip().splitlines()[0] if str(exc).strip()
                          else "The install failed.") from None


def _no_progress(_fraction, _message) -> None:
    return None


# --- installs ---------------------------------------------------------


def _require_uv() -> str:
    uv = _uv_path()
    if uv is None:
        raise ActionError(
            "uv is not installed, and the on-device runtime needs it. Install it from "
            "https://docs.astral.sh/uv/ and try again. Nothing was downloaded."
        )
    return uv


def _download(manifest, entries, progress) -> None:
    """Fetch each entry not already verified, reporting the fraction of the
    whole set that is on disk."""
    install = _install()
    total = _total(entries) or 1
    done = 0
    for entry in entries:
        progress(done / total, f"Checking {entry['name']}")
        if not install.file_is_verified(entry, manifest=manifest):
            progress(done / total, f"Downloading {entry['name']}")

            def report(written, _expected, already=done, name=entry["name"]):
                progress(min(1.0, (already + written) / total), f"Downloading {name}")

            install.download_file(
                entry["url"], manifest.MODEL_DIR / entry["name"], entry["size"],
                entry["sha256"], opener=install._default_opener, progress=report,
            )
        done += entry["size"]
        progress(done / total, f"Verified {entry['name']}")


def _recorder(progress) -> str:
    install = _install()
    progress(None, "Building the recorder")
    status, _bundle = install.build_recorder()
    return f" {install.REGRANT_WARNING}." if status == "rebuilt" else ""


def _install_kokoro(_params, progress) -> str:
    install, manifest = _install(), kokoro_manifest
    uv = _require_uv()
    with _install_errors():
        if install.installed(manifest)[0]:
            return "Kokoro is already installed."
        _download(manifest, manifest.FILES, progress)
        install.write_stamp()
        progress(None, "Warming the runtime")
        install.selftest(uv)
    return "Kokoro is installed."


def _install_piper(params, progress) -> str:
    install, manifest = _install(), piper_manifest
    voice = params["voice"]
    uv = _require_uv()
    entries = manifest.voice_files(voice)
    with _install_errors():
        if install.installed(manifest, files=entries)[0]:
            return f"Piper voice {voice} is already installed."
        _download(manifest, entries, progress)
        install.write_stamp(manifest=manifest, files=entries)
        progress(None, "Warming the runtime")
        install.selftest(uv, manifest=manifest)
    return f"Piper voice {voice} is installed."


def _install_parakeet(_params, progress) -> str:
    install, manifest = _install(), parakeet_manifest
    if not manifest.supported():
        raise ActionError("Parakeet runs on Apple Silicon only. Install a Whisper model instead.")
    uv = _require_uv()
    with _install_errors():
        if not install.installed(manifest)[0]:
            _download(manifest, manifest.FILES, progress)
            install.write_stamp(manifest=manifest)
        progress(None, "Warming the runtime")
        install.selftest(uv, manifest=manifest)
        note = _recorder(progress)
    return "Parakeet is installed." + note


def _install_whisper(params, progress) -> str:
    install, manifest = _install(), whisper_manifest
    model = params["model"]
    if not _apple_silicon():
        raise ActionError("The Whisper runtime is locked for Apple Silicon only.")
    uv = _require_uv()
    entry = manifest.file_for(model)
    with _install_errors():
        if not install.installed(manifest, files=[entry])[0]:
            _download(manifest, [entry], progress)
            install.write_stamp(manifest=manifest, files=[entry])
        progress(None, "Warming the runtime")
        install.selftest(uv, manifest=manifest, model=model)
        note = _recorder(progress)
    return f"Whisper {model} is installed." + note


def _install_cleanup(_params, progress) -> str:
    install, manifest = _install(), llm_manifest
    if not _apple_silicon():
        raise ActionError("The cleanup model runs on Apple Silicon only.")
    ram = _ram_bytes()
    if ram is not None and ram < manifest.MIN_RAM_BYTES:
        raise ActionError(
            f"This Mac has {_size(ram)} of memory; the cleanup model needs at least "
            f"{_size(manifest.MIN_RAM_BYTES)}. Nothing was downloaded."
        )
    uv = _require_uv()
    with _install_errors():
        if not install.installed(manifest)[0]:
            _download(manifest, manifest.FILES, progress)
            progress(None, "Checking the model's config files")
            try:
                manifest.check_config(manifest.MODEL_DIR)
            except (ValueError, TypeError) as exc:
                raise ActionError(
                    "The downloaded config files failed validation, so the model was not "
                    "marked installed."
                ) from exc
            install.write_stamp(manifest=manifest)
        progress(None, "Warming the runtime")
        install.selftest(uv, manifest=manifest)
    return "The cleanup model is installed. Set cleanup to local to use it."


# --- uninstalls -------------------------------------------------------


def _env_pattern(engine: str):
    return re.compile(rf"^{re.escape(engine)}-(?:[0-9a-f]{{64}}|missing-lock)$")


def _envs(engine: str) -> list[Path]:
    """This engine's locked environments, every lock version: real
    directories named `<engine>-<lock sha256>` directly in the envs folder."""
    from .local import locks

    base = locks.envs_dir()
    if base.is_symlink() or not base.is_dir():
        return []
    pattern = _env_pattern(engine)
    return sorted(
        child for child in base.iterdir()
        if pattern.match(child.name) and child.is_dir() and not child.is_symlink()
    )


def _model_paths(manifest, entries) -> list[Path]:
    base = manifest.MODEL_DIR
    return [base / name for entry in entries for name in (entry["name"], entry["name"] + ".part")]


def _present(paths) -> list[Path]:
    return [path for path in paths if path.is_file() and not path.is_symlink()]


def _remove(paths) -> int:
    removed = 0
    for path in paths:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ActionError(f"Could not remove {path}: {exc.strerror or 'error'}") from None
        removed += 1
    return removed


def _tidy_model_dir(manifest) -> None:
    try:
        manifest.MODEL_DIR.rmdir()  # only when nothing else is in it
    except OSError:
        pass


def _stop_warm(kind: str | None) -> str:
    """Shut down this engine's warm server, if one is running, before its
    files go: a server left running keeps the model loaded from an
    environment that no longer exists. Through the warm client's own stop
    (vocalize/local/warm.py), over the server's private socket. Returns the
    sentence the action's message adds, or "" when none was running."""
    if kind is None:
        return ""
    from .local import warm, warm_protocol

    try:
        # warm.stop looks where the server really is, short fallback
        # folder included, behind the client's own folder checks.
        if not warm.stop(kind):
            return ""
        still = warm_protocol.check_socket_path(warm.socket_path(kind))
    except Exception:  # noqa: BLE001 — the removal goes on; the next take starts afresh
        return ""
    if still:
        return " Its loaded model was told to stop and lets go within moments."
    return " Its loaded model was stopped first."


def _uninstall_engine(manifest, engine: str | None, name: str,
                      warm_kind: str | None = None) -> str:
    """The model files by name, the stamp, and the engine's locked
    environments. Nothing else: the recorder, the app and other engines'
    files stay. An engine with a warm server has it stopped first."""
    install = _install()
    files = _present(_model_paths(manifest, manifest.FILES))
    stamp = install.stamp_path(manifest=manifest)
    stamp_list = [stamp] if stamp.is_file() and not stamp.is_symlink() else []
    envs = _envs(engine) if engine else []
    if not files and not stamp_list and not envs:
        return f"{name} is not installed; nothing was removed."
    note = _stop_warm(warm_kind)
    _remove([*files, *stamp_list, *envs])
    _tidy_model_dir(manifest)
    return f"{name} is removed." + note


def _uninstall_kokoro(_params, _progress) -> str:
    return _uninstall_engine(kokoro_manifest, kokoro_manifest.ENGINE, "Kokoro")


def _uninstall_piper(_params, _progress) -> str:
    # Piper has no locked environment: uv keeps its engine in uv's own cache.
    return _uninstall_engine(piper_manifest, None, "Piper")


def _uninstall_parakeet(_params, _progress) -> str:
    return _uninstall_engine(parakeet_manifest, parakeet_manifest.ENGINE, "Parakeet")


def _uninstall_cleanup(_params, _progress) -> str:
    return _uninstall_engine(llm_manifest, llm_manifest.ENGINE, "The cleanup model",
                             warm_kind="llm")


def _forget_in_stamp(manifest, name: str) -> None:
    """Drop one file's record from the stamp, so a model put back by hand is
    never trusted without a fresh verify. The stamp goes with its last entry."""
    install = _install()
    path = install.stamp_path(manifest=manifest)
    if path.is_symlink() or not path.is_file():
        return
    recorded = install.stamp_files(install.read_stamp(manifest=manifest), manifest)
    recorded.pop(name, None)
    if not recorded:
        path.unlink()
        return
    path.write_text(
        json.dumps({"manifest_version": manifest.MANIFEST_VERSION, "files": recorded}, indent=2)
        + "\n",
        encoding="utf-8",
    )


def _uninstall_whisper(params, _progress) -> str:
    manifest = whisper_manifest
    model = params["model"]
    entry = manifest.file_for(model)
    files = _present(_model_paths(manifest, [entry]))
    others = _present([manifest.MODEL_DIR / e["name"] for e in manifest.FILES
                       if e["name"] != entry["name"]])
    envs = [] if others else _envs(manifest.ENGINE)
    # The warm server may hold this very model, or run from the environment
    # that goes with the last one.
    note = _stop_warm("whisper") if files or envs else ""
    try:
        _forget_in_stamp(manifest, entry["name"])
    except OSError as exc:
        raise ActionError(f"Could not update the Whisper stamp: {exc.strerror or 'error'}") from None
    _remove(files)
    others = _present([manifest.MODEL_DIR / e["name"] for e in manifest.FILES])
    if not others:
        _remove(_envs(manifest.ENGINE))
        _tidy_model_dir(manifest)
    if not files:
        return f"Whisper {model} is not installed; nothing was removed." + note
    return f"Whisper {model} is removed." + note


# --- the menu-bar app -------------------------------------------------


def _app_install(_params, progress) -> str:
    app = _app()
    if not app.discoverable(app.cli_path()):
        raise ActionError(
            "The app looks for vocalize only in ~/.local/bin, /opt/homebrew/bin and "
            "/usr/local/bin, and this vocalize is elsewhere. Run `vocalize app install` in a "
            "terminal for the one-line fix."
        )
    progress(None, "Building the app")
    with _install_errors():
        status, _bundle = app.build()
    note = ""
    if status == "rebuilt":
        app.reset_accessibility()
        try:
            app.status_path().unlink()
        except OSError:
            pass
        note = f" {app.APP_REGRANT_WARNING}."
    progress(None, "Loading the app")
    try:
        app.write_plist()
    except OSError as exc:
        raise ActionError(
            f"Could not write the LaunchAgent {app.plist_path()}: {exc.strerror or 'error'}"
        ) from None
    app.bootout()  # not loaded is the state we want, not an error
    result = app.bootstrap()
    if result is None or result.returncode != 0:
        raise ActionError("Could not load the app with launchctl.")
    return "The menu-bar app is installed." + note


def _app_restart(_params, _progress) -> str:
    result = _app().kickstart()
    if result is None or result.returncode != 0:
        raise ActionError("Could not restart the app with launchctl. Is it installed?")
    return "The menu-bar app restarted."


def _app_files(app) -> list[Path]:
    return [
        app.bundle_path(), app.plist_path(), app.stamp_path(),
        app.status_path(), app.log_path(), app.copied_path(),
    ]


def _app_uninstall(_params, _progress) -> str:
    app = _app()
    # By name, never a directory wholesale, exactly as `vocalize app uninstall`.
    targets = [
        path for path in _app_files(app)
        if (path.is_dir() or path.is_file()) and not path.is_symlink()
    ]
    app.bootout()
    _remove(targets)
    app.forget_binary_override()
    return ("The menu-bar app is removed. Its Accessibility entry stays in System Settings; "
            "remove it there if you want it gone.")


# --- integrations -----------------------------------------------------


def _integrate_claude(_params, _progress) -> str:
    integrate = _integrate()
    try:
        with _quiet():
            code = integrate.integrate_claude(yes=False)
    except SystemExit:
        code = 1
    if code != 0:
        raise ActionError(
            "The Quick Actions were not installed. Run `vocalize integrate claude` in a "
            "terminal to see why."
        )
    kept = (integrate.SKILL_DEST.exists() and integrate.SKILL_DEST
            not in integrate.installed_paths())
    note = " A different /speak skill file was already there and was kept." if kept else ""
    return ("Installed the five Quick Actions and the /speak skill. Assign their shortcuts in "
            "System Settings › Keyboard › Keyboard Shortcuts › Services." + note)


def _remove_integrations(_params, _progress) -> str:
    try:
        removed, kept = _integrate().remove_claude()
    except OSError as exc:
        raise ActionError(f"Could not remove the Quick Actions: {exc.strerror or 'error'}") from None
    if not removed:
        message = "No Quick Actions or /speak skill were installed; nothing was removed."
    else:
        message = f"Removed {len(removed)} item{'s' if len(removed) != 1 else ''}."
    if kept:
        message += f" Kept {len(kept)} that vocalize did not write."
    return message


_SETTINGS_INVALID = (
    "Claude Code's settings.json is not valid JSON, so it was left alone. Fix or remove it "
    "and try again."
)


def _hook(call: str) -> int:
    module = _hook_module()
    try:
        with _quiet():
            return getattr(module, call)()
    except SystemExit:
        raise ActionError(_SETTINGS_INVALID) from None
    except OSError as exc:
        raise ActionError(
            f"Could not update Claude Code's settings: {exc.strerror or 'error'}"
        ) from None


def _hook_install(_params, _progress) -> str:
    _hook("main")
    return "The Claude Code hook is installed. Start a new Claude Code session to use it."


def _hook_remove(_params, _progress) -> str:
    _hook("remove")
    return "The Claude Code hook is removed. Start a new Claude Code session to finish."


# --- cache, preview, profile, permissions -----------------------------


def _clear_audio_cache(_params, progress) -> str:
    from . import cache

    progress(None, "Clearing the audio cache")
    try:
        removed, freed = cache.clear(_audio_cache_dir())
    except OSError:
        raise ActionError(
            "The audio cache folder is redirected or unreadable, so nothing was removed."
        ) from None
    return f"Removed {removed} cached file{'s' if removed != 1 else ''} ({_size(freed)})."


def _preview_voice(params, progress) -> str:
    from . import audio, chain, config
    from .exceptions import VocalizeError

    engine, voice = params["engine"], params["voice"]
    label = PROVIDER_LABELS.get(engine, engine)
    if engine == "kokoro" and voice not in kokoro_manifest.VOICES:
        raise ActionError("That is not a Kokoro voice. Choose one from the list.")
    if engine == "piper" and voice not in piper_manifest.VOICES:
        raise ActionError("That is not a Piper voice. Choose one from the list.")
    if engine == "say" and not _SAY_VOICE.match(voice):
        raise ActionError("That is not a macOS voice name. Choose one from the list.")
    progress(None, f"Speaking with {label}")
    try:
        file_config = config.load_config_file()
        clip, _name, ext = chain.run(
            PREVIEW_TEXT, chain=[engine], file_config=file_config,
            overrides={"voice_id": voice}, cache_dir=_audio_cache_dir(), forced=True,
        )
    except VocalizeError:
        raise ActionError(
            f"{label} could not speak with that voice. Check that it is installed."
        ) from None
    with tempfile.TemporaryDirectory(prefix="vocalize-preview-") as workdir:
        path = Path(workdir) / f"preview.{ext}"
        path.write_bytes(clip)
        audio.play(path)
    return f"Previewed {label}."


def _apply_profile(params, _progress) -> str:
    from . import config, profiles, settings_registry, wizard
    from .exceptions import ConfigChangedError, ConfigError

    name = params["profile"]
    path = config.config_path()
    try:
        fingerprint = wizard.fingerprint_config(path)  # before the read it guards
        data = config.load_config_file()
        updated = settings_registry.apply_changes(data, profiles.changes(name))
        written = wizard.write_config_if_unchanged(path, updated, fingerprint)
    except ConfigChangedError:
        raise ActionError(
            "The settings file changed while the profile was applied. Reload and try again."
        ) from None
    except ConfigError:
        raise ActionError(
            "The settings file has a value vocalize cannot read, so the profile was not "
            "applied. Fix that setting and try again."
        ) from None
    installs = getattr(profiles, "installs", lambda _n: ())(name)
    params = getattr(profiles, "install_params", lambda _n: {})(name)
    labels = [_install_words(_BY_ID[i].label, params.get(i)) for i in installs if i in _BY_ID]
    message = f"Applied the {name} profile."
    if labels:
        message += " It recommends: " + ", ".join(labels) + "."
    backup = getattr(written, "backup", None)
    if backup:
        message += " " + backup_words(backup, getattr(written, "backup_has_retired", False))
    return message


def _install_words(label: str, params) -> str:
    """An install's label, with the values a profile passes it: 'Install a
    Whisper model (large-v3-turbo-q8_0)'. The values are a profile's own
    allowlisted names, never text a person typed."""
    if not isinstance(params, dict):
        return label
    values = [str(value) for value in params.values() if value not in (None, "")]
    return f"{label} ({', '.join(values)})" if values else label


#: Said once more when the backup still holds settings the save removed.
RETIRED_BACKUP_WORDS = (
    "The copy still holds the old settings that were removed, which can include a service "
    "key from an older version, so delete the copy once it is no longer needed."
)


def backup_words(backup, has_retired: bool = False) -> str:
    """What a save that made `config.toml.bak` tells the user, once."""
    words = f"A copy of the old config file, with its comments, was kept at {backup}."
    if has_retired:
        words += " " + RETIRED_BACKUP_WORDS
    return words


#: What each kind of warm server is called on the page.
_WARM_NAMES = {"whisper": "Whisper", "llm": "the cleanup model"}


def _stop_warm_now(_params, _progress) -> str:
    from .local import warm

    stopped = [_WARM_NAMES[kind] for kind, was in warm.stop_all().items() if was]
    if not stopped:
        return "No model was loaded; there was nothing to unload."
    names = " and ".join(stopped)
    if len(stopped) == 1:
        return f"{names[0].upper()}{names[1:]} was unloaded. The next dictation loads it again."
    return f"{names[0].upper()}{names[1:]} were unloaded. The next dictation loads them again."


def _open_pane(url: str, what: str) -> str:
    if not _on_mac():
        raise ActionError("System Settings exists only on macOS.")
    result = _run_open(url)
    if result is None or result.returncode != 0:
        raise ActionError(f"Could not open System Settings. Open Privacy & Security › {what} "
                          "there yourself.")
    return f"Opened System Settings at Privacy & Security › {what}."


def _open_microphone(_params, _progress) -> str:
    return _open_pane(MICROPHONE_PANE, "Microphone")


def _open_accessibility(_params, _progress) -> str:
    return _open_pane(ACCESSIBILITY_PANE, "Accessibility")


_HANDLERS = {
    "install_kokoro": _install_kokoro,
    "uninstall_kokoro": _uninstall_kokoro,
    "install_piper": _install_piper,
    "uninstall_piper": _uninstall_piper,
    "preview_voice": _preview_voice,
    "install_parakeet": _install_parakeet,
    "uninstall_parakeet": _uninstall_parakeet,
    "install_whisper": _install_whisper,
    "uninstall_whisper": _uninstall_whisper,
    "install_cleanup": _install_cleanup,
    "uninstall_cleanup": _uninstall_cleanup,
    "open_microphone_settings": _open_microphone,
    "app_install": _app_install,
    "app_restart": _app_restart,
    "app_uninstall": _app_uninstall,
    "open_accessibility_settings": _open_accessibility,
    "integrate_claude": _integrate_claude,
    "remove_integrations": _remove_integrations,
    "hook_install": _hook_install,
    "hook_remove": _hook_remove,
    "clear_audio_cache": _clear_audio_cache,
    "apply_profile": _apply_profile,
    "stop_warm": _stop_warm_now,
}


# --- run --------------------------------------------------------------


def _validated(action: Action, params) -> dict:
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ActionError("The action's parameters must be an object of names and values.")
    known = {param.name for param in action.params}
    if any(name not in known for name in params):
        raise ActionError(f"{action.label} was given a parameter it does not take.")
    checked = {}
    for param in action.params:
        value = params.get(param.name)
        if value is None or value == "":
            if param.required:
                raise ActionError(f"Choose a {param.label.lower()} first.")
            continue
        if not isinstance(value, str):
            raise ActionError(f"The {param.label.lower()} must be text.")
        if param.type == "enum":
            choices = _choices(param) or ()
            if value not in choices:
                raise ActionError(
                    f"That {param.label.lower()} is not one of the choices. Pick one from "
                    "the list."
                )
        elif len(value) > 128 or not value.isprintable():
            raise ActionError(f"That {param.label.lower()} is not a usable name.")
        checked[param.name] = value
    return checked


def run(action_id, params, progress=None) -> str:
    """Run one action to the end. Returns a final message; raises
    ActionError with a message a person can act on."""
    action = _BY_ID.get(action_id) if isinstance(action_id, str) else None
    if action is None:
        raise ActionError("There is no such action. Reload the page and try again.")
    checked = _validated(action, params)
    report = progress if callable(progress) else _no_progress
    try:
        return _HANDLERS[action.id](checked, report)
    except ActionError:
        raise
    except OSError as exc:
        raise ActionError(f"{action.label} failed: {exc.strerror or 'a file error'}.") from None
    except Exception:  # noqa: BLE001 — the page needs a sentence, not a traceback
        raise ActionError(
            f"{action.label} failed unexpectedly. The matching vocalize command in a terminal "
            "shows the details."
        ) from None


# --- state ------------------------------------------------------------


def _engine_status(manifest, files=None) -> tuple[bool, bool, str]:
    """(installed, anything on disk, status word) for one manifest."""
    install = _install()
    entries = manifest.FILES if files is None else files
    ready = install.installed(manifest, files=entries)[0]
    present = bool(_present([manifest.MODEL_DIR / e["name"] for e in entries]))
    if ready:
        return True, True, "installed"
    return False, present, "incomplete" if present else "not installed"


def _install_state(status: str, *blockers) -> dict:
    for blocked, reason in blockers:
        if blocked:
            return {"available": False, "reason": reason, "status": status}
    return {"available": True, "reason": "", "status": status}


def _remove_state(status: str, something: bool) -> dict:
    if something:
        return {"available": True, "reason": "", "status": status}
    return {"available": False, "reason": "not installed", "status": status}


def _engines_state(facts) -> dict:
    no_uv = facts["uv"] is None
    no_arm = not facts["arm"]
    out = {}

    _ready, present, status = _engine_status(kokoro_manifest)
    out["install_kokoro"] = _install_state(status, (no_uv, _UV_REASON))
    out["uninstall_kokoro"] = _remove_state(status, present or bool(_envs("kokoro")))

    words, any_piper = [], False
    for voice in piper_manifest.VOICES:
        _r, voice_present, word = _engine_status(piper_manifest, piper_manifest.voice_files(voice))
        any_piper = any_piper or voice_present
        words.append(f"{voice} {word}")
    piper_status = ", ".join(words)
    out["install_piper"] = _install_state(piper_status, (no_uv, _UV_REASON))
    out["uninstall_piper"] = _remove_state(piper_status, any_piper)

    _ready, present, status = _engine_status(parakeet_manifest)
    out["install_parakeet"] = _install_state(status, (not parakeet_manifest.supported(),
                                                      _ARM_REASON), (no_uv, _UV_REASON))
    out["uninstall_parakeet"] = _remove_state(status, present or bool(_envs("parakeet")))

    installed_models, any_whisper = [], False
    for model in whisper_manifest.MODELS:
        ready, present, _w = _engine_status(whisper_manifest, [whisper_manifest.file_for(model)])
        any_whisper = any_whisper or present
        if ready:
            installed_models.append(model)
    whisper_status = ("installed: " + ", ".join(installed_models) if installed_models
                      else "incomplete" if any_whisper else "not installed")
    out["install_whisper"] = _install_state(whisper_status, (no_arm, _ARM_REASON),
                                            (no_uv, _UV_REASON))
    out["uninstall_whisper"] = _remove_state(whisper_status, any_whisper)

    _ready, present, status = _engine_status(llm_manifest)
    ram = facts["ram"]
    low_ram = ram is not None and ram < llm_manifest.MIN_RAM_BYTES
    out["install_cleanup"] = _install_state(
        status, (no_arm, _ARM_REASON),
        (low_ram, f"needs {_size(llm_manifest.MIN_RAM_BYTES)} of memory"),
        (no_uv, _UV_REASON),
    )
    out["uninstall_cleanup"] = _remove_state(status, present or bool(_envs("llm")))
    return out


def _app_state(facts) -> dict:
    app = _app()
    bundle = app.bundle_state()
    loaded = app.plist_path().is_file()
    status = {"current": "installed", "stale": "needs reinstalling"}.get(bundle, "not installed")
    if bundle == "current" and not loaded:
        status = "built, not set to start"
    not_mac = not facts["mac"]
    return {
        "app_install": _install_state(status, (not_mac, _MAC_REASON)),
        "app_restart": _install_state(status, (not_mac, _MAC_REASON),
                                      (not loaded, "not installed")),
        "app_uninstall": _remove_state(
            status, any(path.exists() for path in _app_files(app))),
    }


def _integrations_state(_facts) -> dict:
    integrate = _integrate()
    found = integrate.installed_paths()
    actions = sum(1 for path in found if path != integrate.SKILL_DEST)
    skill = "installed" if integrate.SKILL_DEST in found else "not installed"
    status = f"{actions} of {len(integrate.BUNDLE_NAMES)} Quick Actions, /speak skill {skill}"
    return {
        "integrate_claude": _install_state(status),
        "remove_integrations": _remove_state(status, bool(found)),
    }


def _hook_state(_facts) -> dict:
    try:
        module = _hook_module()
    except ActionError:
        reason = "the hook scripts are missing from this copy of vocalize"
        blocked = {"available": False, "reason": reason, "status": "not available"}
        return {"hook_install": dict(blocked), "hook_remove": dict(blocked)}
    status = "installed" if module.installed() else "not installed"
    return {
        "hook_install": _install_state(status),
        "hook_remove": _remove_state(status, status == "installed"),
    }


def _cache_state(_facts) -> dict:
    from . import cache

    count, size = cache.usage(_audio_cache_dir())
    status = f"{count} file{'s' if count != 1 else ''}, {_size(size)}"
    state = {"available": bool(count), "reason": "" if count else "the cache is empty",
             "status": status}
    return {"clear_audio_cache": state}


def _misc_state(facts) -> dict:
    from . import profiles

    not_mac = not facts["mac"]
    try:
        from . import dictate

        mic = dictate.read_mic_status() or "unknown"
    except Exception:  # noqa: BLE001
        mic = "unknown"
    try:
        accessibility = _app().read_status().get("accessibility", "unknown")
    except Exception:  # noqa: BLE001
        accessibility = "unknown"
    return {
        "preview_voice": _install_state(""),
        "apply_profile": _install_state(f"suggested for this Mac: {profiles.detect()}"),
        "open_microphone_settings": _install_state(f"microphone {mic}",
                                                   (not_mac, _MAC_REASON)),
        "open_accessibility_settings": _install_state(f"accessibility {accessibility}",
                                                      (not_mac, _MAC_REASON)),
    }


def _warm_state(_facts) -> dict:
    """Which warm servers answer now. Asking is a hello over each private
    socket, bounded to 0.1 s, and never keeps a model loaded."""
    from .local import warm

    loaded = [_WARM_NAMES[kind] for kind in warm.KINDS if warm.is_running(kind)]
    if not loaded:
        return {"stop_warm": {"available": False, "reason": "no model is loaded",
                              "status": "nothing loaded"}}
    return {"stop_warm": _install_state("loaded now: " + ", ".join(loaded))}


_STATE_PARTS = (
    ("engines", _engines_state, ("install_kokoro", "uninstall_kokoro", "install_piper",
                                 "uninstall_piper", "install_parakeet", "uninstall_parakeet",
                                 "install_whisper", "uninstall_whisper", "install_cleanup",
                                 "uninstall_cleanup")),
    ("app", _app_state, ("app_install", "app_restart", "app_uninstall")),
    ("integrations", _integrations_state, ("integrate_claude", "remove_integrations")),
    ("hook", _hook_state, ("hook_install", "hook_remove")),
    ("cache", _cache_state, ("clear_audio_cache",)),
    ("warm", _warm_state, ("stop_warm",)),
    ("misc", _misc_state, ("preview_voice", "apply_profile", "open_microphone_settings",
                           "open_accessibility_settings")),
)

_UNKNOWN = {"available": False, "reason": "could not be checked", "status": "unknown"}


def _fact(read, default):
    try:
        return read()
    except Exception:  # noqa: BLE001
        return default


def state() -> dict:
    """{action id: {"available", "reason", "status"}} for every action.
    Reads files, and asks any warm server whether it is there; never raises."""
    facts = {
        "uv": _fact(_uv_path, None),
        "arm": _fact(_apple_silicon, False),
        "mac": _fact(_on_mac, False),
        "ram": _fact(_ram_bytes, None),
    }
    result = {}
    for _name, read, ids in _STATE_PARTS:
        try:
            part = read(facts)
        except Exception:  # noqa: BLE001 — one broken part must not hide the rest
            part = {}
        for action_id in ids:
            result[action_id] = dict(part.get(action_id, _UNKNOWN))
    for action in ACTIONS:
        result.setdefault(action.id, dict(_UNKNOWN))
    return result


# --- sources ----------------------------------------------------------


def _voices(name: str) -> list[dict]:
    from . import providers

    return [{"value": str(v["id"]), "label": str(v["name"])}
            for v in providers.get(name).list_voices()]


def _input_devices() -> list[dict]:
    """The recorder's own device list, as `vocalize listen --list-devices`
    reads it. Only names that can be used as the input device as written."""
    install = _install()
    binary = install.recorder_binary()
    if not binary.is_file() or not install.recorder_is_current():
        return []
    result = subprocess.run([str(binary), "--list-devices"], capture_output=True, text=True,
                            timeout=_DEVICE_TIMEOUT, check=False)
    if result.returncode != 0:
        return []
    names = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    return [{"value": n, "label": n} for n in names if n.isprintable() and len(n) <= 128]


#: The empty value of a Whisper model setting that allows one (the
#: transcription model): no model of its own, so the dictation model is used.
#: The page shows a setting's own `choices`; a source only labels them.
_NO_MODEL_LABEL = "Use the dictation model"


def _whisper_models() -> list[dict]:
    return [{"value": "", "label": _NO_MODEL_LABEL}] + [
        {"value": model, "label": f"{model} ({_size(whisper_manifest.file_for(model)['size'])})"}
        for model in whisper_manifest.MODELS
    ]


def _tts_engines() -> list[dict]:
    return [{"value": name, "label": PROVIDER_LABELS.get(name, name)} for name in PROVIDER_NAMES]


_STT_LABELS = {
    "auto": "Automatic (Parakeet on Apple Silicon when installed, else Whisper)",
    "parakeet": "Parakeet",
    "whisper": "Whisper",
}


def _stt_engines() -> list[dict]:
    from . import config

    return [{"value": name, "label": _STT_LABELS.get(name, name)} for name in config.STT_ENGINES]


def _profiles() -> list[dict]:
    from . import profiles

    listed = []
    for name in profiles.names():
        get = getattr(profiles, "get", None)
        label = get(name).label if get is not None else name
        listed.append({"value": name, "label": label})
    return listed


_SOURCES = {
    "kokoro_voices": lambda: _voices("kokoro"),
    "piper_voices": lambda: _voices("piper"),
    "say_voices": lambda: _voices("say"),
    "input_devices": _input_devices,
    "whisper_models": _whisper_models,
    "tts_engines": _tts_engines,
    "stt_engines": _stt_engines,
    "profiles": _profiles,
}


_NO_RECORDER = (
    "No microphones are listed because the recorder is not built yet. Install a dictation "
    "engine (Install Parakeet or Install a Whisper model) to build it, then press Re-check. "
    "Until then dictation records from the system default microphone."
)
_STALE_RECORDER = (
    "No microphones are listed because the recorder on this Mac was built by another "
    "version of vocalize. Install a dictation engine again to rebuild it, then press "
    "Re-check. Until then the system default microphone is the one to use."
)
_NO_DEVICES = (
    "The recorder listed no microphones. Connect one, then press Re-check; "
    "`vocalize listen --list-devices` shows what macOS can see."
)


def _input_devices_note() -> str:
    install = _install()
    binary = install.recorder_binary()
    if not binary.is_file():
        return _NO_RECORDER
    if not install.recorder_is_current():
        return _STALE_RECORDER
    return _NO_DEVICES


def source_notes(listed: dict | None = None) -> dict:
    """{source name: what to do} for a source that came back empty for a
    reason the page should explain. Today only the microphones: the recorder
    is the only thing that can list them by the name it records from, so
    with no current recorder the list is empty. File checks only; never
    raises."""
    listed = sources() if listed is None else listed
    notes = {}
    if not (listed or {}).get("input_devices"):
        try:
            notes["input_devices"] = _input_devices_note()
        except Exception:  # noqa: BLE001
            notes["input_devices"] = _NO_RECORDER
    return notes


def sources() -> dict:
    """{source name: [{"value", "label"}]}. A source that cannot be listed
    is an empty list; this never raises."""
    listed = {}
    for name, read in _SOURCES.items():
        try:
            listed[name] = list(read())
        except Exception:  # noqa: BLE001 — an empty list is the documented answer
            listed[name] = []
    return listed
