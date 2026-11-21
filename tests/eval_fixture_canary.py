"""Explicitly selected by the harness unit test; never loads a model."""

import os
import subprocess
import sys

import pytest
from eval_runner import owned_group

from vocalize import llm
from vocalize.local import llm_manifest

pytestmark = pytest.mark.eval


def test_supervised_fixture():
    assert llm.LOCAL_RUN_SEAM is subprocess.run
    assert llm_manifest.MODEL_DIR.name == "default-qwen-cache"
    result = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpgrp())"],
        start_new_session=True, capture_output=True, text=True, check=True, timeout=2,
    )
    assert int(result.stdout) == owned_group() == os.getpgrp()
