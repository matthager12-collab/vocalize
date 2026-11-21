"""Local provider identifiers.

Hosted inference credential storage, lookup, validation and deletion were removed.
Existing system-keychain entries are deliberately left untouched.
"""

PROVIDER_NAMES = ("kokoro", "piper", "say")
PROVIDER_LABELS = {"kokoro": "Kokoro", "piper": "Piper", "say": "macOS say"}
DEFAULT_PROVIDER = "kokoro"
