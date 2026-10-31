# Development CI

CI uses the [core-owned policy](https://github.com/WingedGuardian/modding-agent-harness/blob/50a993ba807d4c1e513cedef19b709fa2cf91ce0/CONTRIBUTING.md),
pinned independently of the runtime harness revision.

Develop in a draft PR. Draft updates run the existing shell, Python, Node,
configuration and setup smoke checks. The merge qualification check deliberately
remains blocked while full testing is deferred; green fast checks are not full
qualification.

Mark the PR ready to request the complete existing qualification. Further code
updates require fresh qualification. Default-branch pushes and manual dispatch
retain full testing; feature branches use the PR event. Do not manually dispatch
a second copy of the same PR run.

Each repository has an independent full-run queue. Started requests are retained.
Skyrim runs the .NET/native build stage, then release verification, then the
complete behavioral/mutation matrix with every leg at once: the run is as long as
its slowest job, not its queue. The mutation gate runs as shards (four on Windows,
two on Linux; `SKYGUARD_MUTATION_SHARD` in `tests/conftest.py` deselects the
other slices) and a totals job checks the shards' logs as one gate
(`scripts/check_ci_leg.py` with several logs). Fast draft checks remain
independent of this queue.

Before any push, run the local pre-push gate: `python scripts/prepush-gate.py`
(5-10 minutes: collection against the recorded floors, the generated-file and
guard-hash drift checks, the deterministic sentinels -- mutation anchors and
inventory, corpus rows, rule codes, timing shapes, pin audits, the CI tripwire
and workflow tests -- and the tests for the files the push changes). Of ten red
full runs in the last week of October 2026, seven failed on something this gate
catches at the desk in minutes. The maintainers' clones install it as a git
`pre-push` hook; `--no-verify` is not a workflow.

Retry failed jobs on the same revision while retaining original failures and
passing legs. Reports bind the source head and tested merge candidate and show
queue time separately from execution time. Missing or skipped required jobs
cannot qualify a ready PR. Behavioral test matrices and mutation thresholds are
preserved; mutations run once per OS on Python 3.10, with their named witnesses.
CI does not certify native game or host-hook behavior.
