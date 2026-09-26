# Spike notes: intended mode

Measurements on the reference Mac (Mac mini, Apple M4, 16 GB). Each run is a throwaway script under the same uv runtime pins vocalize uses. Nothing in `vocalize/` changed.

## Where the seconds go, one press, 2026-09-26

The local cleanup model is Qwen3.5-4B 4-bit under `mlx-lm==0.31.3`. Input: a 32-word disfluent dictation with a self-correction, under today's `CLEANUP_PROMPT` plus the data-boundary sentence. Two runs, `/usr/bin/time -l` around `uv run --offline`:

- uv start plus import: 1.3 to 1.6 s
- model load: 3.0 to 3.4 s
- first generate: 2.1 to 2.3 s; a second generate in the same process: 1.7 to 1.9 s
- whole process: **8.2 to 9.5 s real**
- peak memory read as 0.64 GB on one run and 2.02 GB on the other. That disagrees with the earlier 1.1 GB figure (roadmap spike-notes § LLM) and needs a clean measurement before any number is trusted.

Whisper is `pywhispercpp==1.5.1` with `ggml-large-v3-turbo-q5_0.bin` at beam 5. Input: an 8-second `say` clip, 16 kHz mono. Two runs:

- import: 2.38 s on the first run, 0.07 s after
- model load: 0.19 to 0.27 s
- transcribe: 1.13 to 1.18 s
- whole process: **2.58 s real when warm**

**Correction to the summary.** The roadmap's 4.19 s cold figure left out uv start and import. Today a press with local cleanup costs about 2.6 + 8.2, or roughly **11 s** after the stop. Without cleanup, which is Mat's current setup, it costs about 2.6 s.

## Quality finding

Today's `CLEANUP_PROMPT` turned "move the standup to uh Tuesday no Wednesday at nine thirty" into "move the standup to Tuesday at 9:30". **It kept the wrong day and dropped the correction.** It did this identically on all four generates. Fillers and doubled words were removed correctly.

Whisper alone already wrote "9:30" (once "9.30"), and kept "PyProject" and "repository root" on the synthetic voice.

## Preload on press: estimate, not yet measured

Load both workers when the take starts. They wait for the stop, then exit with the take. The estimate uses the numbers above:

- whisper after stop: about 1.1 s, down from 2.6 s
- cleanup after stop: about 2.1 s, down from 8.2 s
- total after stop: **about 3.2 s**, down from about 11 s
- nothing stays loaded between dictations
- a take shorter than about 6 seconds still waits for the part of the load that has not finished
