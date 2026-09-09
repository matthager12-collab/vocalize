"""`vocalize app` — install, uninstall, status, restart (run 8b, T-72).

Nothing here touches the real machine: `launchctl`, `tccutil`, `pgrep` and
`defaults` are shell scripts under `tmp_path` that log their argv and
answer from a state file, the compiler is a fake runner, and every path
`app.py` knows is pointed somewhere under `tmp_path` before a command
runs. The two things worth breaking a build over are pinned literally:
`tccutil` must appear only when the bundle was rebuilt (it drops the
user's Accessibility grant), and `app uninstall` must remove its own six
files and nothing beside them.
"""

from __future__ import annotations

import json
import os
import plistlib
import stat
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from vocalize import app as app_module
from vocalize.cli import main
from vocalize.local import install as install_module

TOOLS = ("launchctl", "tccutil", "pgrep", "defaults")


class _Toolchain:
    """`build_bundle`'s runner: records the argv, writes a fake binary."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        if Path(argv[0]).name != "codesign":
            target = Path(argv[argv.index("-o") + 1])
            target.write_bytes(b"fake-mach-o")
            target.chmod(0o755)
        return subprocess.CompletedProcess(argv, 0, "", "")


class _Tools:
    """The fake tools' shared log and their answers.

    A call to `<tool> <verb> …` answers from `<tool>-<verb>.{out,err,rc}`
    if that exists, else from `<tool>.{out,err,rc}`, else exit 0 in
    silence.
    """

    def __init__(self, state: Path):
        self.state = state

    def set(self, key: str, *, rc: int | None = None, out: str = "", err: str = "") -> None:
        if rc is not None:
            (self.state / f"{key}.rc").write_text(f"{rc}\n", encoding="utf-8")
        if out:
            (self.state / f"{key}.out").write_text(out, encoding="utf-8")
        if err:
            (self.state / f"{key}.err").write_text(err, encoding="utf-8")

    def argv(self, name: str) -> list[str]:
        path = self.state / f"{name}.argv"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    @property
    def order(self) -> list[str]:
        path = self.state / "order"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def clear(self) -> None:
        for path in [self.state / "order", *(self.state / f"{n}.argv" for n in TOOLS)]:
            path.unlink(missing_ok=True)


@pytest.fixture
def tools(tmp_path, monkeypatch):
    state = tmp_path / "tools"
    state.mkdir()
    for name in TOOLS:
        script = tmp_path / name
        script.write_text(
            "#!/bin/sh\n"
            f'STATE="{state}"\n'
            f'NAME="{name}"\n'
            "{ printf '%s' \"$NAME\"; for a in \"$@\"; do printf ' %s' \"$a\"; done; "
            "printf '\\n'; } >> \"$STATE/order\"\n"
            'for a in "$@"; do printf \'%s\\n\' "$a" >> "$STATE/$NAME.argv"; done\n'
            'KEY="$NAME-$1"\n'
            'if [ ! -f "$STATE/$KEY.rc" ] && [ ! -f "$STATE/$KEY.out" ] '
            '&& [ ! -f "$STATE/$KEY.err" ]; then KEY="$NAME"; fi\n'
            'if [ -f "$STATE/$KEY.out" ]; then cat "$STATE/$KEY.out"; fi\n'
            'if [ -f "$STATE/$KEY.err" ]; then cat "$STATE/$KEY.err" >&2; fi\n'
            'if [ -f "$STATE/$KEY.rc" ]; then exit "$(cat "$STATE/$KEY.rc")"; fi\n'
            "exit 0\n",
            encoding="utf-8",
        )
        script.chmod(0o700)
        monkeypatch.setattr(app_module, name.upper(), str(script))
    fake = _Tools(state)
    fake.set("pgrep", rc=1)  # nothing named Hammerspoon is running
    return fake


@pytest.fixture
def env(tmp_path, monkeypatch, tools):
    """Every path `app.py` knows, under tmp_path — asserted, not assumed."""
    home = tmp_path / "home"
    monkeypatch.setattr(app_module, "APP_DIR", home / "Library/Application Support/vocalize")
    monkeypatch.setattr(app_module, "LAUNCH_AGENTS_DIR", home / "Library/LaunchAgents")
    monkeypatch.setattr(app_module, "CACHE_DIR", home / ".cache/vocalize")
    monkeypatch.setattr(
        app_module, "SERVICES_WORKFLOW",
        home / "Library/Services/Dictate with Vocalize.workflow",
    )
    binary = home / ".local/bin/vocalize"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    monkeypatch.setattr(app_module, "BINARY_CANDIDATES", (binary,))
    monkeypatch.setattr(sys, "argv", [str(binary)])

    # The build compiles a scratch source, never the shipped Swift: this
    # suite must be able to change the source's bytes to force a rebuild.
    source = tmp_path / "VocalizeApp.swift"
    source.write_text("import AppKit\n", encoding="utf-8")
    template = tmp_path / "Info.plist.in"
    template.write_text("<plist/>\n", encoding="utf-8")
    monkeypatch.setattr(
        install_module, "APP_SPEC",
        replace(install_module.APP_SPEC, source=source, plist_template=template),
    )
    toolchain = _Toolchain()
    monkeypatch.setattr(app_module, "BUILD_RUNNER", toolchain)

    for path in (
        app_module.APP_DIR, app_module.LAUNCH_AGENTS_DIR, app_module.CACHE_DIR,
        app_module.SERVICES_WORKFLOW, app_module.bundle_path(), app_module.plist_path(),
        app_module.status_path(), app_module.log_path(), app_module.copied_path(),
        app_module.stamp_path(), Path(app_module.LAUNCHCTL), Path(app_module.TCCUTIL),
        Path(app_module.PGREP), Path(app_module.DEFAULTS), binary, source,
    ):
        assert str(path).startswith(f"{tmp_path}/"), path

    return SimpleNamespace(
        home=home, source=source, binary=binary, toolchain=toolchain, tools=tools,
    )


def invoke(*args):
    return CliRunner().invoke(main, ["app", *args])


def write_status(text: str) -> None:
    path = app_module.status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


GOOD_STATUS = (
    "accessibility: granted\n"
    "vocalize: /Users/mat/.local/bin/vocalize\n"
    "hotkeys: ok\n"
    "hotkey_backend: carbon\n"
)


# --- the spec ---------------------------------------------------------


def test_app_spec_is_the_menu_bar_bundle():
    """The fields, literally: a changed name, framework or stamp version
    is a new signature and an Accessibility re-grant for every user."""
    assert install_module.APP_SPEC == install_module.BundleSpec(
        source=install_module.APP_SOURCE,
        plist_template=install_module.APP_PLIST_TEMPLATE,
        entitlements=None,
        bundle_name="Vocalize.app",
        binary_name="vocalize-app",
        frameworks=("AppKit", "Carbon"),
        stamp_name=".app",
        stamp_version=1,
        noun="app",
    )
    assert install_module.APP_SPEC.source.name == "VocalizeApp.swift"
    assert install_module.APP_SPEC.source.is_file()
    assert install_module.APP_SPEC.plist_template.is_file()
    assert install_module._MENUBAR_DIR.name == "menubar"


# --- install ----------------------------------------------------------


def test_install_writes_the_launch_agent_the_design_names(env):
    result = invoke("install", "--yes")

    assert result.exit_code == 0, result.output
    plist = app_module.plist_path()
    assert plist == env.home / "Library/LaunchAgents/cards.arda.vocalize.app.plist"
    data = plistlib.loads(plist.read_bytes())
    assert data == {
        "Label": "cards.arda.vocalize.app",
        "ProgramArguments": [
            str(env.home / "Library/Application Support/vocalize/Vocalize.app"
                / "Contents/MacOS/vocalize-app")
        ],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
    }
    assert stat.S_IMODE(plist.stat().st_mode) == 0o644


def test_install_writes_the_plist_over_a_tighter_umask(env, monkeypatch):
    """`O_CREAT`'s mode is masked by the umask, so the chmod is the only
    thing that makes this 0644 for launchd on a locked-down machine."""
    previous = os.umask(0o077)
    try:
        assert invoke("install", "--yes").exit_code == 0
    finally:
        os.umask(previous)
    assert stat.S_IMODE(app_module.plist_path().stat().st_mode) == 0o644


def test_install_boots_out_before_it_bootstraps(env):
    invoke("install", "--yes")

    order = env.tools.order
    bootout = next(i for i, line in enumerate(order) if line.startswith("launchctl bootout"))
    bootstrap = next(i for i, line in enumerate(order) if line.startswith("launchctl bootstrap"))
    assert bootout < bootstrap
    uid = f"gui/{os.getuid()}"
    assert order[bootout] == f"launchctl bootout {uid}/cards.arda.vocalize.app"
    assert order[bootstrap] == f"launchctl bootstrap {uid} {app_module.plist_path()}"


def test_a_bootstrap_failure_names_the_command(env):
    env.tools.set("launchctl-bootstrap", rc=5, err="Bootstrap failed: 5: Input/output error")

    result = invoke("install", "--yes")

    assert result.exit_code == 1
    assert "bootstrap" in result.stderr and "Input/output error" in result.stderr


def test_tccutil_runs_only_when_the_bundle_was_rebuilt(env):
    """The one destructive call in the run: it drops the Accessibility
    grant, so a first build and a no-op re-run must never reach it."""
    first = invoke("install", "--yes")
    assert first.exit_code == 0
    assert "built:" in first.output
    assert env.tools.argv("tccutil") == []
    assert app_module.APP_REGRANT_WARNING not in first.output

    env.tools.clear()
    second = invoke("install", "--yes")
    assert "current:" in second.output
    assert env.tools.argv("tccutil") == []
    assert app_module.APP_REGRANT_WARNING not in second.output

    env.tools.clear()
    env.source.write_text("import AppKit  // 0.13.1\n", encoding="utf-8")
    third = invoke("install", "--yes")
    assert "rebuilt:" in third.output
    assert env.tools.argv("tccutil") == ["reset", "Accessibility", "cards.arda.vocalize.app"]
    assert app_module.APP_REGRANT_WARNING in third.output
    # And it is reset before the agent is loaded again.
    order = env.tools.order
    assert order.index("tccutil reset Accessibility cards.arda.vocalize.app") < next(
        i for i, line in enumerate(order) if line.startswith("launchctl bootstrap")
    )


def test_install_refuses_a_vocalize_the_app_cannot_find(env, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere" / "vocalize"
    elsewhere.parent.mkdir()
    elsewhere.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(elsewhere)])

    result = invoke("install")

    assert result.exit_code == 1
    assert (
        f"defaults write cards.arda.vocalize.app VocalizeBinary {elsewhere}"
        in result.output.splitlines()
    )
    assert "will not find this vocalize" in result.output
    # Nothing was built and nothing was loaded.
    assert env.tools.order == []
    assert not app_module.bundle_path().exists()
    assert not app_module.plist_path().exists()


def test_yes_prints_the_same_advice_and_installs_anyway(env, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere" / "vocalize"
    elsewhere.parent.mkdir()
    elsewhere.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(elsewhere)])

    result = invoke("install", "--yes")

    assert result.exit_code == 0
    assert (
        f"defaults write cards.arda.vocalize.app VocalizeBinary {elsewhere}"
        in result.output.splitlines()
    )
    assert app_module.plist_path().is_file()


def test_a_symlinked_vocalize_in_a_candidate_path_is_found(env, tmp_path, monkeypatch):
    """`~/.local/bin/vocalize` is a symlink into a venv on most machines."""
    real = tmp_path / "venv" / "bin" / "vocalize"
    real.parent.mkdir(parents=True)
    real.write_text("#!/bin/sh\n", encoding="utf-8")
    link = env.binary
    link.unlink()
    link.symlink_to(real)
    monkeypatch.setattr(sys, "argv", [str(real)])

    result = invoke("install", "--yes")

    assert result.exit_code == 0
    assert "defaults write" not in result.output


def test_install_warns_about_hammerspoon(env):
    env.tools.set("pgrep", rc=0, out="4242\n")

    result = invoke("install", "--yes")

    assert result.exit_code == 0
    assert env.tools.argv("pgrep") == ["-x", "Hammerspoon"]
    assert "hs.hotkey.bind" in result.stderr
    assert "~/.hammerspoon/init.lua" in result.stderr
    assert "ctrl-alt-cmd-S and ctrl-alt-cmd-X" in result.stderr


def test_install_is_quiet_about_hammerspoon_when_it_is_not_running(env):
    assert "hs.hotkey.bind" not in invoke("install", "--yes").stderr


def test_install_warns_about_a_shortcut_not_about_the_quick_action_itself(env):
    """`integrate claude` installs that Quick Action deliberately, so telling
    the user to delete it contradicts the same release. Only an assigned
    shortcut on the app's chord is a conflict."""
    workflow = app_module.SERVICES_WORKFLOW
    workflow.mkdir(parents=True)

    result = invoke("install", "--yes")

    assert result.exit_code == 0
    assert "Dictate with Vocalize" in result.stderr
    assert "ctrl-alt-cmd-D" in result.stderr
    assert "Remove the workflow" not in result.stderr


