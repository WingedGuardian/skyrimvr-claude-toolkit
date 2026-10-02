#!/usr/bin/env bash
# Container-only provisioning. The source workspace stays read-only.
set -euo pipefail
if [ -f requirements.txt ]; then
    test -f requirements.lock || { echo 'Missing requirements.lock' >&2; exit 1; }
    pip install --only-binary=:all: --require-hashes -r requirements.lock
fi
if [ -f package.json ]; then
    test -f package-lock.json || { echo 'Missing package-lock.json' >&2; exit 1; }
    npm ci --ignore-scripts --no-audit --no-fund
fi
if [ -f .config/dotnet-tools.json ]; then
    dotnet tool restore
fi
