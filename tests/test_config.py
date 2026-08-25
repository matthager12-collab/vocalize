import os

import pytest

from vocalize.config import _load_dotenv_if_present, resolve_api_key
from vocalize.exceptions import MissingAPIKeyError

# Bound at import time on purpose: conftest's autouse fixture replaces the
# module attribute, so this reference is the only way to reach the real one.
real_load_dotenv = _load_dotenv_if_present


def test_explicit_key_wins_over_everything(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    assert resolve_api_key("explicit-key") == "explicit-key"


def test_falls_back_to_env_var(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    assert resolve_api_key(None) == "env-key"


def test_raises_clear_error_when_nothing_found(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError):
        resolve_api_key(None)


def test_dotenv_loader_reads_the_env_file_in_the_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ELEVENLABS_API_KEY=from-cwd-file\n", encoding="utf-8")
    # setenv before delenv so monkeypatch restores the var whether or not it
    # was set beforehand — the real loader writes straight to os.environ.
    monkeypatch.setenv("ELEVENLABS_API_KEY", "placeholder")
    monkeypatch.delenv("ELEVENLABS_API_KEY")

    real_load_dotenv()

    assert os.environ["ELEVENLABS_API_KEY"] == "from-cwd-file"
