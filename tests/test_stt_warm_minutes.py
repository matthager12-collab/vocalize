"""The bounded warm window and its portal round trip."""

import json

import pytest

from vocalize import config, wizard
from vocalize.exceptions import ConfigError
from vocalize.local import llm_manifest
from vocalize.portal import TOKEN_HEADER, Portal


@pytest.fixture(autouse=True)
def isolated_llm_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_manifest, "MODEL_DIR", tmp_path / "llm")


@pytest.mark.parametrize("minutes", [-1, 241, "15", True, 1.5, None])
def test_warm_minutes_rejects_invalid_values(tmp_path, minutes):
    with pytest.raises(ConfigError, match="stt.warm_minutes"):
        config.resolve_stt({"stt": {"warm_minutes": minutes}})


@pytest.mark.parametrize("minutes", [0, 15, 240])
def test_warm_minutes_accepts_integers(tmp_path, minutes):
    assert config.resolve_stt({"stt": {"warm_minutes": minutes}})["warm_minutes"] == minutes


def test_warm_minutes_defaults_to_zero(tmp_path):
    assert config.resolve_stt({})["warm_minutes"] == 0


def test_portal_round_trips_warm_minutes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    portal = Portal(probe_timeout=0.2)
    portal.port = 45999
    headers = {"Host": "127.0.0.1:45999"}
    status, _, body = portal.route(
        "POST", "/api/session", headers, json.dumps({"code": portal._code}).encode()
    )
    assert status == 200
    authed = {**headers, TOKEN_HEADER: json.loads(body)["token"]}
    status, _, _ = portal.route(
        "POST", "/api/stt", authed,
        json.dumps({"settings": {"warm_minutes": 15},
                    "fingerprint": wizard.ABSENT_CONFIG}).encode(),
    )
    assert status == 200
    status, _, body = portal.route("GET", "/api/state", authed)
    assert status == 200
    assert json.loads(body)["stt"]["warm_minutes"] == 15
