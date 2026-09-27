"""Manual eval: configured jargon must not leak into unrelated clips."""

import os
import random
import re
import subprocess
import wave
from pathlib import Path

import pytest

from vocalize import config, dictate

pytestmark = pytest.mark.eval

VOCABULARY = [
    "pyproject", "repository root", "uv --no-project", "sha256",
    "resolve_provider_settings", "Kokoro", "ElevenLabs", "MCP", "ponytail", "MaluDB", "Qwen",
]
MODEL = Path.home() / ".cache/vocalize/models/whisper/ggml-large-v3-turbo-q5_0.bin"


def write_noise(path: Path, rms: int, seed: int) -> None:
    rng = random.Random(seed)
    peak = {20: 35, 40: 69, 80: 139}[rms]
    frames = b"".join(rng.randint(-peak, peak).to_bytes(2, "little", signed=True) for _ in range(32000))
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(frames)


def vocabulary_words():
    return {word.lower() for item in VOCABULARY for word in item.split() if word.lower() != "root"}


@pytest.mark.skipif(os.environ.get("VOCALIZE_EVAL") != "1", reason="set VOCALIZE_EVAL=1")
def test_vocabulary_does_not_leak_into_unrelated_audio(tmp_path, monkeypatch):
    if not MODEL.is_file():
        pytest.skip("the large-v3-turbo-q5_0 model is not installed")
    # tests/conftest.py points every model dir at tmp_path for unit tests;
    # this eval is the one place that needs the real installed model.
    from vocalize.local import whisper_manifest

    monkeypatch.setattr(whisper_manifest, "MODEL_DIR", MODEL.parent)
    stt = config.resolve_stt({})
    stt["vocabulary"] = VOCABULARY
    clips = []
    for rms in (20, 40, 80):
        path = tmp_path / f"noise-{rms}.wav"
        write_noise(path, rms, rms)
        clips.append((path.name, path, set()))
    if Path("/usr/bin/say").is_file():
        for name, words in (("yes", "Yes."), ("okay-thanks", "Okay, thanks.")):
            path = tmp_path / f"{name}.wav"
            subprocess.run(
                ["/usr/bin/say", "-o", str(path), "--data-format=LEI16@16000", "--channels=1", words],
                check=True,
            )
            clips.append((path.name, path, set(words.lower().replace(",", "").replace(".", "").split())))

    words = vocabulary_words()
    for name, wav, spoken in clips:
        for run in range(10):
            output = dictate.transcribe(wav, stt)
            print(f"{name} run {run + 1}: {output}")
            leaked = [
                word for word in words - spoken
                if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", output, re.IGNORECASE)
            ]
            assert not leaked, f"{name} run {run + 1} leaked: {', '.join(leaked)}"
