# Plan 8 T15b: third-party version and integration audit

Date 2026-10-08 (the run's output and log are stamped 14:01-14:04 local time, -04:00). Tool:
`scripts/thirdparty-audit.py --json` (merged into master in `999a123`), launched under the resource-budget runner
(30.1 s, child exit 0). The run sat beside the `060d31c` follow-up in the contributor ledger; its output does not
name the commit it read, so treat the table as a reading of the tree at that point, not of the final release commit.
This file records that output; no network check was re-run to write it.

Tiers: every row below is MEASURED by the tool on 2026-10-08 and copied unchanged from its JSON output. The status
counts were summed from the same output.

## Result

| Status | Rows |
|---|---|
| OK | 66 |
| NEWER | 21 |
| NOT DELIVERED | 8 |
| UNPINNED | 0 |
| DRIFT | 0 |
| COULD NOT CHECK | 0 |
| **Total** | **95** |

Exit code 0: no DRIFT and no row that could not be checked. NEWER means upstream moved on; the tool reports it and
never fails on it. NOT DELIVERED means nothing the toolkit ships installs that tool; the reason is in the detail column.

An earlier run on the Plan 8 wave-1 tree (ledger) found 7 DRIFT (setup.sh's .NET 9/8 and Spriggit 0.40 lines), 12
NEWER and 18 UNPINNED. The ledger records the UNPINNED rows as T6d's pinning worklist and the DRIFT rows as fixed by
the wave-2 `setup.sh` work (T7).

## Before the tag: refresh within 24 hours

This result is dated. The release plan (Plan 8 FINAL, T22 step 0) requires the audit to be **re-run within 24 hours
of the tag**, versions only. A new DRIFT row (the plan's "BROKEN") stops the release there. After publication,
`tools/verify-release.sh v4.0.0` runs against the published artifact.

## Open decisions the NEWER rows feed (not decided here)

- DevBench: the recorded reference is 1.24.0; upstream showed 1.27.0. The sighting does not change the reference or
  authorize an upgrade. A bump is deliberate and tested.
- Codex CLI 0.160.0 -> 0.161.0 and Claude Code 2.1.289 -> 2.1.294: the payload fixtures need re-capturing and
  conformance re-running on current agents.
- Git for Windows 2.48.1: moves with the planned Git update (inventory-runtimes MANUAL_STEPS).
- PyNifly 29.1.0 stays rejected (re-validated 2026-10-06); Python 3.12.10 stays NEWER by design (the last 3.12 with
  a Windows installer).

## Rows

Copied from the run's JSON output, in its order. `-` means the tool printed no value for that column.

| Tool | Check | Pinned | Upstream seen | Status | Detail |
|---|---|---|---|---|---|
| Spriggit | version | 0.41.0 | 0.41.0 | OK | - |
| Spriggit | pin published | 0.41.0 | spriggit.cli | OK | - |
| Spriggit | install refs | 0.41.0 | - | OK | 7 ref(s) agree |
| Spriggit | cli flags | 0.41.0 | - | OK | 7 documented flag(s) exist upstream |
| Spriggit | nupkg tfm | 0.41.0 | net10.0 | OK | ships tools/net10.0/ for the pinned SDK |
| .NET SDK | version | 10.0.401 | 10.0.401 (active) | OK | - |
| .NET SDK | install refs | 10.0.401 | - | OK | 29 ref(s) agree |
| .NET SDK | pinned hash | 10.0.401 | .NET SDK AMD64 | OK | SHA-256 matches https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json |
| .NET SDK | pinned hash | 10.0.401 | .NET SDK ARM64 | OK | SHA-256 matches https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json |
| Node.js | version | 24.19.0 | 24.21.0 | NEWER | upstream moved on (reported, not a failure) |
| Node.js | install refs | 24.19.0 | - | OK | 12 ref(s) agree, 1 historical mention(s) skipped |
| Node.js | pinned hash | 24.19.0 | Node.js AMD64 | OK | SHA-256 matches https://nodejs.org/dist/v24.19.0/SHASUMS256.txt |
| Node.js | pinned hash | 24.19.0 | Node.js ARM64 | OK | SHA-256 matches https://nodejs.org/dist/v24.19.0/SHASUMS256.txt |
| Python | version | 3.11.15 | 3.11.17 | NEWER | upstream moved on (reported, not a failure) |
| Python | install refs | 3.11.15 | - | OK | 1 ref(s) agree, 1 historical mention(s) skipped |
| AutoMod | version | v1.11.2 | v1.11.2 | OK | - |
| AutoMod | install refs | v1.11.2 | - | OK | 8 ref(s) agree |
| AutoMod | install refs | e1c34ea34cae4b9a1a547ea6a90c8a91412d689e | - | OK | 5 ref(s) agree |
| AutoMod | tag -> commit | v1.11.2 | e1c34ea34cae | OK | tag still points at the pinned commit |
| AutoMod | upstream path | e1c34ea34cae | - | OK | 2 documented path(s) exist at the pinned commit |
| PyNifly | version | 27.2.0 | V29.1.0 | NEWER | 29.1.0 was re-validated and rejected (2026-10-06): its Blender add-on strips effect shaders on a round trip when the texture is not beside the NIF; re-validate any newer release the same way |
| PyNifly | install refs | 27.2.0 | - | OK | 2 ref(s) agree |
| PyNifly | pinned hash | 27.2.0 | PyNifly | OK | SHA-256 matches https://api.github.com/repos/BadDogSkyrim/PyNifly/releases/tags/V27.2.0 |
| PyNifly | pinned hash | 27.2.0 | PyNifly Blender add-on | OK | SHA-256 matches https://api.github.com/repos/BadDogSkyrim/PyNifly/releases/tags/V27.2.0 |
| PyNifly | upstream path | 27.2.0 | V27.2.0 | OK | 2 documented path(s) ship in io_scene_nifly.zip |
| DevBench | version | 1.24.0 | v1.27.0 | NEWER | upstream moved on (reported, not a failure) |
| DevBench | nexus skyrimspecialedition/181326 | 1.24.0 | 1.27.0 | OK | - |
| DevBench | documented API | 1.24.0 | v1.27.0 | OK | 3 documented item(s) still in upstream README.md |
| VR Address Library | version | 0.276.1 | 0.276.1 | OK | - |
| CrashLoggerSSE | delivery | - | v1.25.0 | NOT DELIVERED | a game mod the user installs with their own mod manager; the toolkit never installs or names a version of it; crash-triage reads the dumps of whatever version is installed |
| CrashLoggerSSE | nexus skyrimspecialedition/59818 | - | 1.25.0 | OK | - |
| SKSE VR | delivery | - | 2.0.12 | NOT DELIVERED | a game mod the user installs with their own mod manager; the toolkit never installs or names a version of it |
| Champollion | version | v1.3.2 | v1.3.2 | OK | - |
| Champollion | install refs | v1.3.2 | - | OK | 5 ref(s) agree |
| Champollion | pinned hash | v1.3.2 | Champollion | OK | SHA-256 matches https://github.com/Orvid/Champollion/releases/download/v1.3.2/Champollion.v1.3.2.zip |
| Caprica | version | v0.3.0 | v0.3.0 | OK | - |
| Caprica | install refs | v0.3.0 | - | OK | 5 ref(s) agree |
| Caprica | pinned hash | v0.3.0 | Caprica | OK | SHA-256 matches https://github.com/Orvid/Caprica/releases/download/v0.3.0/Caprica.v0.3.0.7z |
| xeditlib | version | v1.0.1 | v1.0.1 | OK | - |
| xeditlib | install refs | 19bef0976738a9db0760d45975263e2d7eb76062 | - | OK | 3 ref(s) agree |
| xeditlib | tag -> commit | v1.0.1 | 19bef0976738 | OK | tag still points at the pinned commit |
| zEdit | delivery | - | 0.6.7 | NOT DELIVERED | credited only: XEditLib.dll reaches users inside the pinned xeditlib commit, not from zEdit |
| xEdit (SSEEdit) | delivery | - | xedit-4.1.5f | NOT DELIVERED | the user's own modding tool; the toolkit uses XEditLib.dll from the pinned xeditlib, never xEdit |
| koffi | version | 2.15.1 | 3.3.2 | NEWER | upstream moved on (reported, not a failure) |
| koffi | install refs | 2.15.1 | - | OK | 8 ref(s) agree |
| PyFFI | version | 2.2.3 | 2.2.3 | OK | - |
| ReSaver (FallrimTools) | version | 6.0.643 | 6.0.643 | OK | - |
| ReSaver (FallrimTools) | install refs | 6.0.643 | - | OK | 7 ref(s) agree |
| JDK (recommended LTS) | version | 21 | 21.0.12.1+1 | NEWER | upstream moved on (reported, not a failure) |
| JDK (recommended LTS) | install refs | 21 | - | OK | 5 ref(s) agree |
| Microsoft OpenJDK | version | 21.0.12 | 21.0.12.1 | NEWER | upstream moved on (reported, not a failure) |
| Microsoft OpenJDK | install refs | 21.0.12 | - | OK | 7 ref(s) agree |
| Microsoft OpenJDK | pinned hash | 21.0.12 | JDK amd64 | OK | SHA-256 matches https://aka.ms/download-jdk/microsoft-jdk-21.0.12-linux-x64.tar.gz.sha256sum.txt |
| Microsoft OpenJDK | pinned hash | 21.0.12 | JDK arm64 | OK | SHA-256 matches https://aka.ms/download-jdk/microsoft-jdk-21.0.12-linux-aarch64.tar.gz.sha256sum.txt |
| Python (Windows install) | version | 3.12.10 | 3.12.15 | NEWER | stays NEWER by design: 3.12.10 is the last 3.12 with a Windows installer (later 3.12.x are source-only security releases); moving off it means another minor (3.13.x) -- a T7/user decision |
| Python (Windows install) | install refs | 3.12.10 | - | OK | 4 ref(s) agree |
| NifSkope | version | v2.0.dev9 | v2.0.dev9 | OK | - |
| NifSkope | pinned hash | v2.0.dev9 | NifSkope | OK | SHA-256 matches https://github.com/hexabits/nifskope/releases/download/v2.0.dev9/NifSkope_2_0_2023-09-29-x64.7z |
| Blender (4.2 LTS) | version | 4.2.9 | 4.2.23 | NEWER | upstream moved on (reported, not a failure) |
| Blender (4.2 LTS) | pinned hash | 4.2.9 | Blender | OK | SHA-256 matches https://download.blender.org/release/Blender4.2/blender-4.2.9.sha256 |
| Cathedral Assets Optimizer | delivery | - | 5.3.15 | NOT DELIVERED | evaluated and NOT adopted (KNOWLEDGEBASE.md); nothing tells a user to install it |
| BSArch | version | 1.0 | 1.1 | NEWER | upstream moved on (reported, not a failure) |
| Mod Organizer 2 | delivery | - | v2.5.2 | NOT DELIVERED | the user's own mod manager; the toolkit only reads its layout |
| Vortex | delivery | - | v2.8.0 | NOT DELIVERED | the user's own mod manager; the toolkit only reads its layout |
| Wrye Bash | delivery | - | v315 | NOT DELIVERED | the user's own tool; no shipped instruction installs it |
| Git for Windows | version | 2.48.1 | v2.56.0.windows.2 | NEWER | the Git these suites ran under on the dev machine; Plan 8 Q6 updates Git before T14b -- move this pin (inventory-runtimes MANUAL_STEPS) with it |
| Git for Windows | install refs | 2.48.1 | - | OK | 1 ref(s) agree |
| Codex CLI | version | 0.160.0 | 0.161.0 | NEWER | re-capture payload fixtures + re-run conformance (T15b) |
| Claude Code | version | 2.1.289 | 2.1.294 | NEWER | re-capture payload fixtures + re-run conformance (T15b) |
| devcontainer base image | version | 3.0.6-3.11-bookworm | 3.2.5-3.11-bookworm | NEWER | upstream moved on (reported, not a failure) |
| devcontainer base image | pinned hash | 3.0.6 | image index digest | OK | SHA-256 matches https://mcr.microsoft.com/v2/devcontainers/python/manifests/3.0.6-3.11-bookworm |
| busybox (CI sentinel) | version | 1.37 | 1.38.0 | NEWER | upstream moved on (reported, not a failure) |
| npm ajv | pin published | 8.20.0 | - | OK | - |
| npm ajv | version | 8.20.0 | 8.20.0 | OK | - |
| npm fast-xml-parser | pin published | 5.7.3 | - | OK | - |
| npm fast-xml-parser | version | 5.7.3 | 5.11.2 | NEWER | upstream moved on (reported, not a failure) |
| npm koffi | pin published | 2.15.1 | - | OK | - |
| npm koffi | version | 2.15.1 | 3.3.2 | NEWER | upstream moved on (reported, not a failure) |
| npm zod | pin published | 3.25.76 | - | OK | - |
| npm zod | version | 3.25.76 | 4.6.5 | NEWER | upstream moved on (reported, not a failure) |
| pypi PyYAML | pin published | 6.0.3 | - | OK | - |
| pypi PyYAML | version | ==6.0.3 | 6.0.3 | OK | - |
| pypi pytest | pin published | 9.0.3 | - | OK | - |
| pypi pytest | version | ==9.0.3 | 9.1.1 | NEWER | upstream moved on (reported, not a failure) |
| pypi ruff | pin published | 0.16.9 | - | OK | - |
| pypi ruff | version | ==0.16.9 | 0.16.10 | NEWER | upstream moved on (reported, not a failure) |
| action actions/checkout | tag -> commit | v7.0.1 | 3d3c42e5aac5 | OK | SHA matches its tag comment |
| action actions/checkout | version | v7.0.1 | v7.0.1 | OK | - |
| action actions/setup-python | tag -> commit | v7.0.0 | 5fda3b95a4ea | OK | SHA matches its tag comment |
| action actions/setup-python | version | v7.0.0 | v7.0.0 | OK | - |
| action actions/setup-node | tag -> commit | v7.0.0 | 820762786026 | OK | SHA matches its tag comment |
| action actions/setup-node | version | v7.0.0 | v7.1.0 | NEWER | upstream moved on (reported, not a failure) |
| action actions/setup-dotnet | tag -> commit | v6.0.0 | a98b56852c35 | OK | SHA matches its tag comment |
| action actions/setup-dotnet | version | v6.0.0 | v6.0.0 | OK | - |
| delivery paths | pinned+verified | - | - | OK | 230 file(s) scanned; 44 exempt with a reason |
