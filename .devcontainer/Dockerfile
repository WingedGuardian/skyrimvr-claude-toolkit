# Core devcontainer for the parts of this toolkit that don't need Windows or MO2's virtual
# filesystem: ESP inspection/diffing (Spriggit, esplugin-style tooling), FOMOD/JSON generation,
# unit-testing mod logic. See docs/container-vs-windows.md for what belongs here vs. on Windows.
#
# EXACT AND VERIFIED (Plan 8 reproducibility ruling: every user gets exactly the versions we tested).
# The base image is pinned by digest, and every runtime below is one exact version downloaded as an
# archive whose SHA-256/SHA-512 is checked BEFORE it is unpacked -- no channel, no "latest", no
# download piped into a shell, no distro package whose version moves under us.
# scripts/thirdparty-audit.py checks each hash against what upstream publishes, and holds the Node
# and .NET versions here equal to .node-version and tools/toolchain-check.sh.
#
# Debian bookworm (12), not bullseye (11) -- bullseye's LTS window ends 2026-08-31. Bookworm's
# extended support runs to 2028-06-30. amd64 and arm64 are both covered (the base image is multi-arch).
FROM mcr.microsoft.com/devcontainers/python:3.0.6-3.11-bookworm@sha256:aea59f6834231486ee510ed90fc89659762e8ac246bb5e5fc49809ab736a0b94

# Use bash for all RUN commands; a failed command anywhere in a pipeline fails the step.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# --- Microsoft Build of OpenJDK 21 (ReSaver CLI; the JDK the Windows docs recommend) ---
ARG JDK_VERSION=21.0.12
ARG JDK_URL_AMD64=https://download.visualstudio.microsoft.com/download/pr/52968986-1ae3-44a2-b803-76184bec2b89/eeb3a706dc688dec3e1fa6f5037999b0/microsoft-jdk-21.0.12-linux-x64.tar.gz
ARG JDK_SHA256_AMD64=f2a84ad31ebeaf3a26252dd86a4a8e1b74aefb6bfc8e55fd20190110d1353c0f
ARG JDK_URL_ARM64=https://download.visualstudio.microsoft.com/download/pr/c7d3e465-726d-4ec3-9e1f-718ae2804011/70654abc38109c921059c79b082034d2/microsoft-jdk-21.0.12-linux-aarch64.tar.gz
ARG JDK_SHA256_ARM64=c61cadbc8ad4f950131dc260f0cdfd8d4d1f200fb16f8ac6a2611f17a77ab301
RUN case "$(dpkg --print-architecture)" in \
        amd64) url="$JDK_URL_AMD64"; sum="$JDK_SHA256_AMD64";; \
        arm64) url="$JDK_URL_ARM64"; sum="$JDK_SHA256_ARM64";; \
        *) echo "unsupported architecture $(dpkg --print-architecture)" >&2; exit 1;; \
    esac \
    && curl -fsSL -o /tmp/jdk.tar.gz "$url" \
    && echo "$sum  /tmp/jdk.tar.gz" | sha256sum -c - \
    && mkdir -p /usr/local/jdk \
    && tar -xzf /tmp/jdk.tar.gz -C /usr/local/jdk --strip-components=1 --no-same-owner \
    && rm /tmp/jdk.tar.gz \
    && /usr/local/jdk/bin/java -version 2>&1 | grep -q "\"$JDK_VERSION\""
ENV JAVA_HOME=/usr/local/jdk
ENV PATH="/usr/local/jdk/bin:$PATH"

# --- Node.js 24 LTS: exactly .node-version, from nodejs.org with its SHASUMS256 hash ---
ARG NODE_VERSION=24.19.0
ARG NODE_SHA256_AMD64=f625d97cd707df4ff96254916fbc5ff014f09c09effe5a1e0ca8f6d41a8789d4
ARG NODE_SHA256_ARM64=d28c8a5bf0a808f0ed434a1dce8c54ae98f0371c0bd86ac58abc613f73e6643f
RUN case "$(dpkg --print-architecture)" in \
        amd64) arch=x64; sum="$NODE_SHA256_AMD64";; \
        arm64) arch=arm64; sum="$NODE_SHA256_ARM64";; \
        *) echo "unsupported architecture $(dpkg --print-architecture)" >&2; exit 1;; \
    esac \
    && curl -fsSL -o /tmp/node.tar.gz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${arch}.tar.gz" \
    && echo "$sum  /tmp/node.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/node.tar.gz -C /usr/local --strip-components=1 --no-same-owner \
    && rm /tmp/node.tar.gz \
    && test "$(node --version)" = "v${NODE_VERSION}"

# --- .NET 10 SDK (Spriggit 0.41 / AutoMod CLI) -- the toolkit's only .NET requirement ---
# The exact SDK from Microsoft's release CDN, checked against the SHA-512 its release metadata publishes
# (builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json). No dotnet-install.sh channel.
ARG DOTNET_SDK_VERSION=10.0.401
ARG DOTNET_SHA512_AMD64=51c8b999af9e8dd9998c9edc5944e19a90788862068acd38694e098889054ce8c23d4f0c5cccfa16bf187d044562359e5ee69a9f8ad0bbe913ba90311fbce25b
ARG DOTNET_SHA512_ARM64=58ace73ced6b4360754689a686bdfb8a317f4da6cb8bb416dbc7d0ba9f47e43e3c09f5eb1f1a1cfaacbd10df9558da4882bf2a5e195d6ab56a02c1f9f76102ed
RUN case "$(dpkg --print-architecture)" in \
        amd64) rid=linux-x64; sum="$DOTNET_SHA512_AMD64";; \
        arm64) rid=linux-arm64; sum="$DOTNET_SHA512_ARM64";; \
        *) echo "unsupported architecture $(dpkg --print-architecture)" >&2; exit 1;; \
    esac \
    && curl -fsSL -o /tmp/dotnet.tar.gz "https://builds.dotnet.microsoft.com/dotnet/Sdk/${DOTNET_SDK_VERSION}/dotnet-sdk-${DOTNET_SDK_VERSION}-${rid}.tar.gz" \
    && echo "$sum  /tmp/dotnet.tar.gz" | sha512sum -c - \
    && mkdir -p /usr/local/dotnet \
    && tar -xzf /tmp/dotnet.tar.gz -C /usr/local/dotnet --no-same-owner \
    && rm /tmp/dotnet.tar.gz \
    && /usr/local/dotnet/dotnet --list-sdks | grep -q "^${DOTNET_SDK_VERSION} "

ENV DOTNET_ROOT=/usr/local/dotnet
ENV PATH="$PATH:/usr/local/dotnet"

# Project Node modules live OUTSIDE the read-only /workspace bind (install-dependencies.sh runs
# `npm ci` in this prefix). A volume nested under /workspace cannot be mounted on a fresh checkout:
# Docker cannot create its mountpoint through the read-only bind. CommonJS require() finds them
# via NODE_PATH; ES-module `import` ignores NODE_PATH, so use require() or createRequire().
ENV NODE_PATH=/opt/skyrim-node/node_modules