def test_install_without_yes_asks_first_and_writes_nothing_on_no(env):
    result = CliRunner().invoke(main, ["app", "install"], input="n\n")

    assert result.exit_code == 1
    assert str(app_module.bundle_path()) in result.output
    assert str(app_module.plist_path()) in result.output
    assert env.tools.order == ["pgrep -x Hammerspoon"]  # the check, nothing else
    assert not app_module.plist_path().exists()


# --- status -----------------------------------------------------------


def test_status_json_before_anything_is_installed(env):
    env.tools.set(
        "launchctl-list", rc=113,
        err="Could not find service “cards.arda.vocalize.app” in domain for login",
    )

    result = invoke("status", "--json")

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "bundle": "not built",
        "agent": "not running",
        "accessibility": "unknown",
        "hotkeys": "unknown",
        "hotkey_backend": "unknown",
        "vocalize": "unknown",
    }


def test_status_json_when_everything_is_up(env):
    invoke("install", "--yes")
    write_status(GOOD_STATUS)
    env.tools.clear()
    env.tools.set("launchctl-list", rc=0, out='{\n\t"PID" = 4242;\n\t"Label" = "x";\n};\n')

    result = invoke("status", "--json")

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "bundle": "current",
        "agent": "loaded",
        "accessibility": "granted",
        "hotkeys": "ok",
        "hotkey_backend": "carbon",
        "vocalize": "/Users/mat/.local/bin/vocalize",
    }
    assert env.tools.argv("launchctl") == ["list", "cards.arda.vocalize.app"]


