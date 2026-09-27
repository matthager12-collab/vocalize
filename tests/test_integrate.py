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
    monkeypatch.setattr(integrate, '_resolve_claude', lambda: ('', ''))
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
