"""`python tools/skyguard <command> ...` runs the skyguard CLI (see cli.py)."""
import os
import sys

# Running a directory puts the directory ITSELF first on sys.path, which would make its modules top-level
# names; the package is imported as `skyguard` from the folder above instead.
sys.path[0] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from skyguard.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
