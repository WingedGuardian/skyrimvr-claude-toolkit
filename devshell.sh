#!/usr/bin/env bash
# Start the dev container (builds the image if needed) and open an interactive shell.
# Usage: ./devshell.sh
#
# Prerequisites:
#   - Docker Desktop running
#   - the devcontainer CLI installed globally, pinned: npm install -g @devcontainers/cli@0.89.0
#
# If you don't have the devcontainer CLI, use ./devshell-docker.sh instead -- it drives
# Docker directly with no extra dependencies.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

devcontainer up --workspace-folder "$SCRIPT_DIR"
devcontainer exec --workspace-folder "$SCRIPT_DIR" bash
