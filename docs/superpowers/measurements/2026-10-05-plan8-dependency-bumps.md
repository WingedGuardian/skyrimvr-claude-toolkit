# Plan 8 T6c: dependency bump research (RESEARCH PHASE, no pin changed)

Date 2026-10-05. Worktree `p8-deps`, branch from master 59e506f. This file changes no pin and no lock.
It re-verifies `dependency-bump-research.md` (the earlier pass, tree plan7) against PyPI / OSV / npm / GitHub as of today and against
THIS tree. Tiers: MEASURED = I ran/queried it today; READ = file:line; INFERRED = said so.

Method (all MEASURED today): PyPI JSON API (versions, dates, Requires-Python, requires_dist, wheel file lists), OSV `api.osv.dev` per
exact version, `npm view`, `npm audit --package-lock-only` on the real lock, `gh api` for tags/SHAs, MCR registry for the image digest.
Two experiments, scratch only (no repo file touched): (a) `ruff check` with five ruff versions over `git ls-files '*.py'` (175 files);
(b) `pytest --collect-only -W error::PytestRemovedIn9Warning -W error::PytestReturnNotNoneWarning -W error::PytestDeprecationWarning` of
`tests/` under pytest 8.0.0, 8.4.2 and 9.0.3, plus the three lock/adapter test files executed under each. The full suite was NOT run.

## 0. What changed versus the earlier report

1. **The ruff HARD gate is already RED on this tree, with every ruff version.** `ruff check --select=E9,F63,F7,F82` reports the same 4 F821
   (`Undefined name json`) on `tools/adapting/examples/exit2.slots.py:13` and `json_deny.slots.py:13,26,30` under 0.3.0, 0.3.7, 0.15.22,
   0.16.9 and 0.16.10 (MEASURED). Those files are slot FRAGMENTS spliced into the adapter template (the template supplies `json`), so the
   finding is a false positive, not a ruff-version effect; `ci.yml:62-65` runs ruff over every `git ls-files '*.py'`, so the `python` CI job
   fails today on master regardless of any bump. Must be fixed (exclude `*.slots.py` from the gate or `# noqa: F821`, with a red twin) BEFORE
   the ruff bump can be judged. Pre-arc defect, not introduced by T6c; flagged for T10.
2. **Test count is 7538, not 4357** (`--collect-only`, identical under pytest 8.0.0 / 8.4.2 / 9.0.3). The collected set has zero errors with
   the three `-W error` filters on pytest 8.4.2 and 9.0.3: no test returns a non-None value, no PytestRemovedIn9Warning at collection
   (MEASURED). This closes the INFERRED risk for collection time only; run-time warnings are still decided by the real suite run.
3. **pygments is a NEW transitive dependency of pytest >= 8.4** and pygments < 2.20.0 carries GHSA-5239-wwwm-4pmq / CVE-2026-4539
   (AdlLexer ReDoS, local, CVSS 3.1 low-impact; fixed 2.20.0; MEASURED OSV: 2.17.1 to 2.19.2 affected, 2.20.0 and 2.21.0 clean). The re-lock
   must resolve pygments >= 2.20.0 (the resolver will give 2.21.0, requires-python >=3.9). Not reachable (pytest uses pygments only to colour
   tracebacks), but a lock that pins a known-advisory version would be a regression from "0 advisories".
4. **Master moved**: Spriggit is already pinned `--version 0.40.0` in README/docs/setup.sh (the earlier report saw it unpinned); `.config/dotnet-tools.json`
   still 0.40.0 (T6b moves it). `.python-version` 3.11.15, `.node-version` 20.20.2 unchanged.
5. Everything else in the earlier report re-verified (see tables). No claim was found wrong other than the above; corrections are marked.

## 1. Pins enumerated (this tree)

