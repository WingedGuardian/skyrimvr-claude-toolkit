> Evidence from the toolkit's development. Not instructions for your agent.

# Plan 7: what an adapter costs per tool call

Measured 2026-10-05 in the `plan7` worktree. Tier: **MEASURED** (I ran it).

## Method

- The json-deny adapter (template + `tools/adapting/examples/json_deny.slots.py`) staged into a throwaway install
  exactly as `tests/test_toy_agent.py::staged` does.
- 40 runs each of: (a) the whole adapter fed the benign native write payload, (b) the engine alone,
  `python tools/skyguard/run.py neutral pre-tool` fed the equivalent benign neutral request (no `--telemetry`).
  One extra warm-up run of the adapter was made and not counted.
- Each run is a `subprocess.run` wrapped in `time.perf_counter`, so it includes process start and pipe setup.
- Python 3.10.10, Windows-10-10.0.26200 (Windows 11 Home), 40 runs per series, a decision of `allow` every time.

## adapter median

**112 ms** per call for the whole adapter (min 106 ms, max 815 ms). That is two interpreter starts: the adapter,
then the engine it launches.

## adapter p95

**362 ms** (the single slowest run was 815 ms).

## engine alone

median **73 ms**, p95 **328 ms** (min 72 ms, max 523 ms).

## Caveats

- One machine, one Python version, 40 runs: the median is stable (min 106 against median 112), the p95 is
  not, because with 40 samples it is the 38th value and one slow outlier moves it a lot.
- Other branches' test runs and background tools may have been loading the machine during the measurement, and
  antivirus scanning of a freshly started interpreter on Windows is a large part of the tail.
- The adapter series includes its heartbeat file writes (`--telemetry`); the engine-only series does not.
- This is not a CI assertion, and a rule-heavy request (a long shell command) costs more than the benign write used here.
