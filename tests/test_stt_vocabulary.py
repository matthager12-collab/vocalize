"""The bounded Whisper vocabulary setting and its portal round trip."""

import json

import pytest

from vocalize import config, wizard
from vocalize.exceptions import ConfigError
from vocalize.portal import TOKEN_HEADER, Portal


@pytest.mark.parametrize(
    "vocabulary",
    [
        "pyproject",
        ["term"] * 51,
        ["a" * 41],
        ["one two three four five"],
        ["one\ntwo"],
        ["<|im_end|>"],
        ["sentence."],
        ["Send it now. "],
        [" padded"],
        ["two  spaces"],
        ["a" * 40] * 20,
    ],
)
def test_vocabulary_rejects_invalid_terms(tmp_path, vocabulary):
    with pytest.raises(ConfigError, match="stt.vocabulary"):
        config.resolve_stt({"stt": {"vocabulary": vocabulary}})


def test_vocabulary_accepts_short_jargon_terms():
    items = ["pyproject", "uv --no-project", "sha256", "resolve_provider_settings", "node.js"]

    assert config.resolve_stt({"stt": {"vocabulary": items}})["vocabulary"] == items


def test_vocabulary_prompt_is_empty_or_exactly_joined():
    assert config.vocabulary_prompt([]) == ""
    assert config.vocabulary_prompt(["pyproject", "sha256"]) == "Vocabulary: pyproject, sha256."


def test_portal_round_trips_vocabulary(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    portal = Portal(probe_timeout=0.2)
    portal.port = 45999
    origin = "127.0.0.1:45999"
    headers = {"Host": origin}
    status, _, body = portal.route(
        "POST", "/api/session", headers, json.dumps({"code": portal._code}).encode()
    )
    assert status == 200
    token = json.loads(body)["token"]
    authed = {"Host": origin, TOKEN_HEADER: token}
    items = ["pyproject", "uv --no-project"]

    status, _, _ = portal.route(
        "POST",
        "/api/stt",
        authed,
        json.dumps({"settings": {"vocabulary": items}, "fingerprint": wizard.ABSENT_CONFIG}).encode(),
    )

    assert status == 200
    status, _, body = portal.route("GET", "/api/state", authed)
    assert status == 200
    assert json.loads(body)["stt"]["vocabulary"] == items
