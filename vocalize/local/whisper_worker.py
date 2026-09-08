"""Whisper's transcription worker. Runs under uv, never imported.

This file ships inside the vocalize package but is not part of it: uv
executes it with its own Python 3.12 and its own copy of pywhispercpp, so
vocalize's interpreter never sees pywhispercpp or numpy. That is the
whole reason it exists, and it is why nothing here imports vocalize.

pywhispercpp and numpy are imported inside functions, so the file can be
byte-compiled, linted and AST-checked anywhere — including in vocalize's
own test run, where neither package is installed.

Protocol: `--transcribe <wav> --model <abs .bin> --language <code>`
prints exactly one JSON line on stdout and exits 0 either way:

    {"ok": true, "text": "..."}
    {"ok": false, "error": "one line, no newline"}

The WAV path arrives as an argument — unlike dictated text, a file path
is not the secret here — but its CONTENTS never reach argv, a log, or a
second process; only the transcribed text does, and only on stdout.

`--selftest` loads the model and transcribes 0.5s of a generated tone,
printing `ok`. This is where the one-time Metal shader compile (~8s on
the reference Mac) gets paid, during `local install --stt`, never during
a dictation.
"""

from __future__ import annotations

import argparse
import json
import sys
import wave

# Error strings are clipped rather than passed through: an exception is
# not supposed to quote the input, and a reply is one line by contract.
_MAX_ERROR_CHARS = 200

# whisper.cpp's own default is min(4, hardware_concurrency()); pinned
# here rather than left to that default so behavior does not shift with
# whatever machine happens to run it.
_N_THREADS = 4

# The recorder's contract (design § Recorder contract): 16 kHz mono
# 16-bit LPCM. Anything else is untrusted input to this boundary and is
# refused rather than handed to whisper.cpp.
_EXPECTED_FORMAT = (1, 2, 16000)  # (channels, sample width bytes, frame rate)


def _one_line(exc: BaseException) -> str:
    """An exception as a single short line, safe to put on a pipe."""
    message = " ".join(str(exc).split()) or exc.__class__.__name__
    return message[:_MAX_ERROR_CHARS]


def _model_class():
    """The pywhispercpp Model class, imported on use.

    Also the seam the tests replace: importing this module never pulls in
    pywhispercpp, so a stub can stand in for the real runtime.
    """
    from pywhispercpp.model import Model

    return Model


def _check_wav(path: str) -> str | None:
    """None if `path` is a 16 kHz mono 16-bit WAV; an error message otherwise.

    The recorder always produces exactly this format, but this worker
    treats it as untrusted input rather than assuming that holds — a
    malformed or hand-crafted file must fail loudly here, never reach
    whisper.cpp's C code.
    """
    try:
        with wave.open(path, "rb") as reader:
            got = (reader.getnchannels(), reader.getsampwidth(), reader.getframerate())
    except (OSError, wave.Error, EOFError) as exc:
        return f"not a valid WAV file: {_one_line(exc)}"
    if got != _EXPECTED_FORMAT:
        channels, width, rate = got
        return (
            f"expected 16 kHz mono 16-bit WAV, got {rate} Hz, "
            f"{channels} channel(s), {width * 8}-bit"
        )
    return None


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="whisper_worker",
        description="Transcribe speech with whisper.cpp. Only the file path is an argument.",
    )
    parser.add_argument("--model", required=True, help="Path to a ggml .bin model file")
    parser.add_argument("--language", default="en")
    parser.add_argument(
        "--beam-size", type=int, default=5, choices=range(1, 9), metavar="N",
        help="1 keeps whisper.cpp's greedy decoder; 2-8 turns on beam search with N beams",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--transcribe", metavar="WAV", help="Path to a 16 kHz mono 16-bit WAV")
    mode.add_argument("--selftest", action="store_true", help="Load the model and say one word")
    return parser.parse_args(argv)


def _model_kwargs(beam_size: int) -> dict:
    """The decode strategy, as pywhispercpp Model kwargs.

    Greedy (0.10.x's default, `beam_size` 1) ran words together on fast
    speech ("toget" for "to get", issue #4); beam search with five beams is
    whisper.cpp's own default for the beam strategy and is the fix. The
    kwargs follow pywhispercpp 1.5.1's public API: a strategy selector plus
    a `beam_search` dict forwarded to `whisper_full_params`. The dict must
    carry both of whisper.cpp's beam fields — the binding indexes `patience`
    unconditionally and a dict without it fails with KeyError before the
    model loads; -1.0 is whisper.cpp's "no patience factor" default.
    """
    if beam_size <= 1:
        return {}
    return {
        "params_sampling_strategy": 1,
        "beam_search": {"beam_size": beam_size, "patience": -1.0},
    }


def _join_segments(texts) -> str:
    """Segment texts joined with exactly one space between them.

    `small.en` emits every segment with a leading space, so a plain
    `"".join` looked right; the turbo models emit none, and sentences ran
    together ("working.I want"). Stripping each segment and joining with
    one space gives the same text for both model families.
    """
    return " ".join(part for part in (text.strip() for text in texts) if part)


def transcribe(model, wav_path: str, language: str) -> dict:
    """One transcription attempt -> the reply dict `main` prints. Never raises."""
    error = _check_wav(wav_path)
    if error is not None:
        return {"ok": False, "error": error}
    try:
        segments = model.transcribe(wav_path, language=language)
        text = _join_segments(segment.text for segment in segments)
    except Exception as exc:  # noqa: BLE001 -- whisper.cpp can raise anything; report, don't crash
        return {"ok": False, "error": _one_line(exc)}
    return {"ok": True, "text": text}


def _write_selftest_wav(path: str) -> None:
    """0.5s of a generated tone, in the exact format the worker requires."""
    import numpy as np

    rate = 16000
    t = np.linspace(0, 0.5, int(rate * 0.5), endpoint=False)
    samples = (np.sin(2 * np.pi * 440 * t) * 3000).astype("<i2")
    with wave.open(path, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(samples.tobytes())


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        model = _model_class()(
            args.model, n_threads=_N_THREADS, **_model_kwargs(args.beam_size)
        )
    except Exception as exc:  # noqa: BLE001 -- pywhispercpp can raise anything
        if args.selftest:
            print(f"whisper: could not load the model: {_one_line(exc)}", file=sys.stderr)
            return 1
        # --transcribe's contract holds even when the model itself is
        # broken: exactly one JSON line, exit 0.
        print(json.dumps({"ok": False, "error": _one_line(exc)}))
        return 0

    if args.selftest:
        import tempfile
        from pathlib import Path

        try:
            with tempfile.TemporaryDirectory() as tmp:
                wav_path = str(Path(tmp) / "selftest.wav")
                _write_selftest_wav(wav_path)
                reply = transcribe(model, wav_path, args.language or "en")
        except Exception as exc:  # noqa: BLE001 -- the point is to report, not to crash
            print(f"whisper: selftest failed: {_one_line(exc)}", file=sys.stderr)
            return 1
        if not reply.get("ok"):
            print(f"whisper: selftest failed: {reply.get('error')}", file=sys.stderr)
            return 1
        print("ok")
        return 0

    print(json.dumps(transcribe(model, args.transcribe, args.language)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
