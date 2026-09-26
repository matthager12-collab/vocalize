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
7. Make the speed-versus-RAM trade a user setting (Mat, 2026-09-26: "can we make that a config option for users?"). Proposed: one whole-number setting, the minutes the models stay loaded after a take. 0 means unload straight after the take. The package default is 0, so no other user finds memory held after dictating. Mat's config sets a longer window. Preload during the take is always on and has no setting. It holds whisper and the cleanup model at the same time, about 1.9 GB, where today they run one after the other. The local model already refuses to install below 12 GiB of RAM (`llm_manifest.MIN_RAM_BYTES`). The setting's name and default are a public config contract, so they go to decision round 1.

## How dictation works today

- **Hotkey.** The menu-bar app has three fixed chords: dictate, speak, stop. Its Swift source is frozen. Any edit costs every user an Accessibility re-grant (DEC-032).
- **One process per press** (DEC-028). There is no resident helper.
- **Transcribe.** `whisper_worker.py` runs one-shot under uv. It uses pywhispercpp, turbo q5_0 and beam 5. It passes **no prompt**, though pywhispercpp supports `initial_prompt`.
- **Clean up.** `llm.cleanup_transcript` runs when `[stt] cleanup` is not `off`. The default is `off`, and Mat's own config leaves it off. Two prompts exist: `CLEANUP_PROMPT` (already drops restatements, false starts and fillers) and `VERBATIM_PROMPT`. Saying "verbatim" first switches to the second.
- **Local cleanup model.** Qwen3.5-4B 4-bit under mlx-lm, one-shot per press. Its prompt is built as token ids (DEC-027). The spike measured a cold median of **4.19 s** and a 1.1 GB peak. A warm run is 1.86 s, but only a resident model is warm, and the roadmap ruled that out.
- **Deliver.** The text is sanitised, collapsed to one line, sent to `pbcopy` on stdin, and pasted through a nonce marker.
- **Privacy** (DEC-007). A transcript is never a file, an argument, a log line or a notification.

## Constraints

- **Speed beats RAM** whenever a trade-off has to be made (Mat, 2026-09-26).
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

1. **Latency.** The "about 2 seconds" quoted in chat was the warm figure. The first correction said 4 to 5 seconds; the measurement says about 11 seconds after the stop (see below).
2. **Undo versus privacy.** Keeping the raw take somewhere breaks the promise that vocalize never stores a transcript. A new hotkey means editing the frozen app.
3. **Default.** Should the package default stay `off`, with Mat's own config turning it on?
4. **Jargon list.** Where it lives, and who fills it.

## Resolved so far

- **1. Latency: preload on press, plus a warm window.** Mat's rule, 2026-09-26: "Go for speed at the expense of Ram if a decision has to be made." Both workers start loading when the take starts. After the take they stay loaded for a warm window of a set number of minutes, then exit. Back-to-back dictations and short takes are then warm too. RAM is held for the window, never all day. The window length is a two-way door, tuned from the spike. Measured basis: [spike-notes.md](spike-notes.md).
- **2. Undo: A, the clipboard holds both** (Mat, 2026-09-26). The cleaned text is the ordinary clipboard text. The raw take rides along as a second, private clipboard type. A "Paste What I Said" Quick Action swaps it in. Nothing is written to a file, and the frozen app is not edited. The raw text is gone as soon as anything else is copied. The privacy section of docs/dictation.md gains a line saying so.
- **3. Default: decided as a two-way door.** The package default stays `off`, so no other user is surprised. Mat's own config turns cleanup on.
- **4. Jargon list: decided as a two-way door.** A plain list in the config, filled by Mat. The design fixes the exact key.

## Standing authority

Mat, 2026-09-26: "I'll take your rec on other questions." For this plan, Claude decides the remaining questions on its own recommendation. Each one is recorded with its reasoning, so Mat can catch a wrong call afterwards. This covers deciding, not building. Building still needs Mat's explicit go.

## Tier

Full. There are several approaches with real trade-offs, and at least three open decisions.
