# Live Docker and Spriggit verification — 2026-10-02

## Scope and conclusion

Verified repair code at `06e2d9c5ccd0248dc5b61af0846b8abd992e02c2` on
`codex/harness-repairs-fd71271`, exclusively using disposable worktree fixtures.
The Windows native Spriggit round trip **passed**. Real Docker mount enforcement,
dependency provisioning, dependency-failure propagation and plugin inspection **passed**.
**Fresh-checkout Docker startup failed**: the nested `node_modules` mountpoint is absent.
This is a toolkit defect, not a Docker availability failure or operator error.

No production code changed during these checks. Setup, guard, agent, snapshot and telemetry
paths remain untouched. No merge, push or deployment occurred. Confidence is high for the
measured outcomes below; fresh-checkout startup is explicitly not accepted.

## Environment and retained evidence

Docker Desktop was installed but stopped. `docker desktop start` started it without changing
settings or updating software; Docker server reports `29.2.0`. It remains running. The existing
Spriggit CLI reports `0.40.0`, with commit `d6645a117b05887f270887155c71a4276b84507d`.
No working jq, Node or host .NET installation was modified.

`docker pull busybox:1.37` obtained the sentinel image:
`sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e`.
The pytest sentinel itself still uses `--pull=never --network=none`.

Built the unchanged Dockerfile with an independently named local tag:

```text
docker build --file .devcontainer/Dockerfile --tag skyrim-toolkit-codex-06e2d9c-live .
```

Build succeeded, using cached prerequisite layers; this is not a cache-free image rebuild.
All new tool provisioning occurred inside disposable containers. Both the BusyBox image and
the independently tagged toolkit image are retained locally for follow-up, not published.
Transient containers and their anonymous volumes were removed. Fixtures/logs remain under
git-ignored `.pytest_cache/`.

## Native Spriggit round trip

Used a read-only source copy of `BoundDaggerFix.esp` (1,070 bytes). The live plugin was never
an output target. All serialize, deserialize, publication, reference snapshot and comparison
operations used `.pytest_cache/native-spriggit-mfq5779a/`.

The workflow invoked the real `tools/spriggit-cli.sh` with the real installed serializer:

```text
serialize --InputPath <fixture>/in/BoundDaggerFix.esp --OutputPath <fixture>/yaml-before
          --GameRelease SkyrimSE --PackageName Spriggit.Yaml --PackageVersion 0.40.0 -u -c
deserialize --InputPath <fixture>/yaml-before --OutputPath <fixture>/rebuilt/BoundDaggerFix.esp
serialize --InputPath <fixture>/rebuilt/BoundDaggerFix.esp --OutputPath <fixture>/yaml-after
          --GameRelease SkyrimSE --PackageName Spriggit.Yaml --PackageVersion 0.40.0 -u -c
```

Measured results:

- Two actual WEAP record YAML files, rather than a header-only fixture.
- Original and rebuilt SHA-256 both
  `6ce67b410944e775683c3114b2f72a691fe8a15b15505b4b85518c6fe11158da`.
- Complete serialized artifact trees identical by relative filename and file hash.
- Metadata ModKey preserved as `BoundDaggerFix.esp`.
- `esp-verify-wrapper.sh snapshot` and `verify`: 27 nonempty reference entries, including
  actual `Skyrim.esm` references; no changed masters or dropped references. An empty inventory
  would have failed the smoke script instead of being treated as proof.
- A stale YAML sentinel present before publication disappeared from the successful result.
- Native serialization of deliberately invalid fixture input failed nonzero and left the
  existing published tree unchanged.
- Rehashing the live source confirmed it was unchanged.

Evidence: `.pytest_cache/native-spriggit-live.log` and the fixture's `result.json`, serializer
logs and reference logs. `.pytest_cache/run-native-spriggit.py` preserves the exact local
orchestration. Scratch/temp/profile/NuGet environment paths were redirected to this fixture.
Spriggit additionally created a relative `NuGet/` cache in the owned worktree; that cache was
moved intact into the fixture as `nuget-cwd`, not deleted or committed.

This certifies this nonlocalized plugin and these wrapper contracts. It is not an in-game
test or a guarantee for every plugin type; the documented Linux localized-string limitation
remains unchanged.

## Real Docker tests and controls

1. `tests/test_harness_controls.py::test_live_container_readonly_sentinels`:
   **1 passed**, using real Docker and BusyBox against disposable files.
