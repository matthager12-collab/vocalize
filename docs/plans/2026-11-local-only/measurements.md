# Measurements: vocalize, local only

This is the public record of the engine measurements behind the 1.0.0
release. It answers tasks T-31, T-32 and T-35 in [plan.md](./plan.md). The
thresholds come from [verification.md](./verification.md), Phase 3.

Real engines were run four times on the same day. Each run used a copy of
the code that was frozen for that run and not edited during it.

| Run | Code | What it covered |
|---|---|---|
| 1 | an earlier build | Kokoro and Whisper: install, speed, memory, word errors, silence, offline |
| 2 | the same earlier build | Parakeet and the cleanup model: install, speed, memory, word errors, long files, silence and noise, offline, the cleanup evaluation |
| 3 | a later build, with chunked Parakeet file transcription and vocabulary corrections | `vocalize transcribe` on 10 and 60 minute files, vocabulary, the warm Whisper server, the removal actions, offline |
| 4 | the release candidate | the fixes for what runs 1 to 3 found |

## Setup

| Item | Value |
|---|---|
| Date | 2026-11-21 |
| Machine | Mac Studio, Apple M5 Max, 128 GB memory, macOS 27.2 |
| uv | 0.13.7 |
| Home folder | a scratch home folder; every command ran with `HOME` pointed at it |

Nothing was played aloud and no microphone was used. Speech was written to
files (`speak-file … --no-play -o FILE`, or the Kokoro engine called
directly, which returns the audio as data). Every transcription was of a
saved file, through `vocalize listen --wav FILE` or `vocalize transcribe
FILE`. Runs 3 and 4 reused the engines installed in runs 1 and 2, so they
installed nothing.

## Method

- **Install time** is the wall time of `vocalize local install …  --yes`
  under `/usr/bin/time -l`. Most of it is download time, which depends on
  the network at that moment.
- **Disk use** is `du -sh` of the model folder and of the engine's
  environment folder.
- **Memory** is the largest resident memory of the engine process. For
  one-shot runs it is the "maximum resident set size" that `/usr/bin/time
  -l` reports for the command and its children. For a resident worker it
  is the peak of `ps` samples. Parakeet and the cleanup model run on MLX,
  which keeps graphics buffers in unified memory that this figure may not
  fully count, so treat their memory figures as a lower bound.
- **Speed** for Kokoro is the real-time factor: seconds of work for each
  second of audio produced. "First audio" is the time until the first
  piece's audio exists, which is when playback could start.
- **Word error rate** compares the transcript with the text that was
  spoken: both lower-cased, punctuation removed, then the word-level edit
  distance divided by the number of words in the original. Some technical
  text was also scored after a second, "normalised" pass that drops the
  spoken word "slash", joins "sha 256" and "p m", and turns "one" to "ten"
  into digits. That pass removes formatting differences, not mishearings.
- **Speech to test with.**
  - Runs 1 and 4: Kokoro read three passages and Whisper transcribed them
    back, so errors from both engines count: (a) 154 words of plain prose;
    (b) 128 words of technical text naming pyproject, sha256, Kokoro,
    DuckDB, the repository root and a file path; (c) a Markdown file with a
    date, a time, money, a percentage and a small table.
  - Run 2: the macOS voice Samantha (`say -o`) read three passages: (a) 65
    words of prose; (b) 59 words naming pyproject, sha256, Kokoro, DuckDB
    and the repository root; (c) passage (a) with fillers and a
    self-correction added. Long files repeat those passages with
    half-second gaps.
  - Run 3: the macOS voice read ten passages of 25 to 29 s each. The 10
    minute file (616.4 s, 1,943 words) and the 60 minute file (3,614.5 s,
    11,374 words) join them in a shuffled order with half-second gaps. A
    22.9 s take uses every vocabulary term in sentences, and a 111.3 s take
    is plain prose. Run 4 added a second 111.1 s prose take.
- **Silence and noise** were made with Python's `wave` module: 10 s of
  digital silence, 10 s of uniform white noise at about −45 dBFS (run 2),
  and 10 s of random noise with a level of 39.5 on a 16-bit scale (run 4).
- **Offline runs** used the macOS sandbox:
  `sandbox-exec -p '(version 1)(allow default)(deny network*)' …`. A
  control run of `curl` under the same profile failed to resolve a host.

## Results: Kokoro (text to speech)

