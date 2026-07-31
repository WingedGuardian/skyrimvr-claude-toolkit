#!/usr/bin/env bash
# Start the dev container via Docker directly and open an interactive shell.
# Usage: ./devshell-docker.sh [--rebuild]
#
# Prerequisites:
#   - Docker Desktop running (no other dependencies)
#
# Pass --rebuild to force a fresh image build even if one already exists.
#
# Reads the mount sources straight out of .devcontainer/devcontainer.json (which setup.sh fills
# in with your real mod paths), so this stays in sync with that file rather than hardcoding paths.
set -e

# Under Git Bash on Windows, any bare "/..."-looking argument gets silently rewritten to a Windows
# path (prepending the Git install root) before reaching docker.exe/jq -- e.g. "/skyrim/mods" would
# become "C:/Program Files/Git/skyrim/mods". This script passes several such container-side paths
# (as docker run targets, env values, and a jq --arg), so path conversion must stay off for all of
# it. Real Windows paths (the mount SOURCES, which start with a drive letter) are unaffected either
# way, so this is safe to set unconditionally; it's a no-op on Linux/macOS.
export MSYS_NO_PATHCONV=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -W 2>/dev/null || pwd)"
IMAGE_TAG="skyrim-toolkit"
CONTAINER_NAME="skyrim-toolkit-dev"
DC_JSON="$SCRIPT_DIR/.devcontainer/devcontainer.json"

command -v jq >/dev/null 2>&1 || { echo "ERROR: jq is required (the toolkit already depends on it for the safety hooks)."; exit 1; }
[ -f "$DC_JSON" ] || { echo "ERROR: $DC_JSON not found."; exit 1; }

mount_source() { # $1 = target path inside the container
    # devcontainer.json is JSONC (VS Code and the devcontainer CLI both allow // comments), but
    # plain jq doesn't -- strip whole-line comments first so this doesn't choke on them.
    grep -v '^[[:space:]]*//' "$DC_JSON" | jq -r --arg t "$1" '.mounts[] | select(.target == $t) | .source'
}
MODS_SRC="$(mount_source /skyrim/mods)"
PROFILE_SRC="$(mount_source /skyrim/profile)"
OVERWRITE_SRC="$(mount_source /skyrim/overwrite)"

for v in MODS_SRC PROFILE_SRC OVERWRITE_SRC; do
    if [ -z "${!v}" ] || [[ "${!v}" == *"{{"* ]]; then
        echo "ERROR: .devcontainer/devcontainer.json still has an unfilled mount placeholder."
        echo "  Run 'bash setup.sh' first so it can resolve your mod paths."
        exit 1
    fi
done

# --- Build image ---
if [[ "$1" == "--rebuild" ]] || ! docker image inspect "$IMAGE_TAG" &>/dev/null; then
    echo "Building $IMAGE_TAG from .devcontainer/Dockerfile..."
    docker build -f "$SCRIPT_DIR/.devcontainer/Dockerfile" -t "$IMAGE_TAG" "$SCRIPT_DIR"
fi

# --- Clean up any leftover container from a previous session ---
docker rm -f "$CONTAINER_NAME" &>/dev/null || true
trap 'echo "Stopping container..."; docker rm -f "$CONTAINER_NAME" &>/dev/null || true' EXIT

# --- Start container detached (sleep infinity keeps it alive so we can exec into it) ---
echo "Starting container..."
docker run -d \
    --name "$CONTAINER_NAME" \
    -v "$SCRIPT_DIR:/workspace" \
    -w /workspace \
    -v "$MODS_SRC:/skyrim/mods:ro" \
    -v "$PROFILE_SRC:/skyrim/profile:ro" \
    -v "$OVERWRITE_SRC:/skyrim/overwrite:ro" \
    -e SKYRIM_MODS_DIR=/skyrim/mods \
    -e SKYRIM_PROFILE_DIR=/skyrim/profile \
    -e SKYRIM_OVERWRITE_DIR=/skyrim/overwrite \
    "$IMAGE_TAG" \
    sleep infinity

# --- postCreateCommand (only what's present -- a fresh checkout may not have all three yet) ---
echo "Installing dependencies inside the container..."
docker exec "$CONTAINER_NAME" bash -c '
    [ -f requirements.txt ] && pip install -r requirements.txt
    [ -f package.json ] && npm install
    [ -f .config/dotnet-tools.json ] && dotnet tool restore
    true
'

echo "Done. Opening shell (exit to stop the container)..."
docker exec -it "$CONTAINER_NAME" bash
