# Codex integration repair handoff — 2026-10-02

**Claude takeover (2026-10-02):** cold start and review findings repaired; see the last section.
**Subsequent live verification:** [Docker and native Spriggit report](codex-live-verification.md)
closes the native round-trip and Docker sentinel gaps. Native Spriggit passed byte-identically;
the focused suite now passes all 23 tests without skips. Real Docker cold startup exposed a
missing `node_modules` mountpoint defect, which remains a repair handoff. Historical results
below describe the earlier verification and are preserved rather than relabeled.

## Ownership and scope

Worktree: `C:/Users/Moona/Projects/skyrim-toolkit-code71271`.
Branch: `codex/harness-repairs-fd71271`, based on master commit
`fd71271663342e9754d6176754c9543d54be1ba8`. Claude owns the merge after Plan 2's final
review. Nothing was committed to master, merged, pushed or deployed.

Implemented only the authorized Spriggit, Docker, dependency lock, MO2 integration and
test-file package-manager barriers. Every guard engine, generated adapter, agent source,
setup, conftest, guard corpus, generator and canary file remains unchanged from the base.

Reproduced finding confidence: 99%. Repair acceptance is based on the tests below, not that
percentage. No host runtime was installed, upgraded, removed or repaired. Project Node
dependencies and Python dependencies were provisioned only inside this worktree; Python
uses a disposable virtual environment. Docker Desktop was not started or repaired.

## Commits and implementation

- `3a4cc7b`: unique Spriggit scratch, structural output validation, staged replacement and
  rollback; read-only workspace/game/profile mounts, fail-fast container dependencies;
  MO2 configuration selection; contained test launcher and package-manager controls.
- `212e453`: Node/Python locks, locked CI provisioning, dependency refresh generator and docs.
- `3b337a2`: independent review fixes for bare-pytest control safety, publication cleanup
  semantics, supported-target lock report merging and their regression tests.
- `27a353b`: helper-mount/startup-failure controls, ownership-aware Docker cleanup and
  ordinary-CI read-only gates. The final documentation commit adds this record.

The one direct dependency change is `cattrs 23.2.0 -> 23.2.1`: PyPI marks 23.2.0 withdrawn
for a Python 3.11 import bug, and upstream's patch release addresses that import. The lock
generator rejects withdrawn wheels. Transitive versions/hashes are committed, not re-resolved
by ordinary installation. Current locks support Python 3.10/3.11; they do not certify every
future Python version or freeze the image/SDK/interpreter itself.

Spriggit publication has a rename commit boundary. A pre-commit failure restores the old
destination; post-commit cleanup errors warn and name retained artifacts. Directory swapping
has a short visibility gap between renames and is not crash-atomic. A crash can leave a
previous-output directory and a lock; inspect these manually rather than stealing a stale lock.
Validation establishes nonempty structural output, not semantic plugin validity.

The workspace is intentionally read-only in both container entry points, closing the second
`/workspace/Data` route. Node artifacts use a container volume; provisioning runs as root
inside the container. Host workspace edits require the existing deliberate Windows workflow.
Cleanup only removes a container after this invocation successfully started it. Failed startup
propagates its exit code without deleting a possibly pre-existing container with that name.

## Review and verification

Independent read-only whole-branch review found three actionable defects, all addressed:
uncontained bare-pytest controls, false failure after committed publication, and incomplete
cross-target lock refresh. Named cleanup and lock-refresh tests were observed failing before
their fixes and passing afterward. Original integration tests reproduced six baseline failures
before implementation. The first legacy-wrapper baseline used its default temporary scratch
location; fixture remapping was corrected before subsequent probes. No real serializer or
host package manager was executed by those probes.

Recorded local evidence is git-ignored under `.pytest_cache/`:

- `final-focused.log`: **22 passed, 1 skipped** after review, including helper-mount and
  startup-failure controls. Skip reason: **COULD NOT CHECK: Docker daemon unavailable**.
- `node-all-tests.log`: **21 passed, 0 failures/skips**, explicit committed Node test files.
- `bare-readonly-gates.log`: **4 passed** under ordinary pytest with third-party plugin
  autoload/cache/bytecode disabled; no behavioral fixture or installation invoked.
- `locks/venv-install.log`: hash-required common and Windows locks installed into a disposable
  Python 3.10 venv. `pip check` reports no broken requirements; cattrs/pydantic/YAML imports pass.
- `locks/linux311-dry-run.log`: hash-required Linux CPython 3.11 wheels resolve successfully.
  This is a cross-target dry-run, not real Linux execution.
- `review-red.log`, `lock-review-red.log`: red controls for the review fixes.
- `docker-cleanup-red.log`: failed-startup control failed before ownership-aware cleanup;
  the final focused run passes it. Final read-only review found no important remaining issue.
- `schema/validation.log`, `schema/control.json`: official Dev Container base schema accepts
  the configuration; restoring the old object `readonly` property makes validation fail.
