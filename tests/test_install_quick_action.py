import plistlib

import install_quick_action
import pytest


def _seed_templates(tmp_path):
    """Minimal fixture bundles carrying every placeholder in document.wflow."""
    templates = tmp_path / "quick_actions"
    body = (
        f'BIN="{install_quick_action.PLACEHOLDER}" '
        f'CLAUDE="{install_quick_action.CLAUDE_PLACEHOLDER}" '
        f'EXTRA="{install_quick_action.CLAUDE_EXTRA_PATH_PLACEHOLDER}" '
        f'HELPER="{install_quick_action.HELPER_PLACEHOLDER}"'
    )
    for name in install_quick_action.BUNDLE_NAMES:
        resources = templates / name / "Contents" / "Resources"
        resources.mkdir(parents=True)
        (templates / name / "Contents" / "Info.plist").write_text(
            "<plist/>", encoding="utf-8"
        )
        (resources / "document.wflow").write_text(body, encoding="utf-8")
    return templates


@pytest.fixture
def install_env(monkeypatch, tmp_path):
    """Point every module path at tmp and neutralize the pbs call."""
    templates = _seed_templates(tmp_path)
    services = tmp_path / "Services"
    monkeypatch.setattr(install_quick_action, "TEMPLATES_DIR", templates)
    monkeypatch.setattr(install_quick_action, "SERVICES_DIR", services)

    fake_bin = tmp_path / "venv-bin" / "vocalize"
    fake_bin.parent.mkdir()
    fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(install_quick_action, "_resolve_vocalize_bin", lambda: fake_bin)

    fake_helper = tmp_path / "hooks" / "speak_options.py"
    fake_helper.parent.mkdir()
    fake_helper.write_text("# helper\n", encoding="utf-8")
    monkeypatch.setattr(install_quick_action, "_resolve_helper", lambda: fake_helper)

    fake_claude = tmp_path / "npm" / "bin" / "claude"
    fake_extra = str(tmp_path / "npm" / "bin")
    monkeypatch.setattr(
        install_quick_action, "_resolve_claude", lambda: (str(fake_claude), fake_extra)
    )

    pbs_calls = []
    monkeypatch.setattr(
        install_quick_action.subprocess, "run",
        lambda argv, **kwargs: pbs_calls.append(argv),
    )
    return services, fake_bin, fake_helper, fake_claude, pbs_calls


def test_copies_both_bundles_into_services_dir(install_env):
    services, _bin, _helper, _claude, _pbs = install_env

    assert install_quick_action.main() == 0

    for name in install_quick_action.BUNDLE_NAMES:
        assert (services / name / "Contents" / "Resources" / "document.wflow").is_file()


def test_substitutes_the_vocalize_bin_placeholder(install_env):
    services, fake_bin, _helper, _claude, _pbs = install_env

    assert install_quick_action.main() == 0

    for name in install_quick_action.BUNDLE_NAMES:
        text = (services / name / "Contents" / "Resources" / "document.wflow").read_text(
            encoding="utf-8"
        )
        assert install_quick_action.PLACEHOLDER not in text
        assert str(fake_bin) in text


def test_substitutes_claude_helper_and_extra_path_placeholders(install_env):
    services, _bin, fake_helper, fake_claude, _pbs = install_env

    assert install_quick_action.main() == 0

    text = (services / "Speak with Vocalize.workflow" / "Contents" / "Resources"
            / "document.wflow").read_text(encoding="utf-8")
    for placeholder in (install_quick_action.CLAUDE_PLACEHOLDER,
                        install_quick_action.CLAUDE_EXTRA_PATH_PLACEHOLDER,
                        install_quick_action.HELPER_PLACEHOLDER):
        assert placeholder not in text
    assert str(fake_helper) in text
    assert str(fake_claude) in text


def test_bakes_empty_claude_values_when_claude_absent(install_env, monkeypatch):
    services, _bin, _helper, _claude, _pbs = install_env
    monkeypatch.setattr(install_quick_action, "_resolve_claude", lambda: ("", ""))

    assert install_quick_action.main() == 0

    text = (services / "Speak with Vocalize.workflow" / "Contents" / "Resources"
            / "document.wflow").read_text(encoding="utf-8")
    # Placeholders gone, replaced by empty strings — no traceback, still installs.
    assert install_quick_action.CLAUDE_PLACEHOLDER not in text
    assert 'CLAUDE=""' in text


def test_runs_pbs_update_after_installing(install_env):
    _services, _bin, _helper, _claude, pbs_calls = install_env

    assert install_quick_action.main() == 0

    assert pbs_calls == [[install_quick_action.PBS, "-update"]]


