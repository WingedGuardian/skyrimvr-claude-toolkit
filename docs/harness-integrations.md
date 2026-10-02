# Integration safety and reproducible dependencies

## Spriggit publication

`tools/spriggit-cli.sh` preserves input/output basenames (the Spriggit ModKey) and runs
the serializer in a unique shallow scratch directory. Override its scratch parent with
`SPRIGGIT_WORK_ROOT`; Python 3 is required for publication. No prerequisites are installed.

Publication rejects missing/empty files, linked artifacts and directory results without
nonempty YAML. This is structural artifact validation, **not** proof of valid plugin content.
Input/output overlap and linked destinations are rejected. Output is copied to a unique
staging directory beside the destination, validated, then renamed into place. An existing
destination is renamed aside first and restored if publication fails. Old YAML never overlays
the new result. A process crash between renames can leave `.spriggit-previous-*`; preserve it
and inspect before recovery. Concurrent publication to the same destination is rejected.
Post-commit cleanup failures are warnings naming retained artifacts, not failed publication.
After a crash, inspect a retained `.spriggit-lock-*` before removing it; no stale lock is stolen.

## Container isolation

The workspace and game/profile/overwrite bind mounts are read-only. This includes
`/workspace/Data`, which was previously a writable alias of a read-only game mount.
`node_modules` uses a container volume; dependency caches and installed Python/.NET tools
stay inside the container. Copy files to container-local scratch space when writable input
is required. The editor cannot modify the host workspace through this container.

The helper uses a unique container name and removes only its own container/anonymous volume.
It checks Docker availability and mount directories before startup. Missing Docker/daemon
is reported as **COULD NOT CHECK** with exit 2. Dependency provisioning is fail-fast: a
failed pip/npm/dotnet command prevents the interactive shell and never reports Done.

Dev Container mounts use schema-supported strings with `readonly`, not unsupported object
properties. Sources containing commas cannot be represented by this mount syntax.
The live sentinel test uses only disposable paths, never pulls an image, and requires an
existing `busybox:1.37` image and running Linux Docker daemon. Otherwise it reports a skip
with COULD NOT CHECK; fake-Docker tests do not certify actual filesystem enforcement.

## Load-order configuration

The xelib helper chooses `plugins.txt` in this order:

1. Explicit `PLUGINS_TXT`.
2. Explicit `SKYRIM_LOADORDER_DIR` environment variable.
3. Setup's `SKYRIM_LOADORDER_DIR` in the toolkit-root `skyrim-paths.env`, then legacy
   `.claude/skyrim-paths.env`. `SKYRIM_PATHS_FILE` explicitly selects a configuration file.
4. Existing LOCALAPPDATA VR/SE/LE detection with its rival-file warning.

Configuration is read as data. The parser supports setup's single-quoted paths and escaped
apostrophes; malformed configured paths fail visibly rather than selecting a plausible wrong
load order. A configured-but-missing plugins list also fails, rather than silently falling back.
This does not change guard-engine configuration or generate new agent instructions.

## Dependency locks

`package-lock.json` freezes Node dependencies and integrity hashes. Provision with
`npm ci --ignore-scripts --no-audit --no-fund`; never use global npm for this project.

`requirements.lock` and `requirements-windows.lock` freeze direct/transitive versions and
wheel hashes for Python 3.10/3.11. Provision in an isolated virtual environment or container:

```text
python -m pip install --only-binary=:all: --require-hashes -r requirements.lock
python -m pip install --only-binary=:all: --require-hashes -r requirements-windows.lock
```

These locks freeze package resolution, not the complete base image, SDK or interpreter.
The Windows lock adds pywin32; it does not replace the common lock. `cattrs` moves from
withdrawn 23.2.0 to 23.2.1: upstream identifies a Python 3.11 import correction in
[its changelog](https://catt.rs/en/latest/history.html). No host package is upgraded.

An intentional dependency refresh, outside all tests, may use:

```text
python -m pip install --dry-run --ignore-installed --only-binary=:all: --report report.json -r requirements.txt
python scripts/lock-python-dependencies.py --report report.json --input requirements.txt --output requirements.lock
```

Pass repeated `--report` arguments to merge resolutions from supported target environments.
For the common lock include Windows/Python 3.10 resolution (it contains pytest's Windows and
older-Python conditional dependencies), alongside Linux/Python 3.11 evidence. Missing conditional
dependencies or conflicting resolved versions stop generation; they are never silently omitted.
The checked-in lock was generated from the Windows 3.10 superset and its Linux 3.11 wheels were
verified by hash-required dry-run, not a real Linux installation.
Repeat with the Windows input/output files on Windows. The generator rejects withdrawn
wheels and performs no installation. The reports and PyPI metadata define the frozen result;
subsequent installs do not resolve fresh versions. Input checksums normalize line endings.

## Test-only package-manager barriers

Run `python -B tests/run_harness_tests.py` (or supply pytest targets/options). It constructs
a scrubbed environment, fixture-local profile/temp paths, effective Git Bash PATH tripwires
and native Python subprocess barriers. Package-manager attempts fail the originating test.
Controls remove the barrier only against a controlled fake outer command. Python writes are
constrained to fixtures; this is not an OS sandbox for arbitrary native subprocesses.

The existing `tests/conftest.py` and guard corpus are deliberately untouched during Plan 2
certification. **Bare pytest is not protected by this opt-in launcher.** Claude must wire the
barrier into the canonical test entry point after certification. Known Node auto-installation
in setup remains deferred and is intercepted rather than executed in contained tests.
