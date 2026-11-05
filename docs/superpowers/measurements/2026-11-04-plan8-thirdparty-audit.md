# Plan 8 T15b: third-party version audit, refresh

Date 2026-11-04, on `5d078e4`. Tool: `scripts/thirdparty-audit.py --json`. Every row is MEASURED by the tool. Refresh within 24 h before the tag; a new DRIFT row blocks it.

| Status | Rows |
|---|---|
| OK | 75 |
| NEWER | 29 |
| NOT DELIVERED | 9 |
| UNPINNED | 0 |
| DRIFT | 0 |
| COULD NOT CHECK | 2 |
| **Total** | **115** |

Tool exit 2: from the 2 COULD NOT CHECK rows, with no DRIFT. Both are the shared core's CI action, pinned by commit in a repository with no release tags, so there is no version to compare.

Agents moved since the last payload recheck (`2026-11-02-plan8-agent-recheck.md`: Claude Code 2.1.316, Codex CLI 0.165.0): upstream is now Claude Code 2.1.318 and Codex CLI 0.166.0. The recheck was not repeated, by the user's ruling of 2026-11-04 that no further tests are needed.

## NEWER and COULD NOT CHECK

| Tool | Pinned | Upstream | Status |
|---|---|---|---|
| House Carl TOML support | `0.14.0` | `0.15.2` | NEWER |
| Node.js | `24.19.0` | `24.22.2` | NEWER |
| Python | `3.11.15` | `3.11.18` | NEWER |
| PyNifly | `27.2.0` | `V30.3.0` | NEWER |
| DevBench | `1.28.0` | `v1.32.0` | NEWER |
| VR Address Library | `0.279.0` | `0.294.0` | NEWER |
| koffi | `2.15.1` | `3.3.6` | NEWER |
| ReSaver Plus (ReSaverPP) | `098eb8c1c3a235` | `313d42b62eacdd663d10` | NEWER |
| JDK (recommended LTS) | `21` | `21.0.13+9` | NEWER |
| Microsoft OpenJDK | `21.0.12` | `21.0.13.0.3` | NEWER |
| Python (Windows install) | `3.12.10` | `3.12.16` | NEWER |
| 7-Zip | `26.00` | `26.05` | NEWER |
| Blender (4.2 LTS) | `4.2.9` | `4.2.23` | NEWER |
| BSArch | `1.0` | `1.1` | NEWER |
| Git for Windows | `2.55.0.5` | `v2.56.0.windows.4` | NEWER |
| Codex CLI | `0.160.0` | `0.166.0` | NEWER |
| Claude Code | `2.1.289` | `2.1.318` | NEWER |
| devcontainer base image | `3.0.6-3.11-boo` | `3.3.2-3.11-bookworm` | NEWER |
| busybox (CI sentinel) | `1.37` | `1.38.0` | NEWER |
| npm fast-xml-parser | `5.7.3` | `5.12.0` | NEWER |
| npm koffi | `2.15.1` | `3.3.6` | NEWER |
| npm zod | `3.25.76` | `4.6.7` | NEWER |
| pypi pytest | `==9.0.3` | `9.2.0` | NEWER |
| pypi ruff | `==0.16.9` | `0.17.3` | NEWER |
| action WingedGuardian/modding-agent-harness | `50a993ba807d` | `-` | COULD NOT CHECK |
| action WingedGuardian/modding-agent-harness | `-` | `-` | COULD NOT CHECK |
| action actions/upload-artifact | `v4.6.2` | `v7.0.2` | NEWER |
| action actions/setup-python | `v7.0.0` | `v7.1.0` | NEWER |
| action actions/setup-node | `v7.0.0` | `v7.1.0` | NEWER |
| action actions/download-artifact | `v4.3.0` | `v8.0.2` | NEWER |
| action actions/setup-dotnet | `v6.0.0` | `v6.1.0` | NEWER |
