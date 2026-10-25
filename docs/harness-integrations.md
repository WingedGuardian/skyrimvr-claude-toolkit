# Integration safety and reproducible dependencies

## Spriggit publication

`tools/spriggit-cli.sh` preserves input/output basenames (the Spriggit ModKey) and runs
the serializer in a unique shallow scratch directory. Override its scratch parent with
`SPRIGGIT_WORK_ROOT`; Python 3 is required for publication. No prerequisites are installed.

Publication rejects missing/empty files, linked artifacts and directory results without
nonempty YAML. This is structural artifact validation, **not** proof of valid plugin content.
Input/output overlap and linked destinations are rejected. Output is copied to a unique
staging directory beside the destination, validated, then renamed into place. An existing
destination's identity and bytes are captured before staging and rechecked before publication.
It is renamed aside first and restored if publication fails. All publication and rollback
renames refuse an existing destination, including an empty directory. Unsupported exclusive
rename primitives/filesystems refuse; there is no overwriting fallback. Old YAML never overlays
the new result. A process crash between renames can leave `.spriggit-previous-*`; preserve it
and inspect before recovery. Concurrent publication to the same destination is rejected.
The previous output is retained after success and its path is printed. This also preserves
bytes written through an old handle. If a new writer prevents rollback, both its destination
and the previous output remain and the failure names the recovery path. Stop other writers
while publishing: the existing-artifact checks are not a filesystem compare-and-swap.
Operation-owned staging cleanup failures are warnings naming retained artifacts.
After a crash, inspect a retained `.spriggit-lock-*` before removing it; no stale lock is stolen.
The held-lock error names this destination's debris (`.spriggit-previous-<key>-*` holds the OLD
output -- for a deserialize, the previous plugin).

A destination directory is replaced only if everything in it looks like Spriggit output (what a
real serialize writes: `spriggit-meta.json`, `RecordData.yaml` and record-group folders of
`.yaml`/`.json` files). Anything else -- `.git`, notes, a README -- makes the wrapper refuse
before serializing, because the pre-repair wrapper merged and never deleted those. A destination
whose parent is not writable (the container's `/workspace`) is also refused up front.
The wrapper skips the Microsoft Store `python` alias (`...\WindowsApps\python.exe`) and says so
when it is the only Python; inside the container it runs the restored local tool through
`dotnet tool run spriggit`.

## Container isolation

The workspace and game/profile/overwrite bind mounts are read-only. This includes
`/workspace/Data`, which was previously a writable alias of a read-only game mount.
Node modules are installed to the container-local `/opt/skyrim-node` (`NODE_PATH` points
`require()` there); no mount is nested under the read-only workspace, because Docker cannot
create such a mountpoint on a fresh checkout. Dependency caches and installed Python/.NET tools
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

**Cold start (repaired 2026-10-02):** the original nested `node_modules` volume could not be
mounted on a fresh checkout (Docker cannot create the mountpoint through the read-only bind).
`tests/test_harness_controls.py::test_live_cold_start_mounts_without_node_modules` replays the
helper's real mount arguments against Docker on a workspace that never had `node_modules/`, and
`tests/test_dependency_locks.py` refuses any mount target under `workspaceFolder`.
Failed `docker run` can still leave a newly created, stopped container and anonymous volume;
the helper deliberately avoids removing a container unless startup succeeded.

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

`requirements.lock` freezes direct/transitive versions and every non-yanked wheel hash, so it installs on
Python 3.10 through 3.13 (Windows x64, Linux x64 and arm64). Provision in an isolated virtual environment or container:

```text
python -m pip install --only-binary=:all: --require-hashes -r requirements.lock
```

This lock freezes package resolution, not the complete base image, SDK or interpreter. It holds only what the
suite and CI run (pytest, PyYAML, ruff and their dependencies). There is no Windows-only lock: pywin32 was never
imported by anything and was removed with the other unused pins (pydantic, cattrs, mypy, pytest-cov).

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
The generator rejects withdrawn
wheels and performs no installation. The reports and PyPI metadata define the frozen result;
subsequent installs do not resolve fresh versions. Input checksums normalize line endings.

## Test-only package-manager barriers

Run `python -B tests/run_harness_tests.py` (or supply pytest targets/options). It constructs
a scrubbed environment, fixture-local profile/temp paths, effective Git Bash PATH tripwires
and native Python subprocess barriers. Package-manager attempts fail the originating test.
Controls remove the barrier only against a controlled fake outer command. Python writes are
constrained to fixtures; this is not an OS sandbox for arbitrary native subprocesses.

The launcher is the canonical test entry point. `tests/conftest.py` exposes
`harness_active()`, and with `TOOLKIT_REQUIRE_HARNESS=1` a pytest session that does not have the
barriers installed errors at `pytest_configure` instead of skipping the barrier-dependent
modules and going green. CI runs the whole suite as
`python -B tests/run_harness_tests.py tests/ -v --durations=10 -rfEs` with that variable set (the
launcher forwards it), then `scripts/check_could_not_check.py` fails the job on any
`COULD NOT CHECK` skip in the output. Linux allows none (it has a daemon and the sentinel
image); Windows allows only the Docker daemon and the POSIX-only `dir_fd` removal control.
pytest's basetemp is placed in system temp (`RUNNER_TEMP`), outside the checkout, because repo
tools refuse an `--out-dir` inside it. `setup.sh` no longer auto-installs Node; it prints how
to install it, and a test with a tripwire `winget` on PATH proves it never runs one.

## MO2 helper precedence

With Mod Organizer 2, the profile's `plugins.txt` is the real load order and the Documents/LOCALAPPDATA
copies are stale. The precedence above (explicit `PLUGINS_TXT`, then `SKYRIM_LOADORDER_DIR`, then setup's
configuration file, then LOCALAPPDATA detection) is what makes the MO2 profile win:
`tests/test_harness_integrations.py::test_mo2_config_and_explicit_override` shows a configured MO2 profile
beating a rival LOCALAPPDATA list, and each explicit override beating that. The guard side of the same
configuration is covered by `tests/test_setup_paths.py::test_setup_writes_the_mo2_roots_the_guard_reads`
(setup records the MO2 `mods` and `overwrite` roots, read back as data the way the guard reads them).
This section only records the behavior those tests pin.