| Measurement | Result |
|---|---|
| Install (run 1) | 24 s, exit 0; 17 packages installed from the hash-locked list, all 17 pinned |
| On disk | models 337 MB; environment 145 MB; plus a Python 3.12 that uv fetched (70 MB) and uv's own cache (144 MB) |
| Speed, new process each time, passages a / b / c (run 1) | 6.6 / 7.0 / 5.6 s for 50 / 55 / 44 s of audio: real-time factor 0.13 / 0.13 / 0.13 |
| Speed, model already loaded | real-time factor 0.11 to 0.13 |
| First audio, model loaded, earlier build (run 1) | 2.4 s (a), 2.8 s (b), 4.9 s (c); the first piece was about 390 characters |
| First audio, model loaded, release candidate (run 4) | 0.44 to 0.47 s (a), 0.63 s (b), 0.24 s (c) |
| First audio, model loaded, one 39-character sentence (run 1) | 0.32 s for 2.3 s of audio |
| Memory, resident worker | 1.5 GB (run 1); 1.45 GB (run 4) |
| Memory, one-shot command | 1.0 to 1.1 GB |

The earlier build made the first piece up to 400 characters, so a normal
paragraph missed the threshold "first audio at most 1.0 s". The release
candidate makes the first piece short. In run 4 the whole read was also a
little faster (for example 5.1 to 5.2 s against 5.7 s for passage a), the
audio was 0.4 to 1.4 percent longer, and Whisper's word error rate on the
read-back was the same as in run 1 for all three passages.

## Results: Whisper (speech to text)

| Measurement | large-v3-turbo-q5_0 (default) | large-v3-turbo-q8_0 |
|---|---|---|
| Install (run 1) | 185 s, exit 0; 9 packages from the hash-locked list | 627 s, exit 0, almost all download |
| Model file | 547 MB as the installer counts it | 834 MB as the installer counts it |
| Environment | 41 MB | shared with q5_0 |
| Time for a 45 to 55 s file, new process | 1.1 to 1.2 s | 1.2 to 1.3 s, and one run of 1.84 s |
| Memory, new process | about 0.87 GB | about 1.2 GB |
| Word error rate, plain prose (a) | 1.3 % | 1.3 % |
| Word error rate, technical text (b), raw | 6.8 % | 2.3 % |
| Word error rate, technical text (b), normalised | 0.75 % | 0.75 % |
| Word error rate, numbers and table (c), raw / normalised | 11.1 % / 0.0 % | not run |
| 10 minute file, `vocalize transcribe` (run 3) | 19.4 s; memory 1.03 GB; word error rate 1.5 % | not run |

