# Spike notes: app roadmap plan

Measurements taken on the reference Mac (Mac mini, Apple M4, 16 GB, macOS 26.5.1) during the runs. Each section is the record its phase's exit gate greps; keep the line shapes.

## Beam

Run 2, T-10, 2026-09-07. `vocalize listen --wav` on a 43 s clip rendered with `say` from a jargon paragraph (the 2026-09-01 spike clips were not kept), model `small.en`, one-shot worker per run, wall clock from `/usr/bin/time -l`. Run 1 is cold; warm is the median of runs 2 and 3.
cold beam 1: 2.01 s
warm beam 1: 1.75 s
cold beam 5: 2.10 s
warm beam 5: 2.08 s
Peak RSS 800 MB at beam 1, 928 MB at beam 5. Transcripts identical (731 characters, all six "to get" phrases correct on the synthetic voice).

Manual check 1, the owner's voice, 2026-09-07: one 15 s take recorded with the recorder bundle and transcribed twice from the same WAV. Beam 1: "I want to get this past themerge to get the fix in." Beam 5: the same words, "themerge" included. **Beam search did not fix the merge on the owner's voice; issue #4 stays open.** The merge is a missing space between two tokens, which the decoder choice does not touch.

Same take, `large-v3-turbo-q5_0` (installed for this check, 2026-09-07): beam 5 "I want to get this past the merge to get the fix in." and beam 1 the same words — **the larger model writes "the merge" correctly at both beam settings.** Wall 1.52 s (beam 5) and 1.39 s (beam 1) for the 15 s take, peak RSS 886 MB and 811 MB, against 2.0 s and 800–928 MB for `small.en` on the 43 s clip: no slower in practice on this Mac. So the answer to #4 on this voice is the model tier, not the decoder. Two observations for later runs: turbo's segments carry no leading space, so the worker's plain `"".join` glued sentences together ("working.I want") — a one-line join fix with a test; and beam 5 dropped the cut-off last sentence that beam 1 kept, which is the 15 s max-seconds truncation, not a decoding fault. Beam 5 costs about 18 % more time, far under the 2× that would have moved `_TRANSCRIBE_TIMEOUT` (300 s, unchanged).

One implementation finding: pywhispercpp 1.5.1 indexes both `beam_size` and `patience` in the `beam_search` dict; passing only `beam_size` fails with `KeyError: 'patience'` before the model loads. The worker passes `patience = -1.0`, whisper.cpp's "no patience factor" default.

## turbo q8_0

Run 2, T-11, 2026-09-07. `ggml-large-v3-turbo-q8_0.bin` at the manifest's pinned revision `5359861c739e955e79d9a303bcbc70fb988958b1`, downloaded to completion (HTTP 200, `content-length` matched), then hashed from the file on disk.
size: 874188075
sha256: 317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1
The download was deleted after hashing; nothing was installed under `~/.cache/vocalize`.

## CoreML
cold cpu: 3.55 s
warm cpu: 2.88 s
warm cpu: 2.70 s
cold coreml: 5.80 s
warm coreml: 4.93 s
warm coreml: 5.34 s
rss cpu: 760 MB
rss coreml: 1.38 GB
verdict: loses — CoreML is about 75 % slower and uses 600 MB more; no knob is added, and the Kokoro RAM figure in the docs is corrected to 760 MB.

Run 2, T-12, 2026-09-07. `vocalize speak --provider kokoro --no-play --output <tmp>` with a unique sentence per run (no cache hits), four runs per path, `/usr/bin/time -l` on the CLI process (peak RSS is the largest process in the tree, the worker). CPU is the default; CoreML is `ONNX_PROVIDER=CoreMLExecutionProvider`, which the worker inherits from the environment. Run 4 of each path (not counted above): CPU 2.94 s, CoreML 5.09 s.

## Hotkeys

Run 7, T-60, 2026-09-07. A throwaway Swift probe (`NSApplication` in accessory mode, Carbon `RegisterEventHotKey` for control-option-command-D, one log line per `kEventHotKeyPressed` and `kEventHotKeyReleased` with the frontmost app's name). Register status 0 with Hammerspoon running on ctrl-alt-cmd-S and -X.

Claude desktop, normal window: down and up, held 2 s.
Ghostty (self-drawn terminal): down and up, held 3 s.
Claude desktop, full-screen: down and up, held 3 s.
Decision: Carbon (DEC-033 A). No Accessibility grant needed for dictation.

## Cue

Run 11, T-100, 2026-09-08. The real recorder launched through `dictate._launch_recorder` into a scratch workdir, `take.wav` polled every 20 ms from the moment `rec.pid` appeared, three launches per input, owner present.
branch: trim — the take grows incrementally on both inputs, so the first growth past the 4096-byte header is the open-microphone signal; the cue plays after it and is trimmed from the head.
Yeti Stereo Microphone (USB): pid→first growth 383, 359, 383 ms; launch→pid 152, 149, 153 ms; growth in 10240-byte steps (about 320 ms of 16 kHz audio per write).
Mat's AirPods Pro (Bluetooth): pid→first growth 500, 521 ms (a third run measured 50 ms, which was the 4096-byte header landing on a file that did not exist yet, not audio — the baseline must be the header, see below); launch→pid 147, 154, 146 ms; growth in about 5460-byte steps (about 170 ms per write).
Baseline rule for `_wait_for_audio`: the file may not exist at pid time, and its first write is a 4096-byte header with no audio in it; t0 is the first moment the size exceeds max(first observed size, 4096). `_AUDIO_GRACE` 5 s is ten times the worst case seen.
Write granularity bounds the cue's lateness at one write (320 ms USB, 170 ms Bluetooth); late is the safe side, because the user speaks after the cue and the trim covers t0 to the end of the cue.
