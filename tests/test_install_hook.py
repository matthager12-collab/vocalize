import json
import shlex

import install_hook
import pytest


@pytest.fixture
def settings_path(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(install_hook, "SETTINGS_PATH", path)
    return path


def _stop_groups(settings_path):
    return json.loads(settings_path.read_text(encoding="utf-8"))["hooks"]["Stop"]


def test_creates_settings_file_when_absent(settings_path):
    assert install_hook.main() == 0

    command = _stop_groups(settings_path)[0]["hooks"][0]["command"]
    assert str(install_hook.HOOK_SCRIPT) in command


def test_quotes_a_hook_path_containing_a_space(settings_path, monkeypatch, tmp_path):
    script = tmp_path / "my checkout" / "claude_stop_hook.py"
    monkeypatch.setattr(install_hook, "HOOK_SCRIPT", script)

    assert install_hook.main() == 0

    command = _stop_groups(settings_path)[0]["hooks"][0]["command"]
    assert shlex.quote(str(script)) in command
    assert shlex.split(command) == ["python3", str(script)]


def test_is_idempotent(settings_path, capsys):
    assert install_hook.main() == 0
    capsys.readouterr()

    assert install_hook.main() == 0

    assert len(_stop_groups(settings_path)) == 1
    assert "already installed" in capsys.readouterr().out


def test_preserves_unrelated_settings_and_hooks(settings_path):
    pre_tool_use = [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
    ]
    settings_path.write_text(
        json.dumps({"model": "opus", "hooks": {"PreToolUse": pre_tool_use}}),
        encoding="utf-8",
    )

    assert install_hook.main() == 0

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["model"] == "opus"
    assert settings["hooks"]["PreToolUse"] == pre_tool_use
    assert len(settings["hooks"]["Stop"]) == 1


def test_backs_up_existing_settings(settings_path):
    original = json.dumps({"model": "opus"})
    settings_path.write_text(original, encoding="utf-8")

    assert install_hook.main() == 0

    backups = list(settings_path.parent.glob("settings.json.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original


def test_refuses_to_touch_invalid_json(settings_path):
    settings_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        install_hook.main()

    assert excinfo.value.code == 1
    assert settings_path.read_text(encoding="utf-8") == "{not json"


def test_remove_takes_out_only_our_hook(settings_path):
    other = {"type": "command", "command": "echo done"}
    settings_path.write_text(json.dumps({
        "model": "opus",
        "hooks": {"Stop": [{"matcher": "", "hooks": [other]}],
                  "PreToolUse": [{"matcher": "Bash", "hooks": [other]}]},
    }), encoding="utf-8")
    assert install_hook.main() == 0
    assert install_hook.installed()

    assert install_hook.remove() == 0

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert not install_hook.installed()
    assert settings["model"] == "opus"
    assert settings["hooks"]["Stop"] == [{"matcher": "", "hooks": [other]}]
    assert settings["hooks"]["PreToolUse"] == [{"matcher": "Bash", "hooks": [other]}]


def test_remove_keeps_a_group_that_shares_our_entry(settings_path):
    ours = {"type": "command", "command": f"python3 {install_hook.HOOK_SCRIPT}"}
    other = {"type": "command", "command": "echo done"}
    settings_path.write_text(json.dumps(
        {"hooks": {"Stop": [{"matcher": "", "hooks": [ours, other]}]}}), encoding="utf-8")
    assert install_hook.remove() == 0
    assert _stop_groups(settings_path) == [{"matcher": "", "hooks": [other]}]


def test_remove_drops_empty_tables_and_backs_up(settings_path):
    settings_path.write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    install_hook.main()
    for backup in settings_path.parent.glob("settings.json.bak.*"):
        backup.unlink()

    assert install_hook.remove() == 0

    assert json.loads(settings_path.read_text(encoding="utf-8")) == {"model": "opus"}
    assert len(list(settings_path.parent.glob("settings.json.bak.*"))) == 1


def test_remove_when_not_installed_changes_nothing(settings_path, capsys):
    assert install_hook.remove() == 0
    assert not settings_path.exists()
    assert "not installed" in capsys.readouterr().out


def test_remove_refuses_invalid_json(settings_path):
    settings_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        install_hook.remove()
    assert settings_path.read_text(encoding="utf-8") == "{not json"
    assert install_hook.installed() is False
