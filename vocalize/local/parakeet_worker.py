"""Parakeet's transcription worker. Runs under uv, never imported.

Like whisper_worker.py, this file ships inside the vocalize package but is
executed by uv with its own Python 3.12 and its own copy of parakeet-mlx,
so vocalize's interpreter never sees mlx or numpy.

Protocol: `--transcribe <wav> --model <dir>` prints exactly one JSON line
on stdout and exits 0 either way:

    {"ok": true, "text": "..."}
    {"ok": false, "error": "one line, no newline"}

parakeet-mlx's own file loader shells out to ffmpeg, which vocalize does
not require. The WAV is checked and decoded here with the standard
library instead, so only a 16 kHz mono 16-bit file ever reaches the
model. The model directory is read offline: nothing here may touch the
network. # ponytail: one process per take (about 2 s with load and
compile); a warm server like whisper's if that proves too slow.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import wave

_MAX_ERROR_CHARS = 200
_EXPECTED_FORMAT = (1, 2, 16000)  # (channels, sample width bytes, frame rate)
_MAX_BYTES = 200 * 1024**2


def _one_line(exc: BaseException) -> str:
    message = " ".join(str(exc).split()) or exc.__class__.__name__
    return message[:_MAX_ERROR_CHARS]


def read_wav(path: str) -> bytes:
    """The PCM frames of a 16 kHz mono 16-bit WAV. Raises ValueError otherwise.

    Opened without following symlinks and size-capped, so a swapped or
    hostile path cannot make the worker read something else.
    """
    import stat

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES:
                raise ValueError("not a regular WAV file of a sane size")
            with wave.open(source, "rb") as reader:
                got = (reader.getnchannels(), reader.getsampwidth(), reader.getframerate())
                if got != _EXPECTED_FORMAT:
                    channels, width, rate = got
                    raise ValueError(
                        f"expected 16 kHz mono 16-bit WAV, got {rate} Hz, "
                        f"{channels} channel(s), {width * 8}-bit"
                    )
                frames = reader.readframes(reader.getnframes())
    except (OSError, wave.Error, EOFError) as exc:
        raise ValueError(f"not a valid WAV file: {_one_line(exc)}") from exc
    return frames


def _load(model_dir: str):
    # Offline by construction: the weights are already on disk and verified.
    os.environ["HF_HUB_OFFLINE"] = "1"
    from parakeet_mlx import from_pretrained

    return from_pretrained(model_dir)


def transcribe(model, frames: bytes) -> str:
    import mlx.core as mx
    import numpy as np
    from parakeet_mlx.audio import get_logmel

    # float32, not bfloat16: get_logmel views the FFT output as the input
    # dtype, and bfloat16 makes the mel matrix the wrong shape.
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    mel = get_logmel(mx.array(audio), model.preprocessor_config)
    return " ".join(model.generate(mel)[0].text.split())


def _selftest_frames() -> bytes:
    import numpy as np

    rate = 16000
    t = np.linspace(0, 0.5, int(rate * 0.5), endpoint=False)
    return (np.sin(2 * np.pi * 440 * t) * 3000).astype("<i2").tobytes()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="parakeet_worker",
        description="Transcribe speech with Parakeet on MLX. Only the file path is an argument.",
    )
    parser.add_argument("--model", required=True, help="Directory holding config.json and model.safetensors")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--transcribe", metavar="WAV", help="Path to a 16 kHz mono 16-bit WAV")
    mode.add_argument("--selftest", action="store_true", help="Load the model and transcribe a tone")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.selftest:
            transcribe(_load(args.model), _selftest_frames())
            print("ok")
            return 0
        frames = read_wav(args.transcribe)
        print(json.dumps({"ok": True, "text": transcribe(_load(args.model), frames)}))
        return 0
    except Exception as exc:  # noqa: BLE001 -- the contract is one JSON line, never a traceback
        if args.selftest:
            print(f"parakeet: selftest failed: {_one_line(exc)}", file=sys.stderr)
            return 1
        print(json.dumps({"ok": False, "error": _one_line(exc)}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
