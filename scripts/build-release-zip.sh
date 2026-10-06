#!/usr/bin/env bash
# Build the release zip from COMMITTED git objects (Plan 8 T10.1). The one build both the release workflow
# and a local pre-release build use; the work is in build_release_zip.py beside this file (see its header).
#
#   bash scripts/build-release-zip.sh <rev> <outdir> [--version V] [--allow-dirty]
#
# Exit 0 built (prints ZIP=, COMMIT=, VERSION=) / 2 usage / 3 dirty tree / 4 bad or foreign rev.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for cand in python3 python; do
    # The Windows Store alias `python3` exists and prints an advert instead of running: ask for a version.
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; then
      PY="$cand"; break
    fi
  done
fi
[ -n "$PY" ] || { echo "build-release-zip: no Python 3 found (set PYTHON)" >&2; exit 2; }
exec "$PY" -B "$HERE/build_release_zip.py" "$@"
