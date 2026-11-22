"""What the wheel and the source archive are configured to hold.

Nothing is built here. The build configuration in pyproject.toml is read
as data, and the function that finds the Claude Code hook is driven with a
faked package location: the packaged copy inside an installed wheel, the
repository's `hooks/` folder in a source checkout, and nothing else — in
particular never a top-level `hooks/` folder some other package installed
beside vocalize in site-packages.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

import vocalize
from vocalize import actions

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

REPO = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
HOOK_SCRIPTS = ("claude_stop_hook.py", "install_hook.py")


def _targets() -> dict:
    return PYPROJECT["tool"]["hatch"]["build"]["targets"]


# --- the wheel ------------------------------------------------------------


def test_the_wheel_is_the_package_alone():
    assert _targets()["wheel"]["packages"] == ["vocalize"]


def test_the_wheel_ships_the_hook_scripts_inside_the_package():
    """L7/M6: without these the hook buttons were off on every PyPI install."""
    force = _targets()["wheel"]["force-include"]
    for name in HOOK_SCRIPTS:
        assert force[f"hooks/{name}"] == f"vocalize/hooks/{name}"
        assert (REPO / "hooks" / name).is_file()
    assert all(target.startswith("vocalize/hooks/") for target in force.values())


def test_the_hook_scripts_are_not_git_ignored():
    """Hatchling drops what the VCS ignores, as it once did the cue sounds."""
    import subprocess

    for name in HOOK_SCRIPTS:
        try:
            done = subprocess.run(
                ["git", "check-ignore", "-q", str(REPO / "hooks" / name)],
                cwd=REPO, capture_output=True, check=False,
            )
        except OSError:
            pytest.skip("git is not available")
        if done.returncode == 128:
            pytest.skip("not a git checkout")
        assert done.returncode == 1, f"hooks/{name} is git-ignored"


# --- the source archive ---------------------------------------------------


def test_the_source_archive_leaves_out_the_old_plans_and_research():
    """L11: about 160 historical files describe features that are gone."""
    sdist = _targets()["sdist"]
    excluded = set(sdist["exclude"])
    assert {"/docs/plans", "/docs/research"} <= excluded
    for kept in ("README.md", "CHANGELOG.md", "LICENSE", "tests", "hooks", "docs", "vocalize"):
        assert f"/{kept}" not in excluded and kept not in excluded
    assert "only-include" not in sdist and "include" not in sdist


def test_there_is_no_env_example_asking_for_a_cloud_key():
    """M5: no code reads it, and this release talks to no service."""
    assert not (REPO / ".env.example").exists()


# --- where the hook installer is loaded from ------------------------------


def _write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_an_installed_wheel_uses_its_packaged_copy(tmp_path):
    package = tmp_path / "site-packages" / "vocalize"
    _write(package / "hooks" / "install_hook.py")
    assert actions.hooks_dir(package) == package / "hooks"


def test_a_hooks_folder_beside_an_installed_package_is_never_used(tmp_path):
    """Any other distribution may install a top-level `hooks/` package."""
    site = tmp_path / "site-packages"
    _write(site / "vocalize" / "__init__.py")
    _write(site / "hooks" / "install_hook.py", "raise SystemExit('not ours')\n")
    assert actions.hooks_dir(site / "vocalize") is None


def test_a_source_checkout_uses_its_own_hooks_folder(tmp_path):
    repo = tmp_path / "checkout"
    _write(repo / "pyproject.toml", '[project]\nname = "vocalize-cli"\n')
    _write(repo / "hooks" / "install_hook.py")
    assert actions.hooks_dir(repo / "vocalize") == repo / "hooks"


def test_a_folder_beside_some_other_project_is_never_used(tmp_path):
    repo = tmp_path / "other"
    _write(repo / "pyproject.toml", '[project]\nname = "something-else"\n')
    _write(repo / "hooks" / "install_hook.py")
    assert actions.hooks_dir(repo / "vocalize") is None


def test_this_checkout_resolves_to_its_hooks_folder():
    package = Path(vocalize.__file__).resolve().parent
    assert actions.hooks_dir() == actions.hooks_dir(package)
    if not (package / "hooks").is_dir():  # a checkout, not an installed wheel
        assert actions.hooks_dir() == REPO / "hooks"


def test_the_packaged_installer_points_the_settings_at_the_packaged_script(tmp_path, monkeypatch):
    """The settings entry runs the stop hook that shipped beside the
    installer, so a wheel's hook is the wheel's own script."""
    package = tmp_path / "site-packages" / "vocalize"
    (package / "hooks").mkdir(parents=True)
    for name in HOOK_SCRIPTS:
        shutil.copyfile(REPO / "hooks" / name, package / "hooks" / name)
    monkeypatch.setattr(actions, "_PACKAGE_DIR", package)
    monkeypatch.setattr(actions, "_hook_cache", {})
    module = actions._hook_module()
    settings = tmp_path / "claude" / "settings.json"
    monkeypatch.setattr(module, "SETTINGS_PATH", settings)

    actions.run("hook_install", {}, None)

    (group,) = json.loads(settings.read_text(encoding="utf-8"))["hooks"]["Stop"]
    command = group["hooks"][0]["command"]
    assert str((package / "hooks" / "claude_stop_hook.py").resolve()) in command
    assert module.installed()


def test_the_installer_is_loaded_from_the_resolved_folder_only(tmp_path, monkeypatch):
    site = tmp_path / "site-packages"
    _write(site / "vocalize" / "__init__.py")
    _write(site / "hooks" / "install_hook.py", "raise SystemExit('not ours')\n")
    monkeypatch.setattr(actions, "_PACKAGE_DIR", site / "vocalize")
    monkeypatch.setattr(actions, "_hook_cache", {})
    spy = []
    real = importlib.util.spec_from_file_location
    monkeypatch.setattr(importlib.util, "spec_from_file_location",
                        lambda *a, **k: spy.append(a) or real(*a, **k))
    with pytest.raises(actions.ActionError):
        actions._hook_module()
    assert spy == []
