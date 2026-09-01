"""Kokoro's resident synthesis worker. Runs under uv, never imported.

This file ships inside the vocalize package but is not part of it: uv
executes it with its own Python 3.12 and its own copy of kokoro-onnx, so
vocalize's interpreter never sees onnxruntime, numpy or torch. That is
the whole reason it exists, and it is why nothing here imports vocalize.

kokoro_onnx and numpy are imported inside functions, so the file can be
byte-compiled, linted and AST-checked anywhere — including in vocalize's
own test run, where neither package is installed.

Protocol (`--serve`): the 326 MB model loads once, then each line of
stdin is one request

    {"id": 1, "text": "...", "out": "/tmp/x/1.wav"}

and each line of stdout is one reply

    {"id": 1, "ok": true}
    {"id": 1, "ok": false, "error": "one line, no newline"}

The text arrives on stdin and never through argv, where every other
process on the machine could read it. It is never printed back, either:
error replies carry the exception's own words, clipped to one short line.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import wave

# Kokoro's output rate. Duplicated from the manifest on purpose — this
# file must not import vocalize.
SAMPLE_RATE = 24000

# Error strings are clipped rather than passed through: an exception is
# not supposed to quote the input, and a reply is one line by contract.
_MAX_ERROR_CHARS = 200


def _one_line(exc: BaseException) -> str:
    """An exception as a single short line, safe to put on a pipe."""
    message = " ".join(str(exc).split()) or exc.__class__.__name__
    return message[:_MAX_ERROR_CHARS]


def _kokoro_class():
    """The Kokoro class, imported on use.

    Also the seam the tests replace: importing this module never pulls in
    kokoro_onnx, so a stub can stand in for the real runtime.
    """
    from kokoro_onnx import Kokoro

    return Kokoro


def write_wav(samples, path: str, sample_rate: int = SAMPLE_RATE) -> None:
    """float32 samples in [-1, 1] to a 16-bit PCM mono WAV, via stdlib wave."""
    import numpy as np

    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(path, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(int(sample_rate))
        writer.writeframes(pcm.tobytes())


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="kokoro_worker",
        description="Synthesize speech with kokoro-onnx. Text arrives on stdin.",
    )
    parser.add_argument("--model", required=True, help="Path to kokoro-v1.0.onnx")
    parser.add_argument("--voices", required=True, help="Path to voices-v1.0.bin")
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--lang", default="en-us")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--serve", action="store_true", help="Read JSON requests from stdin.")
    mode.add_argument("--list-voices", action="store_true", help="Print the voice ids.")
    mode.add_argument("--selftest", action="store_true", help="Load and say one word.")
    return parser.parse_args(argv)


def serve(model, args, stdin=None, stdout=None) -> int:
    """One JSON request per stdin line until EOF. Always returns 0."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout

    for line in stdin:
        line = line.strip()
        if not line:
            continue

        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            samples, rate = model.create(
                request["text"],
                voice=args.voice,
                speed=args.speed,
                lang=args.lang,
            )
            write_wav(samples, request["out"], rate)
            reply = {"id": request_id, "ok": True}
        except Exception as exc:  # noqa: BLE001 — one bad request must not end the session
            reply = {"id": request_id, "ok": False, "error": _one_line(exc)}

        stdout.write(json.dumps(reply) + "\n")
        stdout.flush()

    return 0


def main(argv=None) -> int:
    # A terminated session should die quietly, not dump a traceback into
    # the parent's stderr.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    args = parse_args(argv)
    try:
        model = _kokoro_class()(args.model, args.voices)
    except Exception as exc:  # noqa: BLE001 — onnxruntime raises anything; exit 1 either way
        print(f"kokoro: could not load the model: {_one_line(exc)}", file=sys.stderr)
        return 1

    if args.list_voices:
        for voice in sorted(model.get_voices()):
            print(voice)
        return 0

    if args.selftest:
        import tempfile
        from pathlib import Path

        try:
            samples, rate = model.create(
                "vocalize", voice=args.voice, speed=args.speed, lang=args.lang
            )
            with tempfile.TemporaryDirectory() as tmp:
                write_wav(samples, str(Path(tmp) / "selftest.wav"), rate)
        except Exception as exc:  # noqa: BLE001 — the point is to report, not to crash
            print(f"kokoro: selftest failed: {_one_line(exc)}", file=sys.stderr)
            return 1
        print("ok")
        return 0

    return serve(model, args)


if __name__ == "__main__":
    sys.exit(main())
