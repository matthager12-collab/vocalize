import plistlib

import install_quick_action
import pytest


def _seed_templates(tmp_path):
    """Minimal fixture bundles with the placeholder in each document.wflow."""
    templates = tmp_path / "quick_actions"
    for name in install_quick_action.BUNDLE_NAMES:
        resources = templates / name / "Contents" / "Resources"
        resources.mkdir(parents=True)
        (templates / name / "Contents" / "Info.plist").write_text(
            "<plist/>", encoding="utf-8"
        )
        (resources / "document.wflow").write_text(
            f'BIN="{install_quick_action.PLACEHOLDER}"', encoding="utf-8"
        )
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

    pbs_calls = []
    monkeypatch.setattr(
        install_quick_action.subprocess, "run",
        lambda argv, **kwargs: pbs_calls.append(argv),
    )
    return services, fake_bin, pbs_calls


def test_copies_both_bundles_into_services_dir(install_env):
    services, _bin, _pbs = install_env

    assert install_quick_action.main() == 0

    for name in install_quick_action.BUNDLE_NAMES:
        assert (services / name / "Contents" / "Resources" / "document.wflow").is_file()


def test_substitutes_the_vocalize_bin_placeholder(install_env):
    services, fake_bin, _pbs = install_env

    assert install_quick_action.main() == 0

    for name in install_quick_action.BUNDLE_NAMES:
        text = (services / name / "Contents" / "Resources" / "document.wflow").read_text(
            encoding="utf-8"
        )
        assert install_quick_action.PLACEHOLDER not in text
        assert str(fake_bin) in text


def test_runs_pbs_update_after_installing(install_env):
    _services, _bin, pbs_calls = install_env

    assert install_quick_action.main() == 0

    assert pbs_calls == [[install_quick_action.PBS, "-update"]]


def test_is_idempotent_on_rerun(install_env):
    services, fake_bin, _pbs = install_env

    assert install_quick_action.main() == 0
    assert install_quick_action.main() == 0

    for name in install_quick_action.BUNDLE_NAMES:
        text = (services / name / "Contents" / "Resources" / "document.wflow").read_text(
            encoding="utf-8"
        )
        # A second run overwrites cleanly: still exactly one substituted path.
        assert text.count(str(fake_bin)) == 1


def test_refuses_a_path_with_unsafe_characters(install_env, monkeypatch, tmp_path, capsys):
    services, _bin, pbs_calls = install_env
    evil = tmp_path / 'has"quote' / "vocalize"
    monkeypatch.setattr(install_quick_action, "_resolve_vocalize_bin", lambda: evil)

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


def _real_wflow(name):
    path = (
        install_quick_action.TEMPLATES_DIR / name / "Contents" / "Resources" / "document.wflow"
    )
    with path.open("rb") as fh:
        return plistlib.load(fh)


def test_checked_in_speak_bundle_reads_stdin_and_asks_via_dialog():
    doc = _real_wflow("Speak with Vocalize.workflow")
    params = doc["actions"][0]["action"]["ActionParameters"]
    # inputMethod 0 = stdin: selected text must never pass through argv/shell.
    assert params["inputMethod"] == 0
    assert "--ask-dialog" in params["COMMAND_STRING"]
    assert "speak-file - " in params["COMMAND_STRING"]


def test_checked_in_stop_bundle_takes_no_stdin():
    doc = _real_wflow("Stop Vocalize.workflow")
    params = doc["actions"][0]["action"]["ActionParameters"]
    # inputMethod 1 = as-arguments: a no-input service must not wait on stdin.
    assert params["inputMethod"] == 1
    assert " stop" in params["COMMAND_STRING"]


def test_checked_in_plan_bundle_speaks_newest_plan_on_demand():
    doc = _real_wflow("Speak Latest Plan.workflow")
    params = doc["actions"][0]["action"]["ActionParameters"]
    script = params["COMMAND_STRING"]
    # No-input service (inputMethod 1), reads the newest plan file, asks
    # via dialog when over the cap, and quotes the resolved path.
    assert params["inputMethod"] == 1
    assert '.claude/plans"/*.md' in script
    assert "--ask-dialog" in script
    assert 'speak-file "$PLAN"' in script
    assert install_quick_action.PLACEHOLDER in script
