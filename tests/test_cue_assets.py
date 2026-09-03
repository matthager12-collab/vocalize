"""The shipped `[stt] cues` word files: present, small, and well-formed.

Not a test of speech quality — just the shape that `dictate._play` and the
packaged wheel both depend on: a real mono 16-bit WAV, short and light
enough to ship and to speak without lagging behind the sound it replaces.
"""

import wave
from pathlib import Path

import pytest

from vocalize.dictate import _CUE_WORDS

MAX_SECONDS = 1.5
MAX_BYTES = 80_000


@pytest.mark.parametrize("path", list(_CUE_WORDS.values()), ids=lambda p: p.name)
def test_a_cue_word_file_is_a_small_mono_16_bit_wav(path: Path):
    assert path.is_file(), f"missing cue asset: {path}"
    assert path.stat().st_size <= MAX_BYTES

    with wave.open(str(path), "rb") as reader:
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        duration = reader.getnframes() / reader.getframerate()
    assert duration <= MAX_SECONDS
