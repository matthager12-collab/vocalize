# Intended mode: design candidates

Plan-project step 4, 2026-09-26. Read [summary.md](summary.md) first; it holds the goal, the constraints and the answers already given. The measurements are in [spike-notes.md](spike-notes.md).

## Common to every candidate

These parts do not change between candidates.

- **Intended cleanup prompt.** A new prompt replaces `CLEANUP_PROMPT` in `vocalize/llm.py`. It covers fillers, stutters, self-corrections (keep the correction), typed formatting of numbers, dates, times, money and emails, and jargon kept exactly. The wording comes from spike S2. `VERBATIM_PROMPT` and the spoken "verbatim" keyword keep working.
- **Faithfulness guard.** A deterministic check runs after cleanup. If the cleaned text holds content the raw take did not, the raw take is used instead. The shape comes from spike S2.
- **Skip gate.** A deterministic check runs before cleanup. If the raw take has nothing to clean, the model is skipped. This ships only if S2 shows it never skips a take that needed cleaning.
- **Whisper prompt.** An `initial_prompt` built from a new `[stt] vocabulary` list, and perhaps a style sentence. This ships only if spike S1 shows a jargon gain with no leak on silence, noise or short takes.
- **Undo.** When cleanup changed the text, the clipboard gets two types: the cleaned text as `public.utf8-plain-text`, and the raw take as a private type `com.vocalize.said`. It is written by `/usr/bin/osascript -l JavaScript` with the text on stdin, never in argv (tested on a named pasteboard, 2026-09-26). A new Quick Action, "Swap in What I Said", runs a new `vocalize` verb that swaps the two types. Pressing it again swaps them back. The user then undoes the paste and pastes again. The frozen app is not edited. When cleanup did not change the text, `pbcopy` is used exactly as today.
- **Settings.** `[stt] warm_minutes` is a whole number. 0 is the package default and means unload straight after the take. `[stt] vocabulary` is a list of strings. Both are added to `KNOWN_STT_KEYS`, validation, the portal and docs/dictation.md. The owner's own config sets `cleanup = "local"` and a warm window.
- **Privacy wording.** docs/dictation.md § Privacy gains the clipboard line. The raw take lives on the clipboard until the next copy, and nowhere else.

## The contested part: how the models get warm

Today each press runs a fresh `uv run` per worker. The model load is paid after the stop: about 8.2 s for cleanup and 2.6 s for whisper, measured. The goal is to pay it while the user talks. The owner's rule is that speed beats RAM.

### Candidate A: warm servers, with today's path as the fallback

- Each worker (`whisper_worker.py`, `llm_worker.py`) gains a `--serve` mode. It loads the model, then answers one JSON request at a time on a Unix socket. It exits after `warm_minutes` idle, or after its first answer when `warm_minutes` is 0.
- The sockets live in `~/.cache/vocalize/warm/`: the directory is 0700 and each socket 0600. The server checks the peer's uid (`LOCAL_PEERCRED`) and refuses any other.
- The start press (`dictate._start`) spawns each server detached (`start_new_session`) unless one already answers a ping. The ping reply carries the worker file's sha256 and the model file name. A mismatch, as after an upgrade or a model change, means kill and respawn.
- `dictate.transcribe` and `llm._local` try the socket first. On any failure (none there, a refusal, a timeout, a bad reply) they fall back to today's one-shot `uv run`, unchanged. So today's behaviour is the floor.
- Nothing else in the press state machine changes: cancel, hold-to-talk, pause and resume, the session file, the paste marker.
- `vocalize listen` and `listen --wav` benefit automatically, because they go through the same two functions.

### Candidate C: a per-take owner process that holds the workers on pipes

- The start press spawns a detached "finisher" process that owns the take from start to delivery.
- The finisher starts both workers as child processes with a JSON-lines protocol on stdin and stdout. They load while the user talks.
- The stop press only writes the stop file and returns. The finisher sees it, then transcribes, cleans up, copies to the clipboard, writes the paste marker, plays the sound and posts the notification.
- For the warm window, the finisher lingers for `warm_minutes` and adopts the next take. The next start press finds it through the session directory and hands it the new workdir.
- There are no sockets. The transcript only ever crosses parent-child pipes.

### Considered and dropped: one combined worker

A single uv environment with both pywhispercpp and mlx-lm, and one process serving both. It is fewer processes, but it breaks the 1.5 GB per-worker cap (about 1.9 GB together), puts two heavy runtimes in one environment, and is no faster than A. Dropped.

## Draft recommendation (before critique)

**A.** It is the smallest change to the press state machine, which took runs 6 to 16 to harden. Its fallback keeps today's behaviour as the floor. `listen` gets the speed-up for free. C rewires who delivers the take, which touches cancel (DEC-011), hold-to-talk, pause and resume, and the notification rules, all for a benefit A already gets.