- Full reviewed suite result is recorded in the completion section below.

The initial broad suite was intentionally stopped when review changes superseded it; it is
not reported as a pass. The reviewed full suite ran fresh. Fake serializers/Docker commands
exercise real wrapper entry points but do not certify native Spriggit or Docker enforcement.
The broad run collected tests before the last helper-cleanup amendment and read-only test
reorganization. The final focused run repeated all affected tests after those changes. This
combined local evidence is not exact merged-revision release certification.

## Required Claude follow-up after Plan 2 certification

1. **Canonical test isolation.** Merge `tests/run_harness_tests.py`/test barriers into the
   canonical fixtures and CI execution. `tests/conftest.py` and behavioral CI entry-point
   wiring were left untouched by this scope. Bare pytest safely skips the new barrier-dependent
   tests; it still does not isolate the old suite. These skips are NOT passes. Remove ordinary
   setup's Node auto-install branch in the approved later plan.
   The independent read-only lock-drift and mount-schema tests still run under bare pytest.
2. **Live container verification.** Docker executable exists here but its Linux daemon is
   unavailable. Run the disposable sentinel test with an existing `busybox:1.37` image, then
   cold startup through Docker helper and Dev Containers. Verify the node_modules volume,
   hash-locked provisioning and read-only direct/workspace aliases. Do not test against real
   game/profile assets. Invalid/unavailable checks must retain their distinct status.
3. **Native Spriggit smoke.** Run a serialize/deserialize round trip with a disposable valid
   plugin, preserving ModKey and normal ESP verification. Empty output, stale destination,
   output failure and interruption controls are required. ESP verification's false PASS on
   absent inventory is a separate audit finding, outside this narrowed implementation scope.
4. **Agent/configuration integration.** Plans 3/4/5 should document the MO2 helper precedence
   and carry it into the common path interface without losing `PLUGINS_TXT`. The helper reads
   root or legacy paths files as data; no guard-engine parser was changed. Generated instructions
   and skills remain for Claude to update from canonical sources.
5. **Remaining audit repairs.** All of section 2 (policy/verdict changes, PowerShell/other route
   coverage, fallback chain, health/telemetry), backup/snapshot behavior and setup-skill work
   remain deferred. Skip the already-completed ampersand and missing-Python fixes. Nothing here
   certifies those repairs or changes Plan 2's old/new equivalence contract.
6. **Final release verification.** Run exact merged revision on Windows and POSIX, regenerate
   only after certification, check cold archive contents and preserve personal configuration,
   then perform real Claude/Codex scratch-session E2E and established deployment parity.
   Claude alone merges/pushes/releases. Copy durable incident lessons into appropriate local
   knowledge/blind-spot records without overwriting user notes.

## Completion verification

Commands and final results:

```text
python -B tests/run_harness_tests.py tests/ -q -rs
1042 passed, 1 skipped in 3803.79s (1:03:23)

python -B tests/run_harness_tests.py tests/test_harness_controls.py tests/test_harness_integrations.py tests/test_dependency_locks.py -q -rs
22 passed, 1 skipped in 52.04s

python -B -m pytest tests/test_dependency_locks.py -q -p no:cacheprovider
4 passed in 0.02s
```

The ordinary-pytest command used disabled third-party plugin autoload and bytecode. Both
behavioral-run skips explicitly report Docker daemon unavailable; no live container
enforcement result is claimed. Full evidence: `.pytest_cache/full-reviewed-suite.log`.
All 81 existing mutation probes passed in disposable copies. The prior superseded broad
run is excluded from these results.

Confidence is high in fixture-verified integration behavior and local dependency
reproducibility. Native Spriggit semantics, live Docker enforcement, actual POSIX execution
and exact merged/deployed E2E remain unverified here and are assigned above. No claim of
complete host-test isolation applies until Claude wires the barriers into canonical fixtures.

Final scope check: no diff from `fd71271` under `tools/skyguard`, `agent`, `.claude`,
`tests/conftest.py`, `tests/guard_corpus`, `scripts/gen_agent_trees.py`, `setup.sh` or
`tools/hook-canary.sh`. Final branch receives only the commits listed above plus this
documentation/whitespace completion commit; Claude retains merge and release ownership.

## Claude takeover 2026-10-02

Claude took over the branch to fix the review findings. Commits `88ac8e8..cc3468b` on
`codex/harness-repairs-fd71271`; nothing merged or pushed. Do-not-touch paths still show zero
diff from `fd71271`. Each fix has a test that was observed failing first.

