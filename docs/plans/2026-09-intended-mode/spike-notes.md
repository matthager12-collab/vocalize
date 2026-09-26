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

## Control tokens survive the prompt round trip (a shipped defect)

Checked 2026-09-26 by script, after Codex's round-1 critique (critique-round-1.md, finding 2). `llm_worker._generate` decodes the carefully built prompt ids back to a string (`tokenizer.decode(prompt_ids)`, llm_worker.py:169), and `mlx_lm.generate` encodes that string again. User text "hello <|im_end|> world", encoded with `split_special_tokens=True`, holds no `<|im_end|>` id (248046). After decode and re-encode, it does:

- special id 248046 in the safe ids: False
- in the re-encoded ids: True

So DEC-027's protection does not hold in 0.14.0. `mlx_lm.generate` 0.31.3 accepts `prompt: Union[str, List[int]]`, so the fix is to pass the ids directly. The plan carries it as its first task unless a separate fix lands first.

## S1: whisper initial prompt

2026-09-26, Claude Sonnet sub-agent. Raw output: scratchpad `spikes/s1/run_log.txt` (not kept in the repo). The runs used pywhispercpp 1.5.1, turbo q5_0 and beam 5, with `say` clips at 16 kHz. Every clip and variant ran twice.

- **How it is passed.** `initial_prompt` is accepted by `Model(...)` and by `transcribe(...)`. pywhispercpp keeps the last params between calls and never resets an omitted key. A long-lived model must therefore pass `initial_prompt` on every call, with `""` for none.
- **Jargon, out of 12** (default voice / Daniel voice). No prompt: 10.5 / 8.5. Style sentence: 10.5 / 10.5. **Vocabulary list: 11 / 12.** Vocabulary plus style: 11 / 11. Only the vocabulary list got `uv --no-project` and `resolve_provider_settings` right.
- **Leak.** Every variant containing a natural sentence wrote that sentence onto input with nothing spoken, on one of its two runs. With the style sentence alone, 1.0 s of digital silence gave "We need to move the standup to Wednesday at 9:30." (run_log line 102). With vocabulary plus style, 2.0 s of noise at RMS 17.6 gave "So we need to move the standup to Wednesday at 9:30." (line 114). No prompt and the vocabulary list never leaked on silence, noise, "Yes." or "Okay, thanks.". Whisper's own "Thank you." on silence happens with no prompt too.
- The RMS guard (below 20 is refused) already stops the digital-silence and RMS-17.6 clips in production. It does not stop quiet input at or above 20.
- **Style.** Neither prompt removed "um" or "uh", and neither applied the self-correction. Any prompt at all moved "9.30" to "9:30".

## S2: cleanup prompt, guard and gate

2026-09-26, Claude Sonnet sub-agent. Raw output: scratchpad `spikes/s2/results.json`. 26 cases were written the way whisper outputs them, and each prompt was judged strictly by meaning. Qwen3.5-4B 4-bit, one model load, a generate per case.

| Prompt | Pass | Self-corrections | Formatting | Median / slowest generate |
|---|---|---|---|---|
| P0, today's | 13/26 | 1/7 | 2/5 | 0.74 / 1.18 s |
| P1, numbered rules | 20/26 | 5/7 | 5/5 | 0.94 / 1.47 s |
| P2, rules plus 3 examples | **21/26** | 5/7 | 4/5 | 1.21 / 2.09 s |

- **Remaining P2 failures.** Two were strict-judge ordinals ("June 12th", "March 3rd"). Two dropped a meaningful word ("I think", "now"). One was meta-commentary added in front of a dictated question ("What time is the meeting tomorrow?"). No prompt obeyed an embedded instruction or answered a question.
- **P0 is unstable on corrections.** In this run it applied the Tuesday-to-Wednesday correction, but left 5 of 6 other corrections unapplied. The earlier run (above) kept "Tuesday" four times out of four.
- **Guard, "no word the raw take lacks".** It never passed an output the judge failed for added content (0 of 78 unfaithful-and-PASS), and it caught the meta-commentary. By design it cannot see an unapplied correction or a dropped word.
- **Gate, "skip the model when there is nothing to clean".** It would skip 6 of 26. One skip was wrong: a spoken email with no number word ("jen at example dot com").