2. Fresh disposable toolkit workspace contained the real helper, dependency script,
   configuration, manifest, package files and locks. Mount placeholders referenced only
   fixture Data/profile/overwrite directories. Only the fixture helper's image tag was
   substituted to avoid overwriting anyone else's `skyrim-toolkit` image.
3. **Cold startup failed** before provisioning with:
   `error mounting ... to /workspace/node_modules ... mkdirat ... read-only file system`.
   A newly created stopped container and volume remained because startup never succeeded.
   Inspection confirmed all binds referenced the owned fixture; that exact container ID
   and its volume were removed, without deletion by a guessed name.
4. Created an empty `node_modules/` **in the fixture only**, then reran the real helper.
   It installed the hash-required Python lock on Linux/Python 3.11, ran real `npm ci`,
   restored the pinned Spriggit tool and opened its shell successfully.
5. Actual `docker inspect` mount evidence showed workspace, mods, profile and overwrite
   `RW=false`; only the anonymous Node volume had `RW=true`.
6. Real writes to `/workspace/package.json`, `/workspace/Data/sentinel`,
   `/skyrim/mods/sentinel`, `/skyrim/profile/sentinel` and `/skyrim/overwrite/sentinel`
   were refused. Fixture sentinels remained unchanged. A write inside the anonymous Node
   volume succeeded; the host's placeholder directory remained empty.
7. Container `pip check`: no broken requirements. Imports of cattrs, pydantic and YAML passed.
   Imports of all declared Node dependencies (ajv, fast-xml-parser and zod) passed.
   An initial probe mistakenly requested undeclared fs-extra and failed; that probe was
   corrected and is not counted as a toolkit defect or successful check.
8. Container `dotnet tool run spriggit -- --version` started the real 0.40.0 tool.
   The actual `examples/inspect-esp.py` read the fixture plugin from the read-only mods
   mount and reported both WEAP records successfully.
9. Removing `readonly` in a separate BusyBox control allowed a fixture sentinel write;
   the guard-style test returned its expected failure code **42**. This demonstrates
   that the refusal check goes red if a mount becomes writable.
10. A second disposable workspace had a deliberately invalid Node lock. Real container
    `npm ci` failed; the actual helper propagated exit **1**, did not open its shell or
    print Done, and cleaned up its owned container/volume.
11. Exiting the successful helper removed its exact container and anonymous volume.

Evidence: `.pytest_cache/live-docker-sentinel.log`, `docker-live-mounts.json`,
`docker-live-provisioning.log`, `docker-live-contract.log`, `docker-node-imports.log`,
`docker-real-inspection.log`, `docker-dependency-failure.log` and cleanup logs.
The first contract log includes the corrected undeclared-module probe; use the separate
Node import log for its final result. Full disposable workspace is `live-docker-cold/`;
the actual-failure fixture is `live-docker-failure/`.

## Required repair and remaining limits

1. Repair cold startup without relaxing any read-only bind. Prefer a container-local Node
   dependency prefix/volume outside the read-only workspace, with intentional module
   resolution, or another measured design that does not rely on a preexisting host directory.
   Requiring setup to create the mountpoint would touch frozen setup scope: hand off rather
   than editing it during certification. Apply the solution to both helper and Dev Container
   registrations, and add a real fresh-checkout regression/control.
2. Preserve safe ownership while cleaning partial startup. A `docker create` then `docker start`
   sequence can establish an owned ID before startup, allowing failed-start cleanup without
   risking removal of a name-colliding container owned by another session. Test collision and
   runtime-start failure explicitly; do not restore unconditional name-based cleanup.
3. The official Dev Containers CLI/editor lifecycle was unavailable and not run. JSON schema
   checks and real helper/mount checks do not certify that lifecycle. Verify it after repair.
4. Exact merged-release Windows/POSIX/cold-archive/deployed-session certification, canonical
   test-barrier wiring, generated instructions and all earlier deferred guard work remain
   Claude-owned. This verification does not alter Plan 2's equivalence contract.

## Final focused verification

```text
python -B tests/run_harness_tests.py tests/test_harness_controls.py tests/test_harness_integrations.py tests/test_dependency_locks.py -q -rs
23 passed in 127.24s (0:02:07)
```

There were no skips. Evidence: `.pytest_cache/focused-with-live-docker.log`.
This closes the previous Docker sentinel availability gap, but the separate cold-start
failure above remains a finding: the existing suite does not yet cover that mountpoint.
The previously recorded full suite is not relabeled as live/cold certification. Only
documentation changed after this verification; the final branch remains unmerged/unpushed.