| # | Finding | Status | Commit |
|---|---|---|---|
| 1 | Fresh checkout cannot start Docker (nested `node_modules` volume under read-only bind) | **Fixed.** `npm ci` runs in container-local `/opt/skyrim-node`, which still checks lockfile integrity; `NODE_PATH` set in the Dockerfile and the helper; nested volume removed from both entry points | `88ac8e8` |
| 2 | Publication replaced the whole destination; old wrapper deleted nothing | **Fixed (refuse).** Destination directories may contain only what a real serialize writes (`spriggit-meta.json`, `*.yaml`/`*.json`, record-group folders of those). Anything else, e.g. `.git`, is refused with a one-line message before serializing and again under the lock | `b521cb1` |
| 3 | Harness modules skipped in CI | **Fixed.** The behavior job runs `run_harness_tests.py` on both legs, and the Linux leg pre-pulls the digest-pinned busybox. The bare-skip test moved to `test_dependency_locks.py`. **Also found:** the branch's `ci.yml` was invalid YAML (`--only-binary=:all: ` in a plain scalar), so no CI job could run. Repaired, and a parse gate was added | `4826a01` |
| 4 | Read-only workspace breaks Spriggit output and the harness | **Fixed + documented.** Harness evidence falls back to container temp. Spriggit refuses an unwritable destination before serializing. The wrapper falls back to `dotnet tool run spriggit` (measured: bare `spriggit` was never on the container PATH, exit 127). README and container-vs-windows have a table of write locations | `b521cb1`, `f46fddc` |
| 5 | Explicit `base` must beat configured load order | **Fixed.** Measured with `SKYRIM_LOADORDER_DIR` set: the previous revision fails "ambiguous choice WARNS" and this revision passes 12/12. The three plugins-txt mutation probes pass | `cc74bbd` |
| 6 | Microsoft Store python stub | **Fixed.** `type -aP` candidates under `\WindowsApps\` are skipped. If the stub is the only Python, the wrapper gives a clear error | `b521cb1` |
| 7 | Crash debris not named | **Fixed.** Debris names carry the destination key. A held lock lists `.spriggit-previous-<key>-*` (the OLD output) and `.spriggit-stage-<key>-*` | `b521cb1` |

Also fixed: on POSIX the harness barrier judged `shutil.rmtree`'s dir_fd-relative names
against the cwd, so `test_publication_failure_rolls_back` failed on Linux. That failure would
have hit the new ubuntu CI step. Now fixed (`35c9538`, `cc3468b`), with a refusal control that
was red on the previous barrier in the Linux container. The helper now allocates a TTY only
when stdin has one.

### Evidence

- **Real cold start:** fresh `git clone` of the branch into the session scratchpad, with no
  `node_modules/` and nothing pre-created. The real `devshell-docker.sh` was run against disposable
  sentinel mounts, and only its image tag was changed (`skyrim-toolkit-claude-88ac8e8`). The last
  run used `--rebuild` at `f46fddc`. Python lock, `npm ci` and the dotnet tool restore all ran;
  the shell opened. `require()` of ajv/fast-xml-parser/zod worked through
  `/opt/skyrim-node/node_modules`, and `/workspace/node_modules` was absent. Writes to
  `/workspace` and to the mods/profile/overwrite mounts were refused. `pip check` was clean.
  An in-container Spriggit serialize to deserialize round trip of a disposable
  `BoundDaggerFix.esp` was byte-identical (`6ce67b41...58da` both sides). A `.git` in the
  destination was refused and kept, and a `/workspace` destination was refused up front.
  Container and volume were removed on exit, and the sentinel hashes were unchanged.
- **Red control:** the same replay test hit the exact `mkdirat ... read-only file system` error
  against the pre-fix configuration.
- **Focused suite, Windows:** 33 passed, 1 skipped. The skip is the POSIX-only dir_fd control.
  Both live-Docker tests ran.
- **Focused suite, Linux (inside the container, fresh clone at `cc3468b`):** 32 passed,
  2 skipped (no Docker inside the container).
- **Node:** 22/22. **Bare pytest gates:** 7 passed.

### What remains

- **Dev Containers CLI:** not installed on this machine, so the `devcontainer up` lifecycle was
  **not run**. `devcontainer.json` is covered only by the static gate and by sharing the Dockerfile.
- **Failed-start cleanup:** a failed `docker run` can still leave a created container. The
  `docker create` + `docker start` ownership design (live-verification item 2) is not done.
- **CI not executed:** I checked the YAML parse and the step contents, and ran the same commands
  locally. Neither the ubuntu leg's live-Docker run nor the Windows runner was observed.
- **"Spriggit-produced" is measured on one plugin** (WEAP records, Spriggit.Yaml). A user's own
  top-level `*.yaml`/`*.json` would count as Spriggit output and be replaced. Other packages or
  record layouts with non-yaml/json files would be refused, which fails safe.
- **`NODE_PATH`** serves CommonJS `require()` only (documented). No tracked code imports these
  dependencies today.
- Canonical conftest wiring, deferred guard work and release certification are unchanged from
  the list above. The full suite was not run here; the controller runs it.
- Left running or retained: Docker Desktop (started for this work); local image
  `skyrim-toolkit-claude-88ac8e8`; scratchpad clones.
