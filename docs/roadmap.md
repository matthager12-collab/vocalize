# Roadmap

Vocalize does two things on your Mac: text to speech and speech to text.
Version 1.0 is the first release that is local only.

## In 1.0

| Area | What ships |
|---|---|
| Text to speech | Kokoro (default), Piper (opt-in) and macOS `say` (fallback); Markdown and tables rewritten for listening; pause, resume and stop |
| Dictation | Parakeet or Whisper; vocabulary corrections after either engine; optional on-device cleanup; hold-to-talk; auto-paste |
| Transcription | `vocalize transcribe` for audio and text files, with Parakeet or Whisper |
| Settings | `vocalize portal`, a settings page on your Mac with a control for every setting, a button for every install, removal and integration, and machine profiles |
| Menu-bar app | Keyboard shortcuts for dictation, speaking the selection and stopping |

## Known gaps

- The silence check looks at the level of the whole recording, so faint
  noise with no speech in it can get past it, and Whisper can then write
  invented words. A better check needs recordings from a real microphone
  to tune against. Nothing was measured through a microphone.
- Piper's packages are pinned by version, not yet by hash like the other
  engines.
- The machine profiles for Macs under 64 GB have not been measured on such
  a Mac.
- A true cold start, with the model not yet in the file cache, has not
  been measured.

## Being evaluated

These are considered for larger Macs. Each is added only after a review of
the package and a passing measurement:

- Qwen3-ASR 1.7B, for more accurate speech to text
- Parakeet v3, for languages other than English
- A larger speech-out model

## Not planned

- Online voices or hosted models. Users who need them can stay on 0.15.
- Windows and Linux.
