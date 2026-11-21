import json
import os
from datetime import datetime, timezone

import pytest

from vocalize import ledger
from vocalize.exceptions import ConfigError

NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


def test_local_counts_are_private_and_atomic(tmp_path):
    ledger.record("kokoro", 10, tmp_path, NOW)
    ledger.record("kokoro", 5, tmp_path, NOW)
    assert ledger.status("kokoro", tmp_path, NOW) == (15, False)
    assert ledger.path(tmp_path).stat().st_mode & 0o777 == 0o600
    assert len(list(tmp_path.iterdir())) == 1


def test_cloud_records_are_not_exposed_or_deleted(tmp_path):
    data = {"months": {"2026-10": {"openai": {"chars": 123}}}}
    target = ledger.path(tmp_path)
    target.write_text(json.dumps(data))
    target.chmod(0o600)
    assert ledger.all_status(tmp_path, NOW) == {}
    ledger.record("say", 5, tmp_path, NOW)
    assert json.loads(target.read_text())["months"]["2026-10"]["openai"]["chars"] == 123
    assert ledger.all_status(tmp_path, NOW) == {"say": {"chars": 5, "exhausted": False}}


@pytest.mark.parametrize("provider", ["openai", "google", "../outside", "unknown"])
def test_cloud_or_unknown_provider_cannot_access_stats(tmp_path, provider):
    with pytest.raises(ConfigError):
        ledger.record(provider, 1, tmp_path, NOW)
    with pytest.raises(ConfigError):
        ledger.status(provider, tmp_path, NOW)
    assert not ledger.path(tmp_path).exists()


@pytest.mark.parametrize("value", [-1, True, "1"])
def test_invalid_counts_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        ledger.record("say", value, tmp_path)


@pytest.mark.parametrize("content", ['not json SECRET_CANARY\nforged', '{"months":[]}', '{"months":{"now":{"say":{"chars":-1}}}}', '{"months":{"now":{"say":7}}}', '[]'])
def test_corrupt_and_executable_shaped_data_fails_closed(tmp_path, content, capsys):
    target = ledger.path(tmp_path)
    target.write_text(content)
    target.chmod(0o600)
    assert ledger.load(tmp_path) == {}
    ledger.record("say", 1, tmp_path)
    assert target.read_text() == content
    err = capsys.readouterr().err
    assert "SECRET_CANARY" not in err and "forged" not in err


def test_oversize_json_rejected(tmp_path):
    target = ledger.path(tmp_path)
    target.write_bytes(b" " * (ledger._MAX_BYTES + 1))
    target.chmod(0o600)
    assert ledger.load(tmp_path) == {}


def test_symlink_and_hardlink_cannot_read_or_overwrite_another_file(tmp_path):
    source = tmp_path / "private.json"
    source.write_text('{"months":{}}')
    source.chmod(0o600)
    domain = tmp_path / "domain"
    domain.mkdir()
    target = ledger.path(domain)
    target.symlink_to(source)
    assert ledger.load(domain) == {}
    ledger.record("say", 1, domain)
    assert source.read_text() == '{"months":{}}'
    target.unlink()
    os.link(source, target)
    assert ledger.load(domain) == {}
    ledger.record("say", 1, domain)
    assert source.read_text() == '{"months":{}}'
