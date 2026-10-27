# Development CI

CI uses the [core-owned policy](https://github.com/WingedGuardian/modding-agent-harness/blob/38bf2cb8da9d4add2fa3c71341d641fafd265368/CONTRIBUTING.md),
pinned independently of the runtime harness revision.

Develop in a draft PR. Draft updates run the existing shell, Python, Node,
configuration and setup smoke checks. The merge qualification check deliberately
remains blocked while full testing is deferred; green fast checks are not full
qualification.

Mark the PR ready to request the complete existing qualification. Further code
updates require fresh qualification. Default-branch pushes and manual dispatch
retain full testing; feature branches use the PR event. Do not manually dispatch
a second copy of the same PR run.

Each repository has an independent full-run queue. Started requests are retained;
at most four heavy jobs execute concurrently. Skyrim runs the .NET/native build
stage, then release verification, then the complete behavioral/mutation matrix.
Fast draft checks remain independent of this queue.

Retry failed jobs on the same revision while retaining original failures and
passing legs. Reports bind the source head and tested merge candidate and show
queue time separately from execution time. Missing or skipped required jobs
cannot qualify a ready PR. Test matrices, mutation witnesses and thresholds are
preserved. CI does not certify native game or host-hook behavior.