def test_status_reports_a_bundle_the_source_has_moved_past_as_stale(env):
    invoke("install", "--yes")
    env.source.write_text("import AppKit  // newer\n", encoding="utf-8")

    result = invoke("status")

    assert result.exit_code == 1
    assert "bundle: stale" in result.output


def test_status_never_builds_anything(env):
    """`status` is what readiness and the portal's poll call; compiling
    Swift because someone looked would be a minutes-long surprise."""
    invoke("install", "--yes")
    env.source.write_text("import AppKit  // newer\n", encoding="utf-8")
    env.toolchain.calls.clear()

    invoke("status", "--json")

    assert env.toolchain.calls == []


def test_status_text_prints_the_six_lines_in_order(env):
    result = invoke("status")

    assert [line.split(":")[0] for line in result.output.splitlines()] == [
        "bundle", "agent", "accessibility", "hotkeys", "hotkey_backend", "vocalize",
    ]


@pytest.mark.parametrize(
    "text, expected",
    [
        pytest.param("", {}, id="empty"),
        pytest.param(
            "accessibility: not granted\nvocalize: none\n"
            "hotkeys: failed:dictate:taken\nhotkey_backend: monitor\n",
            {
                "accessibility": "not granted", "vocalize": "none",
                "hotkeys": "failed:dictate:taken", "hotkey_backend": "monitor",
            },
            id="the-other-real-values",
        ),
        pytest.param("hotkeys: failed:speak\n", {"hotkeys": "failed:speak"}, id="one-failure"),
        pytest.param(
            "accessibility: GRANTED\nvocalize: ../../etc/passwd\n"
            "hotkeys: failed:; rm -rf ~\nhotkey_backend: bash\n",
            {},
            id="words-we-do-not-know",
        ),
        pytest.param(
            "accessibility: granted; also granted\nhotkeys: ok ok\n", {}, id="near-misses",
        ),
        pytest.param("hotkeys\naccessibility granted\n", {}, id="no-colon"),
        pytest.param("Accessibility: granted\n", {}, id="another-key"),
        pytest.param("vocalize: /opt/x\x07/vocalize\n", {}, id="control-characters"),
        pytest.param("vocalize: /" + "a" * 600 + "\n", {}, id="an-absurd-path"),
    ],
)
def test_app_status_is_parsed_as_untrusted_words(env, text, expected):
    write_status(text)

    state = app_module.read_status()

    assert state == {
        "accessibility": "unknown", "hotkeys": "unknown",
        "hotkey_backend": "unknown", "vocalize": "unknown",
        **expected,
    }