| File | Pins |
|---|---|
| `requirements.txt` (READ) | pydantic==2.6.0, cattrs==23.2.1, PyYAML==6.0.1, pytest==8.0.0, pytest-cov==4.1.0, ruff==0.3.0, mypy==1.9.0 |
| `requirements.lock` (READ, 371 lines, 20 packages, all wheel hashes, `input-sha256` header) | + pydantic_core 2.16.1, annotated-types 0.8.0, attrs 26.1.0, typing_extensions 4.16.0, coverage 7.16.2, pluggy 1.6.0, iniconfig 2.3.0, packaging 26.3, mypy_extensions 1.1.0, colorama 0.4.6 (win32), exceptiongroup 1.3.1 and tomli 2.4.1 (py<3.11) |
| `requirements-windows.txt/.lock` | pywin32==306 (win32) |
| `package.json` | ajv ^8.17.1, fast-xml-parser ^4.3.6, zod ^3.23.8; engines node >=20.0.0 |
| `package-lock.json` (lockfileVersion 3) | ajv 8.20.0, fast-deep-equal 3.1.3, fast-uri 3.1.8, json-schema-traverse 1.0.0, require-from-string 2.0.2, fast-xml-parser 4.5.7, strnum 1.1.2, zod 3.25.76 |
| runtimes | `.python-version` 3.11.15, `.node-version` 20.20.2, `.config/dotnet-tools.json` spriggit.cli 0.40.0 |