def test_is_idempotent_on_rerun(install_env):
    services, fake_bin, _helper, _claude, _pbs = install_env

    assert install_quick_action.main() == 0
    assert install_quick_action.main() == 0

    for name in install_quick_action.BUNDLE_NAMES:
        text = (services / name / "Contents" / "Resources" / "document.wflow").read_text(
            encoding="utf-8"
        )
        # A second run overwrites cleanly: still exactly one substituted path.
        assert text.count(str(fake_bin)) == 1


def test_refuses_an_unsafe_vocalize_path(install_env, monkeypatch, tmp_path, capsys):
    services, _bin, _helper, _claude, pbs_calls = install_env
    evil = tmp_path / 'has"quote' / "vocalize"
    monkeypatch.setattr(install_quick_action, "_resolve_vocalize_bin", lambda: evil)

    assert install_quick_action.main() == 1

    assert "Refusing to install" in capsys.readouterr().err
    assert not services.exists()
    assert pbs_calls == []


def test_refuses_an_unsafe_claude_path(install_env, monkeypatch, tmp_path, capsys):
    services, _bin, _helper, _claude, pbs_calls = install_env
    monkeypatch.setattr(
        install_quick_action, "_resolve_claude",
        lambda: (str(tmp_path / "ev`il" / "claude"), ""),
    )

    assert install_quick_action.main() == 1

    assert "Refusing to install" in capsys.readouterr().err
    assert not services.exists()
    assert pbs_calls == []


def test_resolves_repo_venv_before_path(monkeypatch, tmp_path):
    venv_bin = tmp_path / "repo" / ".venv" / "bin" / "vocalize"
    venv_bin.parent.mkdir(parents=True)
    venv_bin.write_text("", encoding="utf-8")
    fake_file = tmp_path / "repo" / "hooks" / "install_quick_action.py"
    monkeypatch.setattr(install_quick_action, "__file__", str(fake_file))
    monkeypatch.setattr(
        install_quick_action.shutil, "which",
        lambda name: (_ for _ in ()).throw(AssertionError("PATH consulted despite venv")),
    )

    assert install_quick_action._resolve_vocalize_bin() == venv_bin


def test_falls_back_to_path_lookup(monkeypatch, tmp_path):
    fake_file = tmp_path / "empty-repo" / "hooks" / "install_quick_action.py"
    monkeypatch.setattr(install_quick_action, "__file__", str(fake_file))
    on_path = tmp_path / "usr-bin" / "vocalize"
    on_path.parent.mkdir()
    on_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(install_quick_action.shutil, "which", lambda name: str(on_path))

    assert install_quick_action._resolve_vocalize_bin() == on_path


def test_errors_when_no_binary_found(monkeypatch, tmp_path, capsys):
    fake_file = tmp_path / "empty-repo" / "hooks" / "install_quick_action.py"
    monkeypatch.setattr(install_quick_action, "__file__", str(fake_file))
    monkeypatch.setattr(install_quick_action.shutil, "which", lambda name: None)

    with pytest.raises(SystemExit) as excinfo:
        install_quick_action._resolve_vocalize_bin()

    assert excinfo.value.code == 1
    assert "Could not find a vocalize binary" in capsys.readouterr().err


def test_resolves_helper_next_to_installer():
    helper = install_quick_action._resolve_helper()
    assert helper.name == "speak_options.py"
    assert helper.is_file()


def test_errors_when_helper_missing(monkeypatch, tmp_path, capsys):
    fake_file = tmp_path / "elsewhere" / "install_quick_action.py"
    monkeypatch.setattr(install_quick_action, "__file__", str(fake_file))
    with pytest.raises(SystemExit) as excinfo:
        install_quick_action._resolve_helper()
    assert excinfo.value.code == 1
    assert "picker helper" in capsys.readouterr().err


def test_resolve_claude_empty_when_absent(monkeypatch):
    monkeypatch.setattr(install_quick_action.shutil, "which", lambda name: None)
    assert install_quick_action._resolve_claude() == ("", "")


def test_resolve_claude_includes_node_dir(monkeypatch, tmp_path):
    claude = tmp_path / "npm" / "bin" / "claude"
    node = tmp_path / "node" / "bin" / "node"

    def which(name):
        return {"claude": str(claude), "node": str(node)}.get(name)

    monkeypatch.setattr(install_quick_action.shutil, "which", which)
    path, extra = install_quick_action._resolve_claude()
    assert path == str(claude.resolve())
    parts = extra.split(install_quick_action.os.pathsep)
    assert str(claude.resolve().parent) in parts
    assert str(node.resolve().parent) in parts


def _real_wflow(name):
    path = (
        install_quick_action.TEMPLATES_DIR / name / "Contents" / "Resources" / "document.wflow"
    )
    with path.open("rb") as fh:
        return plistlib.load(fh)