def test_an_oversized_app_status_reads_as_unknown(env):
    write_status("accessibility: granted\n" + "x" * (2 * 1024 * 1024))

    assert app_module.read_status()["accessibility"] == "unknown"


def test_a_fifo_at_app_status_reads_as_unknown_and_does_not_block(env):
    """`O_NOFOLLOW` refuses a symlink but not a FIFO, and a read-only open
    of one waits for a writer for ever. `status_dict()` is on three hot
    paths — `app status`, three doctor rows, the portal's poll — so a
    read that never returns wedges all three."""
    path = app_module.status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(path)
    read: list[dict[str, str]] = []
    worker = threading.Thread(target=lambda: read.append(app_module.read_status()), daemon=True)

    worker.start()
    worker.join(5)

    assert not worker.is_alive(), "read_status() blocked on the FIFO"
    assert read == [{
        "accessibility": "unknown", "hotkeys": "unknown",
        "hotkey_backend": "unknown", "vocalize": "unknown",
    }]


def test_a_symlinked_app_status_is_not_followed(env, tmp_path):
    elsewhere = tmp_path / "planted.status"
    elsewhere.write_text(GOOD_STATUS, encoding="utf-8")
    path = app_module.status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(elsewhere)

    assert app_module.read_status() == {
        "accessibility": "unknown", "hotkeys": "unknown",
        "hotkey_backend": "unknown", "vocalize": "unknown",
    }


