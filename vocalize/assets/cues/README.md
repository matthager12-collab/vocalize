# Dictation cue words

Spoken alternatives to the Tink/Pop/Glass system sounds (`[stt] cues`).

Generated with the local Kokoro voice `af_heart`, one word per file:

    vocalize speak "Start." --provider kokoro --voice af_heart --no-play -o vocalize/assets/cues/start.wav
    vocalize speak "Stopped." --provider kokoro --voice af_heart --no-play -o vocalize/assets/cues/stopped.wav
    vocalize speak "Ready." --provider kokoro --voice af_heart --no-play -o vocalize/assets/cues/ready.wav

Kokoro-82M is Apache-2.0 licensed.
