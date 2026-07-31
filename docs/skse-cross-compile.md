# Advanced: Cross-Compiling SKSE Plugins from Linux

**This is not part of the default devcontainer, and it's unvalidated beyond one experiment.**
Credit: this recipe comes from [@aaronputty](https://github.com/aaronputty)'s fork,
[putty-skyrim-claude-toolkit](https://github.com/aaronputty/putty-skyrim-claude-toolkit), where they
used it (with Claude's help) to cross-compile a pre-pivot version of
[Mora](https://github.com/halgari/mora), an SKSE plugin, entirely from a Linux container. Their own
assessment: "I have no idea if this works with any other SKSE plugin source." Treat this as a
starting point, not a proven pipeline.

It's kept out of the default `.devcontainer/Dockerfile` because it roughly doubles the image (LLVM
17 + the ~700MB Windows SDK/CRT splat) for a capability most users of this toolkit won't touch —
that cost shouldn't land on everyone's container build for one unconfirmed use case. If you want it,
add the following to a copy of the Dockerfile (or a second `Dockerfile.skse-crosscompile`) and adjust
your `devcontainer.json`/build tooling accordingly.

## What it adds

- **LLVM 17** (`clang`, `clang-cl`, `lld-link`) — the base image's default apt only ships an older
  LLVM, so this pulls the official LLVM apt repo.
- **[xwin](https://github.com/Jake-Shadle/xwin)** — downloads and "splats" the Windows SDK and MSVC
  CRT headers/libs so `clang-cl` can target `x86_64-pc-windows-msvc` from Linux. Accepting Microsoft's
  EULA (`XWIN_ACCEPT_LICENSE=true`) is required and is for build-time use only.
- **[xmake](https://xmake.io/)** — the build system used to drive the cross-compile; its
  `add_requires()` package detection wants `cmake`, `pkg-config`, and a few dev libraries
  (`libfmt-dev`, `zlib1g-dev`, `nlohmann-json3-dev`) present so it doesn't try to compile them from
  source at build time.

## Dockerfile additions (verbatim from the source fork)

```dockerfile
# Use bash for all RUN commands -- xmake's install script sources ~/.xmake/profile, which /bin/sh
# (Docker's default) can't do.
SHELL ["/bin/bash", "-c"]

# C++ library dependencies xmake's add_requires() detects via pkg-config/CMake config files.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        cmake pkg-config libfmt-dev zlib1g-dev nlohmann-json3-dev \
    && rm -rf /var/lib/apt/lists/*

# LLVM 17 (clang-cl + lld-link)
RUN curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/llvm.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/llvm.gpg] https://apt.llvm.org/bookworm/ llvm-toolchain-bookworm-17 main" \
        > /etc/apt/sources.list.d/llvm.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        clang-17 lld-17 llvm-17 clang-tools-17 libc++-17-dev \
    && ln -sf /usr/bin/clang-17    /usr/local/bin/clang \
    && ln -sf /usr/bin/clang-cl-17 /usr/local/bin/clang-cl \
    && ln -sf /usr/bin/lld-17      /usr/local/bin/lld \
    && ln -sf /usr/bin/lld-link-17 /usr/local/bin/lld-link \
    && ln -sf /usr/bin/llvm-lib-17 /usr/local/bin/llvm-lib \
    && ln -sf /usr/bin/llvm-ar-17  /usr/local/bin/llvm-ar \
    && rm -rf /var/lib/apt/lists/*

# xmake (root-safe: XMAKE_ROOT=y avoids needing --root on every invocation)
ENV XMAKE_ROOT=y
RUN curl -fsSL https://xmake.io/shget.text | bash

# xwin (pre-built musl binary -- no Rust toolchain needed)
ARG XWIN_VERSION=0.6.5
RUN curl -fsSL \
        "https://github.com/Jake-Shadle/xwin/releases/download/${XWIN_VERSION}/xwin-${XWIN_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
    | tar -xz --strip-components=1 \
        -C /usr/local/bin \
        "xwin-${XWIN_VERSION}-x86_64-unknown-linux-musl/xwin" \
    && chmod +x /usr/local/bin/xwin

# Windows SDK + MSVC CRT headers/libs via xwin. x86_64 only (SKSE64 / CommonLibSSE-NG). ~700MB.
ENV XWIN_ACCEPT_LICENSE=true
RUN xwin splat --output /opt/xwin
```

**Note:** the base image line in the original recipe was `mcr.microsoft.com/devcontainers/python:1-3.11-bullseye`, and the LLVM apt line pointed at `llvm-toolchain-bullseye-17`. This toolkit's default `Dockerfile` moved to bookworm (bullseye's LTS window ends 2026-08-31) — the snippet above is adjusted to `llvm-toolchain-bookworm-17` accordingly, but that specific combination hasn't been tested; the original bullseye version is what was actually proven to work.

## `devcontainer.json` additions

```json
"remoteEnv": {
    "XWIN_DIR": "/opt/xwin",
    "XWIN_INCLUDE": "/opt/xwin/crt/include:/opt/xwin/sdk/include/ucrt:/opt/xwin/sdk/include/um:/opt/xwin/sdk/include/shared",
    "XWIN_LIB_X64": "/opt/xwin/crt/lib/x86_64:/opt/xwin/sdk/lib/um/x86_64"
}
```

These are consumed by `clang-cl` invocations and an xmake toolchain file that points at the splatted
SDK. If you go this route, you're on your own for the xmake project setup — the source fork's
Mora build isn't included here, since it's specific to that plugin's build system.