def test_checked_in_speak_bundle_execs_the_helper():
    doc = _real_wflow("Speak with Vocalize.workflow")
    params = doc["actions"][0]["action"]["ActionParameters"]
    script = params["COMMAND_STRING"]
    # inputMethod 0 = stdin: selected text reaches the helper via stdin only.
    assert params["inputMethod"] == 0
    assert 'exec /usr/bin/python3 "__HELPER__"' in script
    assert 'export CLAUDE_BIN="__CLAUDE_BIN__"' in script
    assert 'export VOCALIZE_BIN="$BIN"' in script


def test_checked_in_stop_bundle_takes_no_stdin():
    doc = _real_wflow("Stop Vocalize.workflow")
    params = doc["actions"][0]["action"]["ActionParameters"]
    # inputMethod 1 = as-arguments: a no-input service must not wait on stdin.
    assert params["inputMethod"] == 1
    assert " stop" in params["COMMAND_STRING"]


def test_checked_in_plan_bundle_redirects_plan_into_helper():
    doc = _real_wflow("Speak Latest Plan.workflow")
    params = doc["actions"][0]["action"]["ActionParameters"]
    script = params["COMMAND_STRING"]
    # No-input service that still resolves the newest plan itself, then feeds
    # it to the shared helper over stdin.
    assert params["inputMethod"] == 1
    assert '.claude/plans"/*.md' in script
    assert 'exec /usr/bin/python3 "__HELPER__" < "$PLAN"' in script
    assert install_quick_action.PLACEHOLDER in script


# --- the dictation Quick Action (T-44) --------------------------------


DICTATE_BUNDLE = "Dictate with Vocalize.workflow"


def _real_info(name):
    path = install_quick_action.TEMPLATES_DIR / name / "Contents" / "Info.plist"
    with path.open("rb") as fh:
        return plistlib.load(fh)


def test_the_dictate_bundle_is_installed_with_the_others(install_env):
    services, fake_bin, _helper, _claude, _pbs = install_env

    assert install_quick_action.main() == 0

    wflow = services / DICTATE_BUNDLE / "Contents" / "Resources" / "document.wflow"
    assert wflow.is_file()
    text = wflow.read_text(encoding="utf-8")
    assert install_quick_action.PLACEHOLDER not in text
    assert str(fake_bin) in text


def test_the_dictate_bundle_takes_no_input_at_all():
    """A keyboard shortcut has no selection to hand over.

    A service that declares NSSendTypes is offered only when something is
    selected, and one whose action reads stdin waits forever when nothing
    arrives — either would make the hotkey do nothing at all.
    """
    info = _real_info(DICTATE_BUNDLE)
    service = info["NSServices"][0]
    assert "NSSendTypes" not in service
    assert "NSReturnTypes" not in service

    doc = _real_wflow(DICTATE_BUNDLE)
    params = doc["actions"][0]["action"]["ActionParameters"]
    assert params["inputMethod"] == 1  # as arguments, never stdin
    assert doc["workflowMetaData"]["serviceProcessesInput"] == 0
    assert doc["workflowMetaData"]["serviceInputTypeIdentifier"] == "com.apple.Automator.nothing"


def test_the_dictate_bundle_has_its_own_identity():
    info = _real_info(DICTATE_BUNDLE)

    assert info["CFBundleIdentifier"] == "cards.arda.vocalize.dictate"
    assert info["NSServices"][0]["NSMenuItem"]["default"] == "Dictate with Vocalize"


def test_the_dictate_bundle_does_not_reuse_the_stop_bundles_uuids():
    """Two services sharing an action UUID confuse the Services registry."""
    dictate_action = _real_wflow(DICTATE_BUNDLE)["actions"][0]["action"]
    stop_action = _real_wflow("Stop Vocalize.workflow")["actions"][0]["action"]

    assert dictate_action["UUID"] != stop_action["UUID"]
    assert dictate_action["InputUUID"] != stop_action["InputUUID"]


def test_the_dictate_bundle_execs_dictate_and_exports_the_claude_environment():
    script = _real_wflow(DICTATE_BUNDLE)["actions"][0]["action"]["ActionParameters"][
        "COMMAND_STRING"
    ]

    assert 'exec "$BIN" dictate' in script
    assert 'export CLAUDE_BIN="__CLAUDE_BIN__"' in script
    assert 'export CLAUDE_EXTRA_PATH="__CLAUDE_EXTRA_PATH__"' in script
    assert install_quick_action.PLACEHOLDER in script


def test_the_dictate_bundle_never_passes_text_on_its_command_line():
    """Nothing spoken or dictated may become a Quick Action argument."""
    script = _real_wflow(DICTATE_BUNDLE)["actions"][0]["action"]["ActionParameters"][
        "COMMAND_STRING"
    ]

    assert "$@" not in script
    assert "$1" not in script
    assert "$*" not in script
