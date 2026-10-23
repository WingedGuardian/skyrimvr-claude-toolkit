#!/usr/bin/env python3
"""Generated from agent/targets/opencode/hooks/skyguard_adapter.py; V1 transport entry."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "tools"))
from skyguard.adapters.opencode import main

if __name__ == "__main__":
    raise SystemExit(main(root))
