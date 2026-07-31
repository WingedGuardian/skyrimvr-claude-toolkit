# Core devcontainer for the parts of this toolkit that don't need Windows or MO2's virtual
# filesystem: ESP inspection/diffing (Spriggit, esplugin-style tooling), FOMOD/JSON generation,
# unit-testing mod logic. See docs/container-vs-windows.md for what belongs here vs. on Windows.
#
# Debian bookworm (12), not bullseye (11) -- bullseye's LTS window ends 2026-08-31. Bookworm's
# extended support runs to 2028-06-30, and it lets openjdk-17-jdk-headless (needed for ReSaver
# CLI) install as a plain apt package instead of a manual JDK fetch.
FROM mcr.microsoft.com/devcontainers/python:3.0.6-3.11-bookworm

# Use bash for all RUN commands.
SHELL ["/bin/bash", "-c"]

# --- OpenJDK 17 (ReSaver CLI's floor is JDK 17+) ---
RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

# --- Node.js 20 LTS ---
# Installed directly from NodeSource rather than the devcontainer feature so the image is fully
# self-contained and devshell-docker.sh has the same behavior without going through the CLI.
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# --- .NET 9 (Spriggit / AutoMod CLI) ---
# Microsoft's dotnet-install.sh is the cross-distro canonical install method.
RUN curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
    && chmod +x /tmp/dotnet-install.sh \
    && /tmp/dotnet-install.sh --channel 9.0 --install-dir /usr/local/dotnet \
    && rm /tmp/dotnet-install.sh

ENV DOTNET_ROOT=/usr/local/dotnet
ENV PATH="$PATH:/usr/local/dotnet"
