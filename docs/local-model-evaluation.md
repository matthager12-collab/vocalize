# Real-model evaluations

The normal test suite uses fakes. A separate runner checks the real models:
cleanup quality, that the Whisper vocabulary list does not leak into
unrelated audio, and the memory use of the warm servers. These runs load
real models and take minutes, so they are never part of `pytest`.

## Before you run it

- Install the models first (`vocalize local install --stt --engine whisper`
  and `vocalize local install --llm`). The runner never downloads anything;
  a missing model fails the run.
- Close other programs that use a lot of memory. The warm stage loads Qwen
  and Whisper at the same time.
- Allow about an hour.

## Run it

```bash
python tests/eval_runner.py all --exclusive-window-confirmed
```

Instead of `all` you can name one stage: `cleanup`, `whisper` or `warm`.
`--exclusive-window-confirmed` is your statement that nothing else on the Mac
needs the memory during the run.

To use Python environments that already have the engines, point the runner
at them:

```bash
VOCALIZE_LLM_PYTHON=<path to venv>/bin/python \
VOCALIZE_WHISPER_PYTHON=<path to venv>/bin/python \
python tests/eval_runner.py all --exclusive-window-confirmed
```

## What the runner controls

- Each stage has 900 seconds. The run stops at the first stage that fails or
  runs out of time.
- Each stage runs in its own process group. When the stage ends, for any
  reason, the whole group is stopped, with 10 seconds allowed for cleanup.
- Temporary files go in a private folder that is removed afterwards.
- Test audio is made with `say -o` into files. Nothing is played and the
  microphone is not used.
- Stages run one at a time.

## What passes

| Stage | Passes when |
|---|---|
| `cleanup` | At least 21 ordinary cases are cleaned correctly, and every adversarial case is either cleaned correctly or kept as the original text by the faithful guard |
| `whisper` | Noise never picks up vocabulary words, and spoken clips contain their expected words |
| `warm` | Qwen peaks at 3.6 GB or less, Whisper at 1.0 GB or less, both together at 4.6 GB or less, with one server per engine |

A stage that runs out of time is an incomplete run, not proof that a model is
poor. Run it again rather than raising the limit; the limit cannot be
changed.

Plain `pytest` refuses to run these tests; use the runner. The runner finds
the engines installed under your home folder by itself; the two variables
above are needed only to use other environments.

## Last result

On 2026-11-21, on a Mac Studio (Apple M5 Max, 128 GB, macOS 27.2), the
`cleanup` stage passed twice: 25 of 26 everyday cases and 10 of 10 guarded
cases (8 adversarial and 2 instruction-like), in about 50 s the first time
and 48.55 s the second. The second run used the runner with no extra
variables. The one failed case was a self-correction with "or
rather", which the faithful guard kept as the original text. The `whisper`
and `warm` stages were not run that day. See
[measurements.md](plans/2026-11-local-only/measurements.md).
