"""Isolated stdlib bootstrap: validate existing runtime before importing a model."""

from __future__ import annotations

import importlib.metadata
import runpy
import sys
from pathlib import Path

_PACKAGES = {"llm": ("mlx-lm", "0.31.3"), "whisper": ("pywhispercpp", "1.5.1")}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in _PACKAGES:
        print("runtime_worker: unsupported worker", file=sys.stderr)
        return 2
    engine = args[0]
    package, version = _PACKAGES[engine]
    try:
        valid = (sys.version_info[:2] == (3, 12)
                 and importlib.metadata.version(package) == version)
    except importlib.metadata.PackageNotFoundError:
        valid = False
    if not valid:
        print(f"runtime_worker: requires Python 3.12 and {package}=={version}; no provisioning attempted",
              file=sys.stderr)
        return 2
    worker = Path(__file__).with_name(f"{engine}_worker.py")
    sys.argv = [str(worker), *args[1:]]
    runpy.run_path(str(worker), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
