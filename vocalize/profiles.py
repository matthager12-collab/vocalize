"""Machine profiles: named presets of ordinary settings, sized by memory.

A profile is nothing more than a set of registered setting values plus the
installs it recommends. Applying one goes through
`settings_registry.apply_changes` like any other save, so a profile can
never write a key the registry does not know or a value it would refuse.

Every profile sets the same keys, so applying a smaller profile after a
larger one puts each value back. `small` holds the package defaults, which
keeps a first install safe on any Mac.

The studio values follow the engines as they were measured on a Mac Studio
(Apple M5 Max, 128 GB). The smaller profiles were not measured on a small
Mac; they are sized from those measurements by memory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from types import MappingProxyType

#: The install actions a profile may recommend.
INSTALLS = ("install_kokoro", "install_parakeet", "install_whisper", "install_cleanup")


@dataclass(frozen=True)
class Profile:
    name: str
    label: str
    help: str
    min_memory_gb: float
    changes: MappingProxyType
    installs: tuple
    #: Install action id -> its parameters, where the profile recommends a
    #: particular model rather than the install's default. Read-only.
    install_params: MappingProxyType = MappingProxyType({})


def _profile(name, label, help, min_memory_gb, changes, installs,
             install_params=None) -> Profile:
    params = {action: MappingProxyType(dict(values))
              for action, values in (install_params or {}).items()}
    return Profile(name, label, help, min_memory_gb, MappingProxyType(dict(changes)),
                   tuple(installs), MappingProxyType(params))


#: Said by every profile that dictates with Parakeet.
_PARAKEET = ("Dictates and transcribes files with Parakeet. The Whisper model and beam "
             "values apply only if you switch the engine to Whisper.")

PROFILES: tuple[Profile, ...] = (
    _profile("small", "Small Mac (under 16 GB)",
             "For a Mac with less than 16 GB of memory. Uses the package defaults and "
             "installs Whisper, so dictation and file transcription use Whisper (dictation "
             "moves to Parakeet only if you install it on Apple silicon). Keeps nothing "
             "loaded between takes and cleanup is off. Not measured on a small Mac.",
             0, {
                 "stt.engine": "auto",
                 "stt.model": "large-v3-turbo-q5_0",
                 "stt.beam_size": 5,
                 "stt.warm_minutes": 0,
                 "stt.cleanup": "off",
             }, ("install_kokoro", "install_whisper")),
    _profile("standard", "Standard Mac (16 to 31 GB)",
             f"For a Mac with 16 to 31 GB of memory. {_PARAKEET} Keeps nothing loaded "
             "between takes: Parakeet is never kept loaded and cleanup is off. Not measured "
             "on a Mac of this size.",
             16, {
                 "stt.engine": "parakeet",
                 "stt.model": "large-v3-turbo-q5_0",
                 "stt.beam_size": 5,
                 "stt.warm_minutes": 0,
                 "stt.cleanup": "off",
             }, ("install_kokoro", "install_parakeet", "install_whisper")),
    _profile("large", "Large Mac (32 to 63 GB)",
             f"For a Mac with 32 to 63 GB of memory. {_PARAKEET} The Whisper model is the "
             "8-bit one. Keeps nothing loaded between takes: Parakeet is never kept loaded "
             "and cleanup is off. Not measured on a Mac of this size.",
             32, {
                 "stt.engine": "parakeet",
                 "stt.model": "large-v3-turbo-q8_0",
                 "stt.beam_size": 5,
                 "stt.warm_minutes": 0,
                 "stt.cleanup": "off",
             }, ("install_kokoro", "install_parakeet", "install_whisper")),
    _profile("studio", "Studio Mac (64 GB and up)",
             f"For a Mac with 64 GB of memory or more, such as a Mac Studio. {_PARAKEET} "
             "The Whisper model is the 8-bit one, installed by this profile. Tidies "
             "transcripts with the local cleanup model and keeps the cleanup model loaded "
             "for 4 hours; Parakeet is never kept loaded. Measured on a Mac Studio (Apple "
             "M5 Max, 128 GB).",
             64, {
                 "stt.engine": "parakeet",
                 "stt.model": "large-v3-turbo-q8_0",
                 "stt.beam_size": 5,
                 "stt.warm_minutes": 240,
                 "stt.cleanup": "local",
             }, ("install_kokoro", "install_parakeet", "install_whisper", "install_cleanup"),
             {"install_whisper": {"model": "large-v3-turbo-q8_0"}}),
)

_BY_NAME = {profile.name: profile for profile in PROFILES}


def names() -> tuple[str, ...]:
    """The profile names, smallest first."""
    return tuple(profile.name for profile in PROFILES)


def get(name: str) -> Profile:
    """The profile called `name`. KeyError when there is none."""
    return _BY_NAME[name]


def memory_gb() -> float:
    """This Mac's physical memory in GB (1024**3 bytes), or 0.0 when unreadable."""
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return 0.0
    if total <= 0:
        return 0.0
    return total / 1024 ** 3


def detect() -> str:
    """The largest profile this Mac's memory meets; `small` when memory is unknown."""
    memory = memory_gb()
    best = PROFILES[0].name
    if memory <= 0:
        return best
    for profile in PROFILES:
        if memory >= profile.min_memory_gb:
            best = profile.name
    return best


def changes(name: str) -> dict:
    """The profile's settings as a plain dict of setting key to value."""
    return dict(get(name).changes)


def installs(name: str) -> tuple[str, ...]:
    """The install action ids the profile recommends."""
    return get(name).installs


def install_params(name: str) -> dict:
    """Install action id -> its parameters, as plain dicts; {} for defaults."""
    return {action: dict(values) for action, values in get(name).install_params.items()}