In passage (b), q5_0's only true mishearing was "pyproject"; the other
errors came from how a file path and "sha256" were written back ("slash
users slash …", "sha 256"). In passage (c), every raw error was a number
written as a word or a time written as "3.45 p.m.".

Against the thresholds: plain prose (at most 3 %) passes. Technical text
(at most 6 %) fails raw with q5_0 and passes with q8_0; both pass once
formatting is normalised.

**The warm server** keeps Whisper loaded between takes (`[stt]
warm_minutes` above 0).

| Measurement | Result |
|---|---|
| A 22.9 s take, new process each time (run 3) | 0.76 to 0.77 s; memory 881 to 883 MB |
| The same take through a running warm server (run 3) | 0.45 to 0.48 s; the server held 863 MB; the server was ready 0.51 s after it started |
| A 9.6 s take, new process against warm server, with a home folder path too long for a socket (run 4) | 0.66 s against 0.33 to 0.37 s; the server used its fallback socket folder |

In runs 3 and 4, `vocalize listen --wav` never started a warm server by
itself, so the server was started with the same internal call a live
dictation makes. 1.0.0 changes this: `listen --wav` now starts one when
`warm_minutes` is above 0. That change was not run on real engines.

## Results: Parakeet (speech to text)

| Measurement | Result |
|---|---|
| Install (run 2) | 111 s, exit 0; 49 packages from the hash-locked list, all 49 pinned |
| On disk | model 2.3 GB; environment 450 MB; uv cache 447 MB; uv's Python 71 MB |
| Time for passages a / b / c (18 / 20 / 23 s), three runs each (run 2) | 0.62 to 0.67 / 0.62 to 0.65 / 0.67 to 0.71 s |
| Memory, short passages | 1.2 to 1.6 GB |
| Word error rate a / b / c | 1.5 % / 3.4 % / 1.4 % |
| 10 minute file, `vocalize transcribe` (run 3) | 7.4 s; memory 944 MB; word error rate 2.0 % |
| 60 minute file, `vocalize transcribe` (run 3) | 27.6 s; memory 1.5 GB; word error rate 1.6 % |
| 10 minute file, `vocalize transcribe`, network denied (run 3) | 5.0 s; transcript identical to the run with the network allowed |
| 10 s of silence, 5 runs (run 2) | no words on 5 of 5 |
| 10 s of white noise, 5 runs (run 2) | no words on 5 of 5 |

`vocalize transcribe` with Parakeet cuts a long file into overlapping
two-minute pieces. In run 3 the 10 minute file had 108 timestamped lines
and the 60 minute file 628, every line in time order. No word was repeated
or dropped at any join; every error at the 34 joins of the 60 minute file
was a number or a spelling variant. Whisper was more accurate on the 10
minute file (1.5 % against 2.0 %), partly because of the defect below;
Parakeet was 2.6 times faster.

Run 2 timed an older path, `vocalize listen --wav` on the whole file in
one call: 10.5 minutes took 8.2 s (word error rate 5.8 %) and 30.4
minutes took 54.8 s (6.3 %). On those files the same few words went wrong
on every repeat ("calm and" as "common", "root" as "route", "pyproject" as
"pie project").

Parakeet has no resident server: each call is a new process. Every run
found the model already in the file cache.

## Results: vocabulary (run 3, and run 4 for the hint)

The list used was six technical terms, among them pyproject, sha256,
DuckDB and a command with dashes.

| Measurement | Result |
|---|---|
| Technical 22.9 s take, Parakeet | word error rate 7.7 % without the list, 1.5 % with it |
| Technical 22.9 s take, Whisper | word error rate 7.7 % without the list, 1.5 % with it |
| The command with dashes | heard as different words ("run of, no project"), so the correction cannot fix it, by design |
| Plain 111.3 s prose, Parakeet | identical with and without the list |
| Plain 111.3 s prose, Whisper | 2.54 % without the list, 3.94 % with it |
| Plain 111.1 s prose, Whisper (run 4) | 0.84 % without the list, 0.28 % with it |

The correction step changed nothing in plain prose on either engine.
Whisper also gets the list as a hint before it starts, and that hint
changes what Whisper hears. Run 4 ran each prose take three times each way
and got byte-identical output every time, so the effect is real, not
noise. It goes either way: on one take Whisper dropped a whole clause with
the hint; on the other the hint made it write numbers as words, which
matched the original text.

## Results: cleanup model (Qwen3.5-4B, 4-bit)

| Measurement | Result |
|---|---|
| Install (run 2) | 312 s, exit 0, mostly download; 34 packages from the hash-locked list, all 34 pinned. A second install ran on the same Mac at the time |
| On disk | model 2.8 GB; environment 371 MB |
| One cleanup of passage (c), one-shot, two runs (run 2) | 1.93 s and 1.91 s; memory about 3.0 GB; fillers removed and "Tuesday, no, Wednesday" became "Wednesday" |
| `listen --wav --cleanup` on a 9.6 s take (run 4) | 1.89 to 4.21 s with cleanup, 0.44 to 0.64 s without; fillers gone and "Tuesday, no wait, I mean Wednesday" became "Wednesday", on both engines |
| Evaluation (`tests/eval_runner.py cleanup`), run 2 | passed: 25 of 26 everyday cases, 10 of 10 guarded cases (8 adversarial and 2 instruction-like); 50 s |
| Evaluation, run again through the runner (run 4) | passed: 25 of 26 everyday cases, 10 of 10 guarded cases; 48.55 s; it found the installed model with no extra settings |

The one failed everyday case was "He works in marketing, or rather, in
sales now": the faithful guard judged the cleaned text unfaithful and kept
the original. The threshold (at least 21 of 26 and every adversarial case)
passes, so the studio profile keeps cleanup on.

## Results: with the network denied

| Engine | Result under `deny network*` |
|---|---|
| Kokoro | worked: exit 0, 7.2 s of audio in 1.7 s |
| Whisper | worked, graphics processor included; transcript identical to the run with the network allowed |
| Parakeet | worked; transcript identical to the run with the network allowed; 0.75 s for a short take, 5.0 s for the 10 minute file |

The same profile also blocks Unix sockets, so the warm Whisper and cleanup
servers cannot run under it. A narrower profile that denies only IP
traffic allowed Unix sockets and still blocked an internet connection; DNS
lookups still resolved under it. Running workers inside a sandbox is not
part of 1.0.

## Results: removal actions, run for real (run 3)

| Action | Result |
|---|---|
| Remove the q8_0 Whisper model | exactly one file removed and the record of verified files rewritten; 31 other entries untouched; the q5_0 model and the Whisper environment kept |
| Clear the audio cache | 8 cached files (7 MB) removed; 24 other entries untouched |

## What failed, and whether it is fixed

| What was seen | Run | Status in 1.0.0 |
|---|---|---|
| `vocalize listen --wav` with a relative path failed with "The transcriber failed to run." | 1, 2 | fixed; run 4 passed relative, `./` and `../` paths |
| Whisper wrote "Thank you." for 10 s of silence, 10 of 10 runs, both models | 1 | fixed for digital silence; run 4: no words in 10 of 10 runs across both engines, and a file with 1 s of speech then silence still gave its words |
| `vocalize listen --wav FILE --cleanup` printed the raw text with no message | 2 | fixed; see the cleanup table |
| The warm Whisper server did not start when the socket path was over 100 bytes, and said nothing | 1 | fixed; run 4 used the fallback folder |
| First audio over 1 s for a normal paragraph | 1 | fixed; see the Kokoro table |
| `vocalize local status` judged speech to text by Whisper alone | 2 | fixed; run 4 checked each engine setting on three homes |
| `vocalize local install --help` said `--stt` installs whisper.cpp | 2 | fixed |
| The cleanup evaluation could not find the installed model with only `HOME` set | 2 | fixed; run 4 |
| Parakeet file transcription swapped two sentences at one chunk join of the 10 minute file (10 of its 39 word errors) | 3 | fixed after run 4: the worker now keeps the engine's order and only stops times from going backwards. Checked by a test with the times run 3 recorded; not run again on real audio |
| At another join of that file, one line's time was about 4 s early | 3 | not changed |
| `vocalize listen --wav` never started the warm server | 3, 4 | fixed; confirmed in the final check (0.65 s, then 0.37 s and 0.35 s with the server kept loaded; `vocalize local stop` unloaded it) |
| Low-level noise got past the silence check: Whisper wrote "." (2 of 2 runs); Parakeet said "Nothing heard." | 4 | partly fixed: a transcript with no letters or digits now counts as nothing heard. Still open: in the final check Whisper wrote a whole invented sentence for another 10 s noise file; Parakeet said "Nothing heard." |
| `listen --wav` refused a valid file named `.WAV` in capitals | 4 | fixed; confirmed in the final check on both engines |
| `vocalize status` said "transcribe cannot run" with Parakeet chosen and no Whisper, though it could | 4 | fixed after run 4; not run again on real engines |
| `vocalize transcribe` with `engine = auto` and only Parakeet failed | 4 | fixed after run 4: `auto` now uses Parakeet when no Whisper model is installed; not run again on real engines |
| `vocalize local status` said the Whisper runtime was installed when its environment was missing | 4 | fixed after run 4: the line now says the environment is not installed yet; not run again on real engines |

## What was not measured

- Anything through the microphone or the speakers. Only saved files were
  transcribed and nothing was played, so the time from the end of a live
  recording to text on the clipboard, and how the voice sounds, were not
  measured. Joins in speech were checked only through Whisper's
  transcripts.
- A true cold start: the first run after a restart, with the model not in
  the file cache. Every run found the model files already cached.
- Any Mac other than this one. Nothing was measured on a small Mac, so the
  small, standard and large machine profile values are untested.
- The warm cleanup server, and how long a warm server waits before it
  exits by itself.
- Graphics memory of the MLX engines (Parakeet, cleanup model): the
  figures above are resident memory only. With that limit, Parakeet's
  figure rose from 944 MB on the 10 minute file to 1.5 GB on the 60 minute
  file.
- Which onnxruntime provider Kokoro used. From the code and the CPU use,
  it ran on the CPU; this was not instrumented.
- Piper: not installed or measured.
- Whisper's evaluation stages (`whisper`, `warm`), passage (c) with q8_0,
  and Whisper on the 60 minute file.
- Removing the last Whisper model together with its environment.
- Low-level noise beyond one file, and quiet speech in a long file.
- Of the fixes made after run 4, the status wording was checked only by
  the test suite, not on real engines.

## Final check on the released code

A short run on the same Mac Studio, on the code that became 1.0.0, with
both speech-to-text engines installed in a scratch home folder:

| Check | Result |
|---|---|
| 10 s of digital silence, `listen --wav`, each engine | no words, a notice, exit code 1 |
| 10 s of faint random noise, `listen --wav` | Parakeet: "Nothing heard."; Whisper: one invented sentence (still open, see above) |
| An 8 s technical take with the vocabulary set, each engine | all four terms spelled as listed |
| The same take in a file named `.WAV` in capitals | accepted by both engines |
| `listen --wav` three times with `warm_minutes = 15` and Whisper | 0.65 s, 0.37 s, 0.35 s; the server held 842 MB |
| `vocalize local stop` | stopped the server; no worker process left |
| `vocalize transcribe` on the 10 minute file with Parakeet | 5.0 s, 835 MB, 108 timestamped lines in spoken order, times never going backwards |
| `vocalize transcribe` on a silent file | a note saying the recording is silent; a second run skipped it |
| Kokoro speaking three sentences to a file, then Parakeet reading it back | 2.2 s for 11 s of audio; the transcript matched the text apart from one capital letter |
