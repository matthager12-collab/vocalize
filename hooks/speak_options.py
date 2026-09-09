#!/usr/bin/env python3
"""Thin wrapper: the picker helper now lives in vocalize/speak_options.py (T-81)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vocalize.speak_options import main

if __name__ == "__main__":
    sys.exit(main())
