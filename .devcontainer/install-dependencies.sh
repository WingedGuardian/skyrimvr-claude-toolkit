#!/usr/bin/env bash
# Container-only provisioning. The source workspace stays read-only.
set -euo pipefail
if [ -f requirements.txt ]; then
    test -f requirements.lock || { echo 'Missing requirements.lock' >&2; exit 1; }
    pip install --only-binary=:all: --require-hashes -r requirements.lock
fi
if [ -f package.json ]; then
    test -f package-lock.json || { echo 'Missing package-lock.json' >&2; exit 1; }
    # node_modules is gitignored and the workspace is a read-only bind, so a fresh checkout
    # has nowhere to put it. Install into a container-local prefix instead; the Dockerfile's
    # NODE_PATH points require() there. `npm ci` still verifies every lockfile integrity hash.
    NODE_PREFIX="${SKYRIM_NODE_PREFIX:-/opt/skyrim-node}"
    case "$NODE_PREFIX" in
        /workspace|/workspace/*) echo "SKYRIM_NODE_PREFIX must be outside the read-only workspace: $NODE_PREFIX" >&2; exit 1;;
    esac
    mkdir -p "$NODE_PREFIX"
    cp package.json package-lock.json "$NODE_PREFIX/"
    (cd "$NODE_PREFIX" && npm ci --ignore-scripts --no-audit --no-fund)
fi
if [ -f .config/dotnet-tools.json ]; then
    dotnet tool restore
fi
