#!/usr/bin/env python3
"""Per-leg CI tripwires (Plan 8 T10.4): a leg that silently runs nothing, or skips its way to green, goes red.

    check_ci_leg.py collect <collect-only.log> --leg Linux|Windows [--spec .github/ci-leg.json]
    check_ci_leg.py log     <pytest -v -rfEs log> --leg Linux|Windows [--part NAME] [--spec .github/ci-leg.json]

`collect` runs FIRST on each leg, from `pytest --collect-only -q` output: fewer tests than the recorded floor
means a module stopped being collected (or the leg is looking at the wrong tree) -- before an hour of suite.
`log` reads the suite's own output afterwards:
  * the summary line exists and the run collected at least the floor;
  * skips at most the leg's recorded skip ceiling, xfails at most the xfail ceiling (a green run that skipped
    its way there is a claim the suite did not earn);
  * every REQUIRED module (the harness-contained ones, the hook-deadline tests, the freeze gate, the release
    machinery; per leg, the platform's own) has at least one PASSED test -- a module skipped wholesale
    otherwise hides inside a total;
  * the opt-in load test (FORBIDDEN_PASSING) has NOT run: it burns CPU on purpose and a shared runner gives
    no stable CPU to measure against, so it runs locally only (T14b).

`--part NAME` (log mode) checks one part of a leg that CI splits into jobs: spec["parts"][NAME] replaces the
top-level floor, ceilings and module lists it names. The mutation gate runs as its own part because it is most of
a Windows leg's time (146-217 of 270 minutes in CI run 39210090486); the top-level numbers are the rest of the suite.

Numbers live in .github/ci-leg.json so a change to them is a reviewed diff. Exit 0 ok / 1 a tripwire fired /
2 nothing could be checked (no summary, bad usage).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SPEC = Path(__file__).resolve().parent.parent / ".github" / "ci-leg.json"


def _count(lines, word):
    return sum(int(m.group(1)) for line in lines if re.match(r"^=+ .* in [0-9.]+s", line)
               for m in [re.search(r"(\d+) " + word + r"\b", line)] if m)


def check_collect(lines, spec, leg):
    # Node ids (`path::name[params]`) are never the summary, and a param id can carry summary-shaped text
    # ("12 tests collected, 1 error ...": test_ci_leg's own ids do), so only non-node-id lines are read.
    lines = [l for l in lines if "::" not in l]
    m = next((re.search(r"(\d+) tests? collected", l) for l in lines if re.search(r"\d+ tests? collected", l)), None)
    if m is None:
        return 2, ["COULD NOT CHECK: no 'N tests collected' line"]
    n, floor = int(m.group(1)), spec["collected_floor"]
    if any(re.search(r"\d+ errors?\b", l) for l in lines if "collected" in l or l.startswith("=")):
        return 1, ["collection reported errors: a module did not import"]
    if n < floor:
        return 1, ["{} leg collected {} tests, floor {}: a module stopped being collected".format(leg, n, floor)]
    return 0, ["{} leg collected {} tests (floor {})".format(leg, n, floor)]


def check_log(lines, spec, leg):
    summary = [l for l in lines if re.match(r"^=+ .* in [0-9.]+s", l)]
    if not summary:
        return 2, ["COULD NOT CHECK: no pytest summary line"]
    problems, notes = [], []
    # Real `pytest -v` output piped to a file reads `collecting ... collected N items` on one line (MEASURED
    # 2026-10-06). The match is anchored at the line start, so a node id carrying the same words in its
    # parameters never counts.
    collected = next((int(m.group(1)) for l in lines
                      for m in [re.match(r"^(?:collecting \.\.\. )?collected (\d+) items?\b", l)] if m), None)
    if collected is None:
        problems.append("no 'collected N items' line: was the suite run with -v?")
    elif collected < spec["collected_floor"]:
        problems.append("collected {} < floor {}".format(collected, spec["collected_floor"]))
    else:
        notes.append("collected {} (floor {})".format(collected, spec["collected_floor"]))
    skipped, xfailed = _count(summary, "skipped"), _count(summary, "xfailed")
    ceiling = spec["skip_ceiling"][leg]
    if skipped > ceiling:
        problems.append("{} skipped > the {} leg's ceiling {}: something stopped running".format(skipped, leg, ceiling))
    else:
        notes.append("{} skipped (ceiling {})".format(skipped, ceiling))
    if xfailed > spec["xfail_ceiling"]:
        problems.append("{} xfailed > ceiling {}".format(xfailed, spec["xfail_ceiling"]))
    required = list(spec["required_modules"]["all"]) + list(spec["required_modules"].get(leg, []))
    def passed(line, module):
        return line.startswith("tests/" + module + "::") and re.search(r"\sPASSED(?:\s+\[\s*\d+%\])?\s*$", line)
    for mod in required:
        if not any(passed(l, mod) for l in lines):
            problems.append("required module {} has no PASSED test on the {} leg".format(mod, leg))
    for mod in spec["forbidden_passing"]:
        if any(passed(l, mod) for l in lines):
            problems.append("{} RAN in CI; it is opt-in and local only".format(mod))
    return (1 if problems else 0), (problems or notes)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 4 or argv[0] not in ("collect", "log") or "--leg" not in argv:
        sys.stderr.write(__doc__.split("\n\n")[1] + "\n")
        return 2
    mode, log = argv[0], argv[1]
    leg = argv[argv.index("--leg") + 1]
    spec_path = Path(argv[argv.index("--spec") + 1]) if "--spec" in argv else SPEC
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if "--part" in argv:
        part = argv[argv.index("--part") + 1]
        if mode != "log" or part not in spec.get("parts", {}):
            sys.stderr.write("unknown part {!r} for {}; known: {}\n".format(part, mode, ", ".join(spec.get("parts", {}))))
            return 2
        spec = dict(spec, **spec["parts"][part])
    if leg not in spec["skip_ceiling"]:
        sys.stderr.write("unknown leg {!r}; known: {}\n".format(leg, ", ".join(spec["skip_ceiling"])))
        return 2
    # An `if: always()` step after a failed suite step reads a log nobody wrote: say so, not a traceback.
    try:
        lines = Path(log).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        print("COULD NOT CHECK: cannot read {} ({}) -- did the step that writes it run?".format(log, e.strerror))
        print("CI LEG {} ({}): COULD NOT CHECK".format(leg, mode))
        return 2
    rc, msgs = (check_collect if mode == "collect" else check_log)(lines, spec, leg)
    for m in msgs:
        print(("TRIPWIRE: " if rc == 1 else "") + m)
    print("CI LEG {} ({}): {}".format(leg, mode, {0: "OK", 1: "RED", 2: "COULD NOT CHECK"}[rc]))
    return rc


if __name__ == "__main__":
    sys.exit(main())
