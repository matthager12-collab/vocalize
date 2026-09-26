# Intended mode for dictation: step 1 summary

Plan-project step 1, 2026-09-26. The gate before any design. Nothing is built.

## The goal, as understood

Borrow CrisperWhisper's "intended mode" idea. Its weights are out, because of the non-commercial license. Mat's scope answer was "Go for max capabilities". That covers six things:

1. Drop fillers and false starts.
2. On a self-correction, keep only the correction ("Tuesday, no, Wednesday" becomes "Wednesday").
3. Write numbers, dates, times and emails the way you would type them.
4. Recognise Mat's jargon.
5. Accept extra seconds per press for cleaner text.
6. Keep the raw take one keystroke away, as an undo.

## How dictation works today

- **Hotkey.** The menu-bar app has three fixed chords: dictate, speak, stop. Its Swift source is frozen. Any edit costs every user an Accessibility re-grant (DEC-032).
- **One process per press** (DEC-028). There is no resident helper.
- **Transcribe.** `whisper_worker.py` runs one-shot under uv. It uses pywhispercpp, turbo q5_0 and beam 5. It passes **no prompt**, though pywhispercpp supports `initial_prompt`.
- **Clean up.** `llm.cleanup_transcript` runs when `[stt] cleanup` is not `off`. The default is `off`, and Mat's own config leaves it off. Two prompts exist: `CLEANUP_PROMPT` (already drops restatements, false starts and fillers) and `VERBATIM_PROMPT`. Saying "verbatim" first switches to the second.
- **Local cleanup model.** Qwen3.5-4B 4-bit under mlx-lm, one-shot per press. Its prompt is built as token ids (DEC-027). The spike measured a cold median of **4.19 s** and a 1.1 GB peak. A warm run is 1.86 s, but only a resident model is warm, and the roadmap ruled that out.
- **Deliver.** The text is sanitised, collapsed to one line, sent to `pbcopy` on stdin, and pasted through a nonce marker.
- **Privacy** (DEC-007). A transcript is never a file, an argument, a log line or a notification.

## Constraints

- Local only. Nothing new leaves the Mac.
- Keep each worker under 1.5 GB peak memory.
- Test whether the whisper prompt leaks into the text on silence and on very short takes.
- Vocalize is public on PyPI. Package defaults reach other users. Releases are Mat's.
- No building without Mat's explicit go.

## Non-goals

- CrisperWhisper weights, or any new speech model.
- Notes (`vocalize notes`). Its long-recording path is separate.
- Cloud cleanup changes. The `claude-cli` and `anthropic` backends keep working as they do.

## Corrections and assumptions to walk, one at a time

1. **Latency.** The "about 2 seconds" quoted in chat was the warm figure. Today a local cleanup costs about 4 to 5 seconds per press.
2. **Undo versus privacy.** Keeping the raw take somewhere breaks the promise that vocalize never stores a transcript. A new hotkey means editing the frozen app.
3. **Default.** Should the package default stay `off`, with Mat's own config turning it on?
4. **Jargon list.** Where it lives, and who fills it.

## Tier

Full. There are several approaches with real trade-offs, and at least three open decisions.