@pytest.mark.parametrize(
    "rc, out, err, expected",
    [
        pytest.param(0, '\t"PID" = 4242;\n', "", "loaded", id="the-plist-form"),
        pytest.param(0, "4242\t0\tcards.arda.vocalize.app\n", "", "loaded", id="the-column-form"),
        pytest.param(
            113, "", 'Could not find service "cards.arda.vocalize.app" in domain',
            "not running", id="not-loaded",
        ),
        pytest.param(0, '{\n\t"LastExitStatus" = 0;\n};\n', "", "unknown", id="loaded-no-pid"),
        pytest.param(0, "who knows\n", "", "unknown", id="unparsable"),
        pytest.param(1, "", "launchctl: something else went wrong", "unknown", id="another-error"),
        pytest.param(64, "", "", "unknown", id="silent-failure"),
    ],
)
def test_launchctl_list_is_read_conservatively(env, rc, out, err, expected):
    """A parse failure is "unknown", never "not running": telling the user
    to reinstall a working app is the worse of the two wrong answers."""
    env.tools.set("launchctl-list", rc=rc, out=out, err=err)

    assert app_module.agent_state() == expected


def test_a_launchctl_that_will_not_run_is_unknown(env, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "LAUNCHCTL", str(tmp_path / "not-there"))

    assert app_module.agent_state() == "unknown"


