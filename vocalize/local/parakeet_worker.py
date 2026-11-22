"""Parakeet's transcription worker. Runs under uv, never imported.

Like whisper_worker.py, this file ships inside the vocalize package but is
executed by uv with its own Python 3.12 and its own copy of parakeet-mlx,
so vocalize's interpreter never sees mlx or numpy.

Protocol: `--transcribe <wav> --model <dir>` prints exactly one JSON line
on stdout and exits 0 once the model ran:

    {"ok": true, "text": "..."}
    {"ok": false, "error": "one line, no newline"}

With `--segments` the recording is decoded in overlapping chunks
(CHUNK_SECONDS, OVERLAP_SECONDS), `{"progress": seconds}` lines may come
first, and the final line also carries timestamped segments:

    {"ok": true, "text": "...", "segments": [{"start": 0.0, "end": 2.5, "text": "..."}]}

A refusal (a model folder that is not an absolute existing directory, or
a recording that is not a readable 16 kHz mono 16-bit WAV) happens before
any model code is imported, exits 2 and prints one fixed line on stderr
that never names the path.

parakeet-mlx's own file loader shells out to ffmpeg, which vocalize does
not require. The WAV is checked and decoded here with the standard
library instead, so only a 16 kHz mono 16-bit file ever reaches the
model. The model directory is read offline: nothing here may touch the
network. parakeet-mlx's `from_pretrained` asks the Hub before it looks at
a folder, so only an absolute, resolved, existing directory is passed.
Only library calls are used, never the package's command-line tool. # ponytail: one process per take (about 2 s with load and
compile); a warm server like whisper's if that proves too slow.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import wave

# --segments decodes long audio in overlapping chunks, so memory stays
# bounded by the chunk, not the recording.
CHUNK_SECONDS = 120.0
OVERLAP_SECONDS = 15.0

_MAX_ERROR_CHARS = 200
_EXPECTED_FORMAT = (1, 2, 16000)  # (channels, sample width bytes, frame rate)
_RATE = _EXPECTED_FORMAT[2]
_MAX_BYTES = 200 * 1024**2


class Refused(ValueError):
    """A fixed, path-free reason to stop before any model code runs."""


def _one_line(exc: BaseException) -> str:
    message = " ".join(str(exc).split()) or exc.__class__.__name__
    return message[:_MAX_ERROR_CHARS]


def model_dir(value: str) -> str:
    """The model folder as an absolute resolved path, or Refused.

    A relative path or bare name would be looked up on the Hub first.
    """
    if not isinstance(value, str) or not value or "\x00" in value or not os.path.isabs(value):
        raise Refused("the model folder must be an absolute path")
    resolved = os.path.realpath(value)
    if not os.path.isdir(resolved):
        raise Refused("the model folder does not exist")
    return resolved


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
                raise Refused("the recording is not a regular WAV file of a sane size")
            with wave.open(source, "rb") as reader:
                got = (reader.getnchannels(), reader.getsampwidth(), reader.getframerate())
                if got != _EXPECTED_FORMAT:
                    channels, width, rate = got
                    raise Refused(
                        f"expected 16 kHz mono 16-bit WAV, got {rate} Hz, "
                        f"{channels} channel(s), {width * 8}-bit"
                    )
                frames = reader.readframes(reader.getnframes())
    except (OSError, wave.Error, EOFError):
        # The exception's words can carry the path; the reason is fixed.
        raise Refused("the recording is not a valid WAV file") from None
    return frames


def _load(folder: str):
    # Offline by construction: the weights are already on disk and verified.
    folder = model_dir(folder)  # checked again here, before any import
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    from parakeet_mlx import from_pretrained

    return from_pretrained(folder)


def transcribe(model, frames: bytes) -> str:
    import mlx.core as mx
    import numpy as np
    from parakeet_mlx.audio import get_logmel

    # The worker decoded the WAV itself: parakeet_mlx.audio.load_audio,
    # which runs ffmpeg from PATH, is never called.
    # float32, not bfloat16: get_logmel views the FFT output as the input
    # dtype, and bfloat16 makes the mel matrix the wrong shape.
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    mel = get_logmel(mx.array(audio), model.preprocessor_config)
    return " ".join(model.generate(mel)[0].text.split())


def _audio(frames: bytes):
    """The decoded recording as the float32 mx.array parakeet-mlx's own
    loader would return, without running ffmpeg."""
    import mlx.core as mx
    import numpy as np

    return mx.array(np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0)


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def segments_from(result, duration: float) -> tuple[str, list[dict]]:
    """(text, [{start, end, text}]) from an AlignedResult, defensively.

    Built from the result's sentences; when they carry no usable timing,
    one segment covers the whole recording.
    """
    if isinstance(result, (list, tuple)):
        result = result[0] if result else None
    sentences = getattr(result, "sentences", None)
    if not isinstance(sentences, (list, tuple)):
        sentences = ()
    raw_text = getattr(result, "text", "")
    text = " ".join(raw_text.split()) if isinstance(raw_text, str) else ""
    if not text:
        text = " ".join(" ".join(s.text.split()) for s in sentences
                        if isinstance(getattr(s, "text", None), str)).strip()
    segments = []
    for sentence in sentences:
        words = getattr(sentence, "text", None)
        start, end = _number(getattr(sentence, "start", None)), _number(getattr(sentence, "end", None))
        if not isinstance(words, str) or start is None or end is None:
            segments = []  # no usable timing: one segment covers it all
            break
        words = " ".join(words.split())
        if words:
            segments.append({"start": start, "end": end, "text": words})
    if segments:
        # The engine's order is the spoken order. Sentence times can overlap
        # where two chunks join, so sorting by start would swap sentences;
        # the times are clamped to be non-decreasing instead.
        previous = 0.0
        for segment in segments:
            segment["start"] = round(min(max(segment["start"], previous), duration), 3)
            segment["end"] = round(min(max(segment["end"], segment["start"]), duration), 3)
            previous = segment["start"]
        return text, segments
    return text, ([{"start": 0.0, "end": round(duration, 3), "text": text}] if text else [])


def transcribe_segments(model, frames: bytes, progress=None) -> dict:
    """The library's chunked `transcribe`, fed the worker's own decode.

    `transcribe` reads its file through the module-level `load_audio`,
    which runs ffmpeg; for this one call that name is swapped for a
    loader that returns the already-checked WAV. If the name is not there
    to swap, the call is refused rather than letting ffmpeg run.
    """
    import mlx.core as mx

    method = getattr(type(model), "transcribe", None)
    names = getattr(method, "__globals__", None)
    if not isinstance(names, dict) or "load_audio" not in names:
        raise RuntimeError("parakeet-mlx transcribe has an unexpected shape")
    audio = _audio(frames)
    duration = (len(frames) // 2) / _RATE

    def decoded(_path, sampling_rate, _dtype=None):  # never reads a file
        if sampling_rate != _RATE:
            raise RuntimeError("unexpected sample rate")
        return audio

    def chunk_callback(position, _total):
        if progress is not None:
            progress(min(position / _RATE, duration))

    # The library's loader is only ever replaced here, never called.
    original = names["load_audio"]
    names["load_audio"] = decoded
    try:
        result = model.transcribe(
            "vocalize-decoded.wav", dtype=mx.float32,
            chunk_duration=CHUNK_SECONDS, overlap_duration=OVERLAP_SECONDS,
            chunk_callback=chunk_callback,
        )
    finally:
        names["load_audio"] = original
    text, segments = segments_from(result, duration)
    return {"ok": True, "text": text, "segments": segments}


def _print_progress(seconds: float) -> None:
    print(json.dumps({"progress": round(seconds, 3)}), flush=True)


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
    parser.add_argument(
        "--segments", action="store_true",
        help="With --transcribe: decode in chunks, emit progress lines and timestamped segments",
    )
    args = parser.parse_args(argv)
    if args.segments and not args.transcribe:
        parser.error("--segments needs --transcribe")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        folder = model_dir(args.model)
        frames = None if args.selftest else read_wav(args.transcribe)
    except Refused as exc:
        print(f"parakeet: refused: {exc}", file=sys.stderr)
        return 2
    try:
        if args.selftest:
            transcribe(_load(folder), _selftest_frames())
            print("ok")
            return 0
        if args.segments:
            print(json.dumps(transcribe_segments(_load(folder), frames, _print_progress)))
            return 0
        print(json.dumps({"ok": True, "text": transcribe(_load(folder), frames)}))
        return 0
    except Exception as exc:  # noqa: BLE001 -- the contract is one JSON line, never a traceback
        if args.selftest:
            print(f"parakeet: selftest failed: {_one_line(exc)}", file=sys.stderr)
            return 1
        print(json.dumps({"ok": False, "error": _one_line(exc)}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
