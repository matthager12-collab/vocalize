"""vocalize: a text-to-speech CLI built on the ElevenLabs API.

Converts plain text, markdown, or piped stdin into natural-sounding
speech, with a preprocessing pass that flattens markdown tables and
formatting into something that actually sounds good spoken aloud
(most TTS tools just read a table's raw cell text left to right,
which is close to useless).
"""

__version__ = "0.10.1"