# --- restart ----------------------------------------------------------


def test_restart_kickstarts_the_agent(env):
    result = invoke("restart")

    assert result.exit_code == 0
    uid = os.getuid()
    assert env.tools.order == [
        f"launchctl kickstart -k gui/{uid}/cards.arda.vocalize.app"
    ]


def test_a_failed_restart_names_the_command(env):
    env.tools.set("launchctl-kickstart", rc=3, err="Could not find service")

    result = invoke("restart")

    assert result.exit_code == 1
    assert "kickstart -k" in result.stderr


# --- uninstall --------------------------------------------------------


def test_uninstall_removes_its_own_files_and_nothing_beside_them(env):
    invoke("install", "--yes")
    write_status(GOOD_STATUS)
    cache = app_module.CACHE_DIR
    app_module.log_path().write_text("stderr\n", encoding="utf-8")
    app_module.copied_path().write_text("{}\n", encoding="utf-8")
    # The neighbours: the recorder's granted bundle, another cache file,
    # and someone else's LaunchAgent.
    recorder = app_module.APP_DIR / "Vocalize Recorder.app"
    recorder.mkdir(parents=True)
    neighbour = cache / "mic.status"
    neighbour.write_text("authorized\n", encoding="utf-8")
    other_agent = app_module.plist_path().with_name("com.example.other.plist")
    other_agent.write_text("<plist/>\n", encoding="utf-8")
    env.tools.clear()

    result = invoke("uninstall", "--yes")

    assert result.exit_code == 0, result.output
    for gone in (
        app_module.bundle_path(), app_module.plist_path(), app_module.stamp_path(),
        app_module.status_path(), app_module.log_path(), app_module.copied_path(),
    ):
        assert not gone.exists(), gone
    for kept in (recorder, neighbour, other_agent, cache, other_agent.parent):
        assert kept.exists(), kept
    assert env.tools.order == [
        f"launchctl bootout gui/{os.getuid()}/cards.arda.vocalize.app",
        "defaults delete cards.arda.vocalize.app VocalizeBinary",
    ]
    assert "Accessibility" in result.output


def test_uninstall_refuses_to_follow_a_symlinked_bundle(env, tmp_path):
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    bundle = app_module.bundle_path()
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.symlink_to(elsewhere)

    result = invoke("uninstall", "--yes")

    assert result.exit_code == 0
    assert "a symlink" in result.output
    assert elsewhere.is_dir() and bundle.is_symlink()


def test_uninstall_with_nothing_installed_still_unloads_and_forgets(env):
    result = invoke("uninstall", "--yes")

    assert result.exit_code == 0
    assert "None of the app's files" in result.output
    assert [line.split()[0] for line in env.tools.order] == ["launchctl", "defaults"]


def test_uninstall_without_yes_asks_first(env):
    invoke("install", "--yes")
    env.tools.clear()

    result = CliRunner().invoke(main, ["app", "uninstall"], input="n\n")

    assert result.exit_code == 1
    assert env.tools.order == []
    assert app_module.plist_path().is_file()


# --- how the tools are run --------------------------------------------


def test_every_tool_call_is_a_list_argv_with_no_shell_and_a_timeout(env, monkeypatch):
    calls = []
    real = subprocess.run

    def spy(argv, **kwargs):
        calls.append((argv, kwargs))
        return real(argv, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "run", spy)
    invoke("install", "--yes")
    invoke("status", "--json")
    invoke("restart")
    invoke("uninstall", "--yes")

    assert [Path(argv[0]).name for argv, _ in calls]
    for argv, kwargs in calls:
        assert isinstance(argv, list)
        assert all(isinstance(part, str) for part in argv)
        assert Path(argv[0]).is_absolute()
        assert kwargs["check"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] in (app_module._LIST_TIMEOUT, app_module._TIMEOUT)
        assert set(kwargs) == {"check", "capture_output", "text", "timeout"}
    # The fakes' own logs agree: one argument per line, so nothing was
    # handed to a shell as one string.
    assert env.tools.argv("launchctl")[:2] == [
        "bootout", f"gui/{os.getuid()}/cards.arda.vocalize.app",
    ]


