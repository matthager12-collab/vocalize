# Roadmap

Vocalize does two things on your Mac: text to speech and speech to text.
Version 1.0 is the first release that is local only.

## In 1.0

| Area | What ships |
|---|---|
| Text to speech | Kokoro (default), Piper (opt-in) and macOS `say` (fallback); Markdown and tables rewritten for listening; pause, resume and stop |
| Dictation | Parakeet or Whisper; optional on-device cleanup; hold-to-talk; auto-paste |
| Transcription | `vocalize transcribe` for audio and text files |
| Settings | `vocalize portal`, a settings page on your Mac |
| Menu-bar app | Keyboard shortcuts for dictation, speaking the selection and stopping |

## Being evaluated

These are considered for larger Macs. Each is added only after a review of
the package and a passing measurement:

- Qwen3-ASR 1.7B, for more accurate speech to text
- Parakeet v3, for languages other than English
- A larger speech-out model

## Not planned

- Online voices or hosted models. Users who need them can stay on 0.15.
- Windows and Linux.