Real usage (grep, READ, re-run today): `import pydantic|cattrs|mypy|pytest_cov|win32*` appears in NO `.py`; no mypy/pytest-cov invocation anywhere;
the contained launcher disables plugin autoload (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`), so pytest-cov could not load. `yaml` is used only by
`pytest.importorskip("yaml")` at `tests/test_adapter_payload.py:118` and `tests/test_dependency_locks.py:79`. No JS in the repo requires
ajv, zod or fast-xml-parser. BUT `docs/container-vs-windows.md:23-24` tells users the container does "Generate FOMOD XML: fast-xml-parser" and
"Validate JSON schemas: ajv, zod", and the Dockerfile puts them on `NODE_PATH` for the user's own scripts: that is a user-facing claim, so the three
npm packages are KEPT (no such claim exists for pydantic, cattrs, mypy, pytest-cov or pywin32; the only mentions are the lock policy text
`docs/harness-integrations.md:88-90` and CHANGELOG).

## 2. Python packages

Wheel columns are MEASURED from PyPI file lists: cells are the CPython minors (310-314) that have a binary or pure wheel.
"arm64": linux aarch64 and win_arm64. OSV = known advisories on that exact version today.

### pytest (the suite; CVE on the current pin)

| Version | Date | Requires-Python | OSV |
|---|---|---|---|
| 8.0.0 (current) | 2024-01-27 | >=3.8 | GHSA-6w46-j5rx-g56g / CVE-2025-71176 / PYSEC-2026-1845 |
| 8.4.2 (last 8.x) | 2025-09-04 | >=3.9 | same advisory (still affected); adds dep pygments>=2.7.2 |
| 9.0.3 | 2026-04-07 | >=3.10 | clean; advisory range is introduced 0, fixed 9.0.3 (MEASURED) |
| 9.1.1 (latest) | 2026-06-19 | >=3.10 | clean; 9.1.x adds deprecations for features we do not use |

Pure wheel (py3-none-any), so Windows/Linux x64 and arm64, 3.10-3.14 all fine. The CVE is the UNIX `/tmp/pytest-of-{user}` predictability; fixed only in
9.0.3. Breaking changes that touch this repo (pytest changelog, as read by the earlier pass, re-checked by experiment today): 8.4.0 turns "test returns non-None" into a
failure; 9.0.0 turns PytestRemovedIn9Warning into errors and drops 3.9. Experiment: collection under 9.0.3 with all three filters as errors passes (7538 tests); the
three lock/adapter test files pass 45/45 on 8.0.0, 8.4.2 and 9.0.3. `scripts/check_could_not_check.py` is the one parser of pytest's text output
(`tests/test_could_not_check.py` is in the 45; it passes on 9.0.3). **Recommend 9.0.3** as two commits (8.4.2 first, so a failure bisects); stop at 8.4.2 with the CVE recorded as accepted only if the real suite shows a failure that a test-file fix cannot cure.
`scripts/lock-python-dependencies.py:23` guards pytest conditionals with `== "8.0.0"`: it must be generalised BEFORE any pytest bump or it silently stops checking.

### ruff (CI gate)

Versions: 0.3.0 (2024-02-29), 0.3.7 (2024-04-12, last 0.3.x), 0.15.0..0.15.22 (2026-02-03..07-16), 0.16.0 (2026-07-23) .. 0.16.9 (2026-09-24), 0.16.10 (2026-10-01). Requires-Python >=3.7;
cp310-cp314 wheels on win_amd64, win_arm64, linux x86_64 and aarch64 for every version tested; OSV clean on all. ruff is 0.x and has no
support window, only "newest". Experiment (MEASURED): the gate result is byte-identical on 0.3.0, 0.3.7, 0.15.22, 0.16.9, 0.16.10 (the 4 F821 above, and nothing else);
the ADVISORY full-ruleset run changes from 134 findings (0.3.x) to 132 (0.15.22) to 1372 (0.16.x, default rule set 59 to 413). Breaking change for us: only the advisory output
(`ci.yml:56-58` says "18 style findings", already stale at 134). **Recommend 0.16.9** (14 days old on 2026-10-08; gate equal to 0.3.0). Fallback 0.3.7. Both are CI-only tools no user installs.
Needs the slot-fragment fix first (section 0.1) and a red twin (undefined name in a scratch file must fail the gate).

### PyYAML

6.0.1 (2023-07-17; cp310-cp312 only), 6.0.2 (2024-08-06; +cp313), 6.0.3 (2025-09-25; cp310-cp314 on win_amd64 / linux x86_64 / linux aarch64; win_arm64 only cp312-cp314, so
`--only-binary` fails on Windows-ARM Python 3.10/3.11, same as today). OSV clean. Only `yaml.safe_load` is used. 6.0.3 changelog class is build/Python-3.13/3.14 support (INFERRED, the PyYAML changelog was not fetched).
**Recommend 6.0.3**: it is the only version with Python 3.13 wheels and the reason the lock can claim 3.10-3.13. The two importorskip tests pass on 6.0.3 (MEASURED, in the 45).

### pydantic 2.6.0 / pydantic_core 2.16.1, cattrs 23.2.1, mypy 1.9.0, pytest-cov 4.1.0 (all unused)

- pydantic: 2.6.4 (core 2.16.3), 2.12.5 (core 2.41.5), 2.13.5 (2026-08-28, core 2.46.5). pydantic pins core exactly; 2.11+ adds typing-inspection. pydantic-core 2.16.1 has no cp313/cp314 wheel (cp310-312 only); 2.41.5 and 2.46.5 have cp310-cp314 (win_arm64 from cp311). OSV clean on all.
- cattrs: 23.2.3 (same line); 25.3.0 and 26.x need attrs>=25.4.0 (lock has 26.1.0, fine), 26.x needs Python >=3.10. OSV clean.
- mypy: 1.9.0 has **no linux aarch64 wheel** (MEASURED): `install-dependencies.sh:6` / ci.yml use `--only-binary=:all:`, so the devcontainer install fails on any ARM Linux/Apple-Silicon Docker host (INFERRED from the file list, not a real arm64 build); no cp313/314 wheel either. 1.20.2 (2026-04-21, >=3.10) has all five platforms and adds librt + pathspec; 2.4.0 latest (2.x flips defaults; the earlier report says 2.4.0 dropped macOS x86_64 wheels, not re-checked).
- pytest-cov: 7.1.0 latest (needs coverage>=7.10.6; lock has 7.16.2). No coverage config exists.
**Recommend: drop all four** (and with them annotated-types, attrs, pydantic_core, coverage, mypy_extensions; typing_extensions stays via exceptiongroup on Python <3.13). Rationale: nothing runs them, our tests cannot prove any bump of them safe, dropping removes the ARM-Linux install failure and the Python 3.13 wheel gap, and shrinks the lock from 20 to about 12 packages. This is a user-visible removal from the container image, so it needs the user's yes (it is the only decision in this report I cannot make for them). If the answer is "keep": pydantic 2.12.5 + core 2.41.5, cattrs 23.2.3, mypy 1.20.2, pytest-cov 7.1.0, each labelled install-only in `requirements.txt`.

### pywin32 306 (Windows lock, unused)

306 has win_amd64 wheels cp310-cp312 only; 311 (2025-07-14) cp310-cp314 on win_amd64 and win_arm64; 312 (2026-06-04, >=3.9). OSV clean on all. Nothing imports it
(`xeditlib` is Node/koffi, `docs` say so). **Recommend drop the Windows lock** (delete `requirements-windows.txt/.lock`, remove the second parametrize row at `tests/test_dependency_locks.py:12-13`, edit
`docs/harness-integrations.md:79-90` and `README.md:556` neighbours); fallback **311**.

### Transitive pins (all MEASURED = PyPI latest, OSV clean today)

pluggy 1.6.0, iniconfig 2.3.0, packaging 26.3, colorama 0.4.6, exceptiongroup 1.3.1, tomli 2.4.1, typing_extensions 4.16.0 (kept). pygments 2.21.0 (new, must be >= 2.20.0).
Combinations pytest 8.0.0 + pluggy 1.6.0 and cattrs 23.2.1 + attrs 26.1.0 exist only in our lock (authors never released them together); the bump to pytest 9 removes the first.

## 3. npm packages

All rows MEASURED (`npm view`, `npm audit --package-lock-only`, OSV). None declares an `engines` field; all load on Node 20-24.

| Package | Lock | Newer in range | Newer majors | Advisory | Recommendation |
|---|---|---|---|---|---|
| ajv | 8.20.0 | none (latest 8.x) | none (`legacy` tag 6.15.0) | none | keep 8.20.0 |
| fast-xml-parser | 4.5.7 | none (last 4.x, tag `legacy`) | 5.7.3 .. 5.11.2 (4-6 transitive deps vs 1; `type: module` with a CJS `require` condition) | GHSA-gh4j-gqv2-49f6 / CVE-2026-41650, XMLBuilder comment/CDATA injection, range <5.7.0, moderate 6.1; `npm audit` flags it, no 4.x fix | keep 4.5.7: nothing in this repo calls XMLBuilder; take 5.7.3 only if the user confirms their FOMOD generator uses `XMLBuilder` on untrusted text (decision for the user; the doc table names fast-xml-parser for "Generate FOMOD XML") |
| zod | 3.25.76 | none (last 3.x) | 4.6.5 (message/error API, `.email()` etc. reshaped) | none | keep; 3.25.76 already ships `zod/v4` |
| fast-uri | 3.1.8 | latest 3.x | 4.2.1 (unreachable: ajv wants ^3.0.1) | none | keep |
| strnum | 1.1.2 | latest 1.x | 2.4.2 (fxp 5 line) | none | keep |
| fast-deep-equal / json-schema-traverse / require-from-string | 3.1.3 / 1.0.0 / 2.0.2 | latest | n/a | none | keep |

`package.json` carets are the only non-exact npm path: make them exact (`8.20.0`, `4.5.7`, `3.25.76`) so a plain `npm install` cannot float; `package-lock.json` stays byte-identical (verify with a diff);
`test_node_lock_tracks_declared_dependencies` ties the two. `engines.node >=20.0.0` becomes `>=22` or `>=24` per T6b (Node 20 EOL 2026-04-30 per the earlier pass; not re-fetched).

## 4. Recommended target set (one exact set; nothing floats)

| Package | Current | Target | Reason |
|---|---|---|---|
| pytest | 8.0.0 | 9.0.3 (commit 1 at 8.4.2) | only line fixing CVE-2025-71176; collection clean under -W error; needs the real suite to confirm |
| pygments (new) | none | 2.21.0 (any >= 2.20.0) | new pytest dep; < 2.20.0 has CVE-2026-4539 |
| ruff | 0.3.0 | 0.16.9 (fallback 0.3.7) | gate output identical on 5 versions; advisory-only noise changes; fix slots F821 first |
| PyYAML | 6.0.1 | 6.0.3 | adds 3.13/3.14 wheels; safe_load only |
| pydantic + pydantic_core | 2.6.0 + 2.16.1 | drop (else 2.12.5 + 2.41.5) | unused; core 2.16.1 has no 3.13 wheel |
| cattrs | 23.2.1 | drop (else 23.2.3) | unused |
| mypy (+ mypy_extensions) | 1.9.0 | drop (else 1.20.2) | unused; 1.9.0 has no linux aarch64 wheel, install fails on ARM |
| pytest-cov (+ coverage) | 4.1.0 | drop (else 7.1.0) | unused; cannot load under launcher |
| annotated-types, attrs | 0.8.0, 26.1.0 | drop with pydantic/cattrs | only needed by those |
| pywin32 | 306 | drop file (else 311) | unused; 306 has no 3.13 wheel |
| pluggy, iniconfig, packaging, colorama, exceptiongroup, tomli, typing_extensions | latest | keep (re-lock) | already newest, clean |
| ajv / zod / fast-xml-parser | 8.20.0 / 3.25.76 / 4.5.7 | keep, exact in package.json | no in-range bump exists; majors add surface for unused code; fxp advisory is XMLBuilder-only |
| fast-uri, strnum, fast-deep-equal, json-schema-traverse, require-from-string | as locked | keep | latest of their line |

Post-bump lock (if the four drops are approved): pytest, pluggy, iniconfig, packaging, pygments, colorama (win32), exceptiongroup + tomli (py<3.11), typing_extensions, PyYAML, ruff = 11 or 12 packages, every one with cp310-cp313 wheels (pure or compiled) on win_amd64, linux x86_64 and linux aarch64; win_arm64 lacks PyYAML 6.0.3 cp310/cp311 (stated, untested platform).

## 5. Proposed commit order (small, separately revertable; every commit re-locks with hashes)

Gate set per commit: `tests/test_dependency_locks.py` and `tests/test_mutation_anchors.py` (always); then the stated test. The controller's wave merge runs the full suite and the full mutation file once on the final set; per-commit full runs are not required but are possible by CPU-capped shard if the user wants them.

| # | Commit | Test it needs |
|---|---|---|
| 0 | (no commit) preflight: slot-fragment F821 finding handed to T10 | n/a |
| 1 | `locks: generalise the pytest conditional-deps guard in lock-python-dependencies.py:23` (any pytest, same three conditionals; tooling only) | mutation twin: guard must still raise for a report missing tomli/exceptiongroup/colorama under pytest 9; lock output byte-identical for current inputs |
| 2 | `ci: ruff gate ignores template slot fragments` (exclude `*.slots.py` or per-file F821) | red twin: undefined name in a real `.py` still fails the gate; slots files pass |
| 3 | `deps: PyYAML 6.0.1 -> 6.0.3` | the two importorskip tests must run, not skip |
| 4 | `deps: pytest 8.0.0 -> 8.4.2` (adds pygments; resolver 2.21.0) | full suite on both legs; `-W error` run; `test_could_not_check.py`; `pip install --dry-run --only-binary=:all: --require-hashes` per cell |
| 5 | `deps: pytest 8.4.2 -> 9.0.3` | same as 4; if a failure needs more than a test-file edit, revert and keep 8.4.2 with the CVE recorded |
| 6 | `deps: ruff 0.3.0 -> 0.16.9` (+ fix the stale "18 style findings" comment, ci.yml:56-58) | gate zero findings after commit 2; red twin |
| 7 | `deps: drop unused pydantic/cattrs/mypy/pytest-cov (+attrs, annotated-types, coverage, pydantic_core, mypy_extensions)` (needs user yes; else install-only bumps) | lock tests; cold `--require-hashes` install on linux x86_64 py3.10-3.13, windows x64 py3.10-3.13, and linux aarch64 (`ubuntu-24.04-arm`) |
| 8 | `deps: remove pywin32 / the Windows lock` (else 311) | `test_dependency_locks.py` parametrize edit; Windows install |
| 9 | `deps: package.json exact versions` | `test_node_lock_tracks_declared_dependencies`; `npm ci` leaves `package-lock.json` unchanged |

Commit 7 can move earlier (before 4) if the user approves: fewer packages in every later re-lock.

## 6. Delivery paths NOT exact+verified today (feeds T6d; file:line READ in this tree)

Pin candidates measured today are in brackets.

| # | Path (file:line) | Problem | Pin to |
|---|---|---|---|
| 1 | `package.json:10-12` carets | `npm install` floats | exact versions (section 3) |
| 2 | `README.md:253`, `docs/installing-tools.md:8`, `docs/xelib-guide.md:14`, `setup.sh:640`, `tools/skyrim-winner.js` hint: `npm install github:WingedGuardian/xeditlib` | default-branch HEAD; xeditlib has no lockfile (MEASURED 404), so its koffi dependency floats (koffi latest 3.3.2 today) | `github:WingedGuardian/xeditlib#19bef0976738a9db0760d45975263e2d7eb76062` (= tag v1.0.1 = HEAD today); better: add it to `package.json` + lock so `npm ci` records the commit and koffi's integrity |
| 3 | `README.md:256`, `docs/installing-tools.md:22`, `setup.sh:648`: `git clone .../spookys-automod-toolkit` | HEAD | tag v1.11.2 = `e1c34ea34cae4b9a1a547ea6a90c8a91412d689e` (checkout by SHA) |
| 4 | `docs/installing-tools.md:12,15`, `README.md:254` Champollion/Caprica "a release" | no version, no checksum; assets expose no digest | Champollion v1.3.2, Caprica v0.3.0 (both latest, 2023), record SHA-256 of the zip/7z |
| 5 | `README.md:258`, `docs/installing-tools.md:35` PyNifly "latest release" | moves under us (V29.1.0 published 2026-10-04) | the tested tag + SHA-256 of `io_scene_nifly.zip` |
| 6 | `README.md:257`, `docs/installing-tools.md:30-33`, `setup.sh:652`: `pip install pyffi setuptools` | no pins, no hashes; setuptools moves (84.0.0 today) | a tiny hash-locked requirements file (pyffi 2.2.3 + the tested setuptools) |
| 7 | `scripts/read-save.py:13,25`, `agent/skills/skyrim-save/SKILL.md:10`, `.agents/skills/skyrim-save/SKILL.md:10`, `KNOWLEDGEBASE.md:905`: `pip install lz4` | unpinned (4.4.5 today) | lock entry with hashes |
| 8 | `README.md:285`, `docs/getting-started.md:57,210`: `npm install -g @anthropic-ai/claude-code`; `devshell.sh:7`, `docs/container-vs-windows.md:115`: `@devcontainers/cli` | latest at install time | state the tested version (`@x.y.z`) or mark as outside the pin contract |
| 9 | `.devcontainer/Dockerfile:8` base image tag | tag, no digest | `...3.0.6-3.11-bookworm@sha256:aea59f6834231486ee510ed90fc89659762e8ac246bb5e5fc49809ab736a0b94` (MCR index digest today); note newer tags exist (3.2.3-3.11-bookworm) |
| 10 | `.devcontainer/Dockerfile:21` `curl .../setup_20.x \| bash -` | channel, EOL Node 20, curl-to-shell | T6b: Node 24 from a pinned tarball with SHA-256 (nodejs.org SHASUMS256), no pipe-to-shell |
| 11 | `.devcontainer/Dockerfile:27-29` `dotnet-install.sh --channel 9.0` | channel + unverified script | T6b: SDK 10 with `--version <exact>`, script pinned by checksum |
| 12 | `.devcontainer/Dockerfile:15` apt `openjdk-17-jdk-headless` | distro-current 17.x | `=<exact>` from the bookworm repo, or accept apt signatures and say so |
| 13 | `.github/workflows/ci.yml:17,43,44,71,72,94,95,244,308,317,321`, `release.yml:20` `actions/*@v7` | moving tags | SHAs: checkout v7.0.1 `3d3c42e5aac5ba805825da76410c181273ba90b1`, setup-python v7.0.0 `5fda3b95a4ea91299a34e894583c3862153e4b97`, setup-node v7.0.0 `820762786026740c76f36085b0efc47a31fe5020` (all MEASURED via `gh api` today) |
| 14 | `ci.yml:15,41,69,92,242,297-299`, `release.yml:18` `ubuntu-latest` / `windows-latest` | floating images | `ubuntu-24.04`, `windows-2025` named in the matrix (T10) |
| 15 | `ci.yml:33,249,331,333` `apt-get install shellcheck jq rsync`, `choco install jq` | unpinned CI tooling | exact version or accept runner image pin (CI only) |
| 16 | `ci.yml:46,97` `python-version-file: .python-version` (3.11.15) vs `ci.yml:319` `"3.11"` | floating patch on the Windows leg (documented reason ci.yml:312-316) | explicit matrix values for 3.10-3.13 (T10) |
| 17 | `setup.sh:68,86,93,142,177,185-186,199`, `docs/getting-started.md:213`, `README.md:509`: `winget install Python.Python.3.12`, `OpenJS.NodeJS.LTS`, `Microsoft.DotNet.SDK.9/.8`, `Microsoft.OpenJDK.21` | no `--exact --version`; 3.12 is not a tested cell today | `winget install --id X --exact --version V --no-upgrade` like `agent/skills/toolkit-setup/SKILL.md:47` and `tools/inventory-runtimes.py:234` already do |
| 18 | `setup.sh:178,644`, `README.md:255`, `docs/installing-tools.md:18`: `dotnet tool install ... --version 0.40.0` | exact, but 0.40.0 is superseded by T6b (0.41.0) and NuGet-signature-only verification | update with T6b; keep the manifest + `dotnet tool restore` as the exact path |
| 19 | `docs/skse-cross-compile.md:38-60` Dockerfile sketch (apt, `xmake.io/shget.text \| bash`, XWIN 0.6.5 no checksum) | doc-only sketch | label as unsupported/untested or pin |
| 20 | pip itself (`ci.yml:61,341`, `install-dependencies.sh:6`) | runner's/user's pip version | acceptable: `--require-hashes` is pip-version independent; record the tested pip |

Already exact+verified: `requirements*.lock` installs (`--only-binary=:all: --require-hashes`: `ci.yml:61,341`, `install-dependencies.sh:6`, `README.md:556`), `npm ci --ignore-scripts` (`ci.yml:77`,
`install-dependencies.sh:19`), `dotnet tool restore` (`install-dependencies.sh:22`), `busybox@sha256:bdf57e52...` (`ci.yml:352-353`).

## 7. Runtime ranges (input for T10; unchanged from the earlier pass, partly re-checked)

Tested-range policy per the user ruling: Python 3.10-3.13 (3.10 hits end-of-life this month per PEP 619, kept as the stated floor), Node 24 primary (22 optional), .NET 10, JDK 17 and 21. After section 4's targets the lock installs on all of
3.10-3.13 on win_amd64, linux x86_64 and linux aarch64 (MEASURED per package above, not by a real install). Today's lock does not (3.13: pydantic-core 2.16.1, PyYAML 6.0.1, pywin32 306 lack wheels; ARM Linux: mypy 1.9.0).
`setup.sh:78-85` still accepts Python >= 3.8 while pytest 9 needs >= 3.10: the stated floor for running the TESTS becomes 3.10; the hooks' own floor is a separate T7 decision.
The devcontainer base image is Python 3.11 only, so 3.10/3.12/3.13 cells need CI matrix entries (T10), not the image.

## 8. What I did not verify

- Run-time pytest 9 behaviour under the real suite (only collection + 45 tests ran); ruff 0.4-0.15 changelogs (replaced by measuring the gate on 0.15.22); PyYAML 6.0.3 and pywin32 changelogs (not fetched; usage is `safe_load` / none).
- That a real linux/arm64 install of the current lock fails on mypy 1.9.0 (derived from the PyPI file list).
- mypy 2.4.0 dropping macOS x86_64 wheels and Node 20/Python 3.10 end-of-life dates (taken from the earlier pass, not re-fetched).
- Whether the maintainer's own FOMOD generator uses `XMLBuilder` (decides the fast-xml-parser 5.7.3 question).
- Scratch artifacts: venv-like `--target` dirs and the query script under the session scratchpad `.../scratchpad/t6c/` (about 190 MB total, ruff x5 and pytest x3); a stray `pt80/` created in the worktree by mistake was removed to the Recycle Bin and `git status` is clean.

## 9. Implementation addendum (same day): rulings applied, and two corrections to this report

Coordinator rulings applied as separate commits on `p8-deps` (ruff gate fix, lock-generator guard, drops, PyYAML 6.0.3, pytest 8.4.2, pytest 9.0.3, ruff 0.16.9, exact package.json, fast-xml-parser 5.7.3 with a smoke). Every re-lock was checked by a hash-required
`pip install --dry-run` per cell (Python 3.10-3.13 x win_amd64, linux x86_64, linux aarch64: 12 of 12 OK after PyYAML 6.0.3; 3.13 failed on PyYAML 6.0.1 as predicted) and by a cold venv install from the lock.

Corrections (MEASURED during implementation):
1. **mypy 1.9.0 does NOT fail to install on ARM Linux.** It publishes a pure-Python `py3-none-any` wheel next to the compiled ones, so `--only-binary=:all:` installs it on aarch64 (the old lock passed the aarch64 cells for 3.10-3.12). Section 0.3 / 2 / 4's "install fails on ARM" claim, inherited from the earlier pass, is wrong; mypy is still dropped because it is unused, and the real 3.13 gap was pydantic-core 2.16.1, PyYAML 6.0.1 and pywin32 306.
2. **fast-xml-parser 4.5.7 already escapes the XMLBuilder comment/CDATA delimiters** (same output as 5.7.3; 4.3.6 injects). The advisory range `< 5.7.0` does not carve out the 4.5.x backport, so `npm audit` flags 4.5.7 although the behaviour is fixed. "No 4.x fix" in sections 0.4 / 3 was wrong about behaviour. The 5.7.3 bump was made as ruled (clean audit; four more dependencies) in its own commit so it can be reverted to 4.5.7 alone.
3. The ruff hard-gate failure (section 0.1) is fixed by `ruff.toml` (`force-exclude`, `extend-exclude = ["*.slots.py"]`), a naming rule shared with `tests/test_adapter_template.py`, with a twin that a real F821 still fails the gate.