def test_the_liveness_read_is_bounded_by_the_poll_budget_not_the_install_one(env, monkeypatch):
    """`agent_state()` is what the portal calls on every poll (a two-second
    budget) and what the doctor calls three times. A wedged launchd domain
    may cost seconds, never the thirty a build or a bootstrap is allowed."""
    calls = []
    real = subprocess.run

    def spy(argv, **kwargs):
        calls.append(kwargs["timeout"])
        return real(argv, **kwargs)

    monkeypatch.setattr(app_module.subprocess, "run", spy)

    app_module.agent_state()

    assert calls == [app_module._LIST_TIMEOUT]
    assert app_module._LIST_TIMEOUT < app_module._TIMEOUT


# --- the three lows the 0.13.0 review left open, closed after it -----------


def test_a_symlinked_plist_path_is_refused_with_a_message_not_a_traceback(env, tmp_path):
    """`O_NOFOLLOW` refuses the symlink; the CLI has to say so rather than
    die, and the symlink's target must be untouched."""
    target = tmp_path / "elsewhere.plist"
    target.write_text("keep me", encoding="utf-8")
    app_module.plist_path().parent.mkdir(parents=True, exist_ok=True)
    app_module.plist_path().symlink_to(target)

    result = invoke("install", "--yes")

    assert result.exit_code == 1
    assert "Could not write the LaunchAgent" in result.output
    assert not isinstance(result.exception, OSError)
    assert target.read_text(encoding="utf-8") == "keep me"


def test_the_override_line_is_quoted_for_a_path_with_a_space(tmp_path):
    path = tmp_path / "my tools" / "vocalize"

    assert app_module.override_command(path) == (
        f"defaults write cards.arda.vocalize.app VocalizeBinary '{path}'"
    )


def test_a_bundle_missing_its_plist_reads_as_stale(env):
    """The same two facts `build_bundle` decides "current" from."""
    invoke("install", "--yes")
    assert app_module.bundle_state() == "current"

    (app_module.bundle_path() / "Contents" / "Info.plist").unlink()

    assert app_module.bundle_state() == "stale"


def test_a_rebuild_forgets_the_old_status_file(env, tmp_path):
    """After `tccutil reset`, the old app's last status still said granted;
    until the new app writes its own, a reader must see no file."""
    invoke("install", "--yes")
    app_module.status_path().parent.mkdir(parents=True, exist_ok=True)
    app_module.status_path().write_text("accessibility: granted\n", encoding="utf-8")
    env.source.write_text("import AppKit  // newer\n", encoding="utf-8")

    result = invoke("install", "--yes")

    assert result.exit_code == 0, result.output
    assert app_module.APP_REGRANT_WARNING in result.output
    assert not app_module.status_path().exists()


def test_bootstrap_retries_once_after_launchd_says_io_error(monkeypatch):
    """Right after a bootout, launchd can answer the bootstrap with error 5
    while it is still tearing the old service down (seen live); the second
    attempt a moment later succeeds."""
    answers = [subprocess.CompletedProcess([], 5, "", "Bootstrap failed: 5: Input/output error"),
               subprocess.CompletedProcess([], 0, "", "")]
    calls = []
    monkeypatch.setattr(app_module, "_run", lambda argv, timeout=30: calls.append(argv) or answers.pop(0))
    naps = []
    monkeypatch.setattr(app_module.time, "sleep", naps.append)

    result = app_module.bootstrap()

    assert result.returncode == 0
    assert len(calls) == 2 and calls[0] == calls[1]
    assert naps == [app_module._BOOTSTRAP_RETRY_PAUSE]


def test_bootstrap_does_not_retry_a_success(monkeypatch):
    calls = []
    monkeypatch.setattr(app_module, "_run", lambda argv, timeout=30: calls.append(argv) or subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(app_module.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("slept")))

    assert app_module.bootstrap().returncode == 0
    assert len(calls) == 1
