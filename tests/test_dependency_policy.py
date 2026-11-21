"""Keep hosted SDKs and unreviewed dependency sources out of this local build."""

import re
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


_APPROVED = {"click", "tomli", "pytest", "pytest-cov", "ruff", "build", "twine", "hatchling"}


def _enforce_policy(requirements):
    for requirement in requirements:
        match = re.match(r"([a-zA-Z0-9][a-zA-Z0-9._-]*)", requirement)
        name = re.sub(r"[-_.]+", "-", match[1]).lower() if match else ""
        if name not in _APPROVED or "@" in requirement:
            raise ValueError("Dependency requires explicit policy review")


def test_declared_dependencies_obey_local_build_policy():
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text())
    requirements = metadata["build-system"]["requires"] + metadata["project"]["dependencies"]
    for group in metadata["project"].get("optional-dependencies", {}).values():
        requirements += group
    _enforce_policy(requirements)


@pytest.mark.parametrize("requirement", [
    "elevenlabs>=2.0", "openai", "google-cloud-texttospeech", "boto3",
    "anthropic", "keyring", "python-dotenv", "unreviewed-package==1.0",
    "click @ https://example.invalid/unreviewed.whl",
])
def test_policy_rejects_controlled_disallowed_dependency(requirement):
    with pytest.raises(ValueError, match="explicit policy review"):
        _enforce_policy(["click>=8.1", requirement])
