#!/usr/bin/env python3
"""Thin wrapper: the Quick Action installer now lives in vocalize/integrate.py (T-81)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vocalize.integrate import main

if __name__ == "__main__":
    sys.exit(main())
