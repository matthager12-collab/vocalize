"""vocalize: a local-first text-to-speech and dictation CLI for macOS.

Converts plain text, markdown, or piped stdin into natural-sounding
speech, with a preprocessing pass that flattens markdown tables and
formatting into something that actually sounds good spoken aloud
(most TTS tools just read a table's raw cell text left to right,
which is close to useless).
"""

__version__ = "0.12.0"
