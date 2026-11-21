"""Install Quick Actions only into temporary Services directories."""
import plistlib

import pytest

from vocalize import app, integrate


@pytest.fixture
def installer(monkeypatch, tmp_path):
    services = tmp_path / 'Services'
    binary = tmp_path / 'bin with spaces' / 'vocalize'
    binary.parent.mkdir()
    binary.write_text('#!/bin/sh\nexit 0\n')
    binary.chmod(0o755)
    monkeypatch.setattr(integrate, 'SERVICES_DIR', services)
    monkeypatch.setattr(integrate, '_resolve_vocalize_bin', lambda: str(binary))
    monkeypatch.setattr(integrate.subprocess, 'run', lambda *a, **k: None)
    return services, binary


def test_installs_five_bundles_with_swap_placeholder_substituted(installer):
    services, binary = installer
    assert integrate.main() == 0
    assert len(integrate.BUNDLE_NAMES) == 5
    assert {p.name for p in services.iterdir()} == set(integrate.BUNDLE_NAMES)
    bundle = services / 'Swap in What I Said.workflow'
    info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
    assert info['CFBundleName'] == 'Swap in What I Said'
    assert info['CFBundleIdentifier'] == 'cards.arda.vocalize.swap'
    workflow = plistlib.loads((bundle / 'Contents/Resources/document.wflow').read_bytes())
    command = workflow['actions'][0]['action']['ActionParameters']['COMMAND_STRING']
    assert '__VOCALIZE_BIN__' not in command
    assert f'BIN="{binary}"' in command
    assert '"$BIN" dictate --swap' in command


def test_swap_template_matches_stop_structure():
    base = integrate.TEMPLATES_DIR
    for relative in ('Contents/Info.plist', 'Contents/Resources/document.wflow'):
        stop = (base / 'Stop Vocalize.workflow' / relative).read_bytes()
        swap = (base / 'Swap in What I Said.workflow' / relative).read_bytes()
        expected = stop.replace(b'Stop Vocalize', b'Swap in What I Said')
        expected = expected.replace(b'cards.arda.vocalize.stop', b'cards.arda.vocalize.swap')
        expected = expected.replace(b'this service only stops whatever vocalize is playing.',
                                    b'this service swaps the two dictation clipboard types.')
        expected = expected.replace(b'"$BIN" stop', b'"$BIN" dictate --swap')
        assert swap == expected


def test_swap_service_conflict_reported(installer, monkeypatch, capsys):
    monkeypatch.setattr(integrate, '_install_skill', lambda **k: 'installed')
    monkeypatch.setattr(integrate, '_path_precheck', dict)
    monkeypatch.setattr(app, '_SERVICES_NAMES', app._SERVICES_NAMES.copy())
    monkeypatch.setattr(app, 'status_dict', lambda: {'accessibility': 'granted'})
    monkeypatch.setattr(app, 'read_services_shortcuts',
                        lambda: {'cards.arda.vocalize.swap': 'ctrl+alt+cmd+d'})
    assert integrate.integrate_claude(yes=False) == 0
    output = capsys.readouterr().out
    assert "Clear 'Swap in What I Said' (ctrl+alt+cmd+d)" in output
    assert '5 Quick Actions' in output


def test_swap_symlink_destination_refused(installer, tmp_path):
    services, _ = installer
    services.mkdir()
    target = tmp_path / 'untouched'
    target.mkdir()
    (services / 'Swap in What I Said.workflow').symlink_to(target, target_is_directory=True)
    assert integrate.main() == 1
    assert list(target.iterdir()) == []


def test_packaged_skill_cannot_grant_hosted_digest_tools():
    """The installer must not ship the retired Agent/Read/web digest grants."""
    text = integrate.SKILL_SRC.read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)[1]
    tools = next(line.split(":", 1)[1].strip() for line in frontmatter.splitlines()
                 if line.startswith("allowed-tools:"))
    assert tools == "Write, Bash(vocalize:*), Bash(wc:*), Bash(ls:*), Bash(pbpaste:*)"
    assert "model: sonnet" not in text
    assert "spawn ONE subagent" not in text
    assert "write a plain spoken-prose summary" not in text


def test_packaged_skill_only_speaks_text_as_given():
    """The /speak skill keeps verbatim modes only: no rewriting, web or gate modes."""
    text = integrate.SKILL_SRC.read_text(encoding="utf-8")
    lowered = text.lower()
    for retired in ("summar", "digest", "speak_url_gate", "webfetch(", "**url:**"):
        assert retired not in lowered
    assert "`vocalize clip --ask-dialog`" in text
    assert "`vocalize speak-file`, which speaks the file verbatim" in text
    assert "**Starts with `say `, or explicitly pasted text:**" in text


def test_quick_action_install_output_and_bundles_name_no_retired_feature(installer, capsys):
    services, _ = installer
    assert integrate.main() == 0
    output = capsys.readouterr().out.lower()
    assert "summar" not in output
    for path in services.rglob("*"):
        if path.is_file():
            body = path.read_bytes().lower()
            assert b"summar" not in body
            assert b"speak_url_gate" not in body
            assert b"digest" not in body
