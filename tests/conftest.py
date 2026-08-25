import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import pytest


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """Prevent tests from loading the developer's real .env file."""
    monkeypatch.setattr("vocalize.config._load_dotenv_if_present", lambda: None)
