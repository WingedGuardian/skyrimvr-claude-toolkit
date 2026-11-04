#!/usr/bin/env python3
"""The local pre-push gate: what a push must pass at the desk before it costs a CI round.

    python scripts/prepush-gate.py [--base REV] [--python EXE]

Full suites run on GitHub CI, never on the dev machine (user ruling 2026-10-18). This is the targeted set that
catches what actually turned CI red: of the ten red full-qualification runs between 2026-10-26 and 2026-10-30,
seven failed on something below -- a moved mutation anchor, a corpus row no longer reproduced, a stale pin
audit, a test module that was not in the pusher's local set -- and each cost a 4-5 hour round (user ruling
2026-10-31: no push without this gate; it is installed as the maintainers' git pre-push hook, never shipped).

Steps, each red on its own:
  1. collection: `pytest --collect-only tests/` collects at least the recorded floors (.github/ci-leg.json: the
     suite part plus the mutation part) with no errors -- a module that stopped importing, or a floor the push
     should have raised;
  2. drift: the generated agent files match agent/ (scripts/gen_agent_trees.py --check) and GUARD-FILES.sha256 is
     fresh (scripts/gen-guard-hashes.py --check);
  3. sentinels: the deterministic test files that fail CI whole when stale (SENTINELS below), plus the tests for
     the files this push changes (`git diff --name-only BASE...HEAD`: a changed test file runs itself; a changed
     tools/ or scripts/ module runs every tests/test_*<its stem>*.py; the guard adapters run the wrapper tests; and
     EVERY test file whose text names a changed file's basename runs, since that is the test that goes red on it).
     Up to seven processes: timing/deadline, corpus, rule messages, and up to four changed-file chunks.

BASE defaults to the branch's upstream (a base it cannot read stops the gate: it would select no tests), else the merge-base with origin/master. A dirty working tree is reported
(the gate runs on the tree, the push sends the commits). Exit 0 = passed, 1 = a step failed, 2 = could not run.
Runs under the resource budget when the hook launches it (rb.py); prints each step's seconds so growth is seen.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SENTINELS = [
    "tests/test_mutation_anchors.py",      # every twin's anchor occurs exactly once (6 of the week's reds)
    "tests/test_mutation_inventory.py",    # anchors unique, Python mutants compile
    "tests/test_guard_corpus.py",          # intended rows reproduce
    "tests/test_skyguard_codes.py",        # every rule and corpus row maps to one code
    "tests/test_rule_messages.py",         # no message echoes its command
    "tests/test_skyguard_timing.py",       # the timing shapes (CI runs them as their own step)
    "tests/test_python_fallback.py",       # interpreter fallback chain and its clocks
    "tests/test_hook_deadline.py",         # hook deadline arithmetic
    "tests/test_thirdparty_audit.py",      # pins and their audit
    "tests/test_spriggit_fixture.py",      # the Spriggit pin agrees with the manifest
    "tests/test_codex_definitions.py",     # the frozen Codex hook definitions
    "tests/test_gen_agent_trees.py",       # agent/ -> generated files
    "tests/test_ci_leg.py",                # the CI tripwires
    "tests/test_workflows.py",             # the workflow files
    # NOT here: tests/test_release_build.py and test_release_payload.py build a release payload per case and ran the
    # gate past its 20-minute step limit (2026-10-31); CI's release-build job runs them on every push, and the pin
    # audits that actually went stale (thirdparty, spriggit) are above. They still run when the push changes them.
]
TIMING_SENTINELS = {"tests/test_skyguard_timing.py", "tests/test_python_fallback.py", "tests/test_hook_deadline.py"}
# changed path prefix -> extra test files (a changed test file always runs itself)
PATH_TESTS = {
    "agent/guards/": ["tests/test_python_fallback.py", "tests/test_hook_deadline.py", "tests/test_skyguard_shell.py",
                      "tests/test_skyguard_write.py"],
    "agent/": ["tests/test_gen_agent_trees.py"],
    ".github/": ["tests/test_workflows.py", "tests/test_ci_leg.py"],
    "scripts/build_release_zip.py": ["tests/test_release_build.py", "tests/test_release_payload.py"],
    "scripts/build-release-zip.sh": ["tests/test_release_build.py", "tests/test_release_payload.py"],
    "tools/check-release-payload.py": ["tests/test_release_payload.py"],
    "tests/guard_corpus/": ["tests/test_guard_corpus.py", "tests/test_skyguard_codes.py"],
    "tests/conftest.py": ["tests/test_ci_leg.py"],
}


def _git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True).stdout.strip()


def base_rev(explicit):
    if explicit:
        return explicit
    up = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if up and _git("rev-parse", "--verify", "-q", up):
        return up
    for ref in ("origin/master", "origin/main", "master"):
        mb = _git("merge-base", "HEAD", ref)
        if mb:
            return mb
    return ""


def base_readable(base):
    """A typo in --base would otherwise read as "nothing changed" and run no changed-file tests."""
    return bool(base) and bool(_git("rev-parse", "--verify", "-q", base + "^{commit}"))


def changed_files(base):
    if not base:
        return []
    out = _git("diff", "--name-only", base + "...HEAD")
    return [l.replace("\\", "/") for l in out.splitlines() if l.strip()]


def referencing_tests(changed, test_dir=None):
    """Every tests/test_*.py whose TEXT names a changed file's basename.

    Stem matching alone missed CI run 41254615914's three deterministic reds on all 8 legs: the batch changed
    full-qualification.yml, ci.yml and build_release_zip.py, and test_dependency_locks / test_pr_ci_policy /
    test_verify_release each assert on those files by name without sharing a stem with them. A test that spells the
    changed file is the test that will go red on it; selecting by reference is what CI would have shown.
    """
    test_dir = test_dir or REPO / "tests"
    names = {Path(p).name for p in changed if not (p.startswith("tests/test_") and p.endswith(".py"))}
    # A test can reach a workflow through its folder, naming no file: X4 CI run 41399417477 went red on all four legs
    # on a test that globs .github/workflows, and name matching had skipped it.
    if any(p.startswith(".github/workflows/") for p in changed):
        names.add(".github/workflows")
    if not names:
        return []
    out = []
    for f in sorted(test_dir.glob("test_*.py")):
        if f.name == "test_mutations.py":
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(n in text for n in names):
            out.append(f.relative_to(REPO).as_posix())
    return out


def tests_for(changed):
    want = []
    for p in changed:
        if p == "tests/test_mutations.py":
            continue    # the whole mutation gate (hours; CI runs it sharded); its anchors and inventory are sentinels
        if p.startswith("tests/test_") and p.endswith(".py"):
            want.append(p)
        for prefix, tests in PATH_TESTS.items():
            if p.startswith(prefix):
                want.extend(tests)
        if (p.startswith("tools/") or p.startswith("scripts/")) and p.endswith(".py"):
            stem = Path(p).stem.replace("-", "_")
            want.extend(sorted(Path(f).as_posix() for f in glob.glob(str(REPO / "tests" / ("test_*" + stem + "*.py")))))
    want.extend(referencing_tests(changed))
    seen, out = set(), []
    for t in want:
        rel = str(Path(t).as_posix()).replace(str(REPO.as_posix()) + "/", "")
        if rel not in seen and (REPO / rel).is_file():
            seen.add(rel)
            out.append(rel)
    return out


def twin_selection(changed, added_lines="", aliases=None):
    """The files whose mutation twins this push must run: what it changed, and every file a line added to the twin table
    names, spelled out or through one of the table's aliases. CI run 41560815549 went red on three surviving twins of a file
    this gate's other steps had passed in full: those tests pass on the pristine tree, so only running the twin shows it."""
    files = {p for p in changed if p != "tests/test_mutations.py"}
    files |= set(re.findall(r"(?:tools|scripts|agent|tests)/[\w./-]+\.\w+|setup\.sh", added_lines))
    for alias, path in (aliases or {}).items():
        if re.search(r"\b" + re.escape(alias) + r"\b", added_lines):
            files.add(path)
    return sorted(files)


def twin_aliases():
    text = (REPO / "tests" / "test_mutations.py").read_text(encoding="utf-8", errors="replace")
    return dict(re.findall(r'^(_[A-Z][A-Z0-9_]*) = "([^"\n]+)"$', text, re.M))


def twin_steps(py, base, changed, processes=4, per_process=50):
    """At most processes x per_process twins, each process under the cap of the other groups: a big branch gets a spread
    sample here and every twin on CI, not a push refused on time. They run after the other groups, never beside them: 600
    twins beside the timing tests starved those past their limits (2026-11-03, a gate that refused on load alone)."""
    added = ""
    if "tests/test_mutations.py" in changed and base:
        diff = _git("diff", "-U0", base + "...HEAD", "--", "tests/test_mutations.py")
        added = "\n".join(l for l in diff.splitlines() if l.startswith("+"))
    files = twin_selection(changed, added, twin_aliases())
    env = dict(os.environ, SKYGUARD_MUTATION_FILES=",".join(files), SKYGUARD_MUTATION_LIMIT=str(processes * per_process))
    node = "tests/test_mutations.py::test_guard_actually_bites"
    listed = subprocess.run([py, "-B", "-m", "pytest", "-p", "no:cacheprovider", "--collect-only", "-q", node], cwd=REPO, env=env,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    count = sum(1 for l in listed.stdout.splitlines() if l.startswith(node))
    if listed.returncode not in (0, 5):
        raise RuntimeError("could not list the twins of the changed files:\n" + (listed.stdout + listed.stderr)[-1500:])
    if not count:
        return []
    if count > processes * per_process:
        # A tree older than the selector ignores these variables, and every process then runs the whole table: four
        # identical copies stopped at 6% of 2,700 twins on master (2026-11-03). CI runs every twin on that tree.
        print("   this tree has no twin selector ({} twins listed): twins are left to CI".format(count), flush=True)
        return []
    n = min(processes, -(-count // 20))
    print("   {} mutation twin(s) of the changed files, in {} process(es)".format(count, n), flush=True)
    return [("mutation twins of changed files {}/{}".format(k + 1, n),
             [py, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-v", "-rfE", node], 1200,
             dict(env, SKYGUARD_MUTATION_SHARD="{}/{}".format(k + 1, n))) for k in range(n)]


class Gate:
    def __init__(self, py):
        self.py = py
        self.failed = []
        self.t0 = time.time()

    def step(self, name, cmd, *, check=None, timeout=900):
        """Run one step with a hard timeout. Output streams to a log file, so a hang (two gates hung on a pytest
        grandchild on 2026-10-31) is killed at the limit and reported with the last lines written -- under -v, the
        test that was running -- instead of holding the push and the resource budget open."""
        t = time.time()
        print("== {}".format(name), flush=True)
        log = REPO / ".pytest_cache" / "prepush-gate.log"
        log.parent.mkdir(exist_ok=True)
        timed_out = False
        with open(log, "w", encoding="utf-8", errors="replace") as fh:
            try:
                rc = subprocess.run(cmd, cwd=REPO, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                rc, timed_out = 124, True
        out = log.read_text(encoding="utf-8", errors="replace")
        r = subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
        ok = rc == 0
        problems = []
        if check is not None:
            problems = check(r)
            ok = ok and not problems
        secs = time.time() - t
        if not ok:
            tail = out.strip().splitlines()[-40:]
            print("\n".join("   " + l for l in tail))
            for p in problems:
                print("   " + p)
            if timed_out:
                print("   TIMED OUT after {} s; the last line above is the test that was running (log: {})".format(timeout, log))
            self.failed.append(name)
        print("   {} in {:.0f}s".format("ok" if ok else "FAILED", secs), flush=True)
        return ok


    def parallel(self, steps, workers=None):
        """Run steps at once, at most `workers` at a time, each to its own log and under its own timeout from when it starts; report
        each as step() would. A free worker takes the next step, so nothing has to predict what a step costs."""
        t = time.time()
        steps = list(steps)
        limit = workers or len(steps) or 1
        procs = []
        waiting = 0
        while waiting < len(steps) or any(p[5] is None for p in procs):
            while waiting < len(steps) and sum(p[5] is None for p in procs) < limit:
                name, cmd, timeout, *env = steps[waiting]
                print("== {} (parallel)".format(name), flush=True)
                log = REPO / ".pytest_cache" / "prepush-gate-{}.log".format(waiting)
                log.parent.mkdir(exist_ok=True)
                fh = open(log, "w", encoding="utf-8", errors="replace")
                procs.append([name, subprocess.Popen(cmd, cwd=REPO, env=env[0] if env else None, stdout=fh, stderr=subprocess.STDOUT), fh, log,
                              time.time() + timeout, None])
                waiting += 1
            for p in procs:
                if p[5] is not None:
                    continue
                rc = p[1].poll()
                if rc is not None:
                    p[5] = rc
                elif time.time() > p[4]:
                    p[1].kill()
                    p[1].wait()
                    p[5] = 124
            time.sleep(0.5)
        for name, proc, fh, log, _, rc in procs:
            fh.close()
            out = log.read_text(encoding="utf-8", errors="replace")
            if rc != 0:
                print("-- {}:".format(name))
                print("\n".join("   " + l for l in out.strip().splitlines()[-25:]))
                if rc == 124:
                    print("   TIMED OUT; the last line above is the test that was running (log: {})".format(log))
                self.failed.append(name)
            m = re.search(r"=+ (.*) in ([0-9.]+)s", out.strip().splitlines()[-1] if out.strip() else "")
            print("   {}: {} in {}s".format(name, "ok" if rc == 0 else "FAILED", m.group(2) if m else "?"), flush=True)
        print("   parallel block done in {:.0f}s".format(time.time() - t), flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="", help="compare against this revision (default: upstream, else origin/master)")
    ap.add_argument("--python", default=sys.executable, help="interpreter that has pytest (default: this one)")
    ap.add_argument("--root", type=Path, default=None,
                    help="the checkout to gate (default: this script's repo); the hook passes the tree being pushed")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")   # a cp1252 console must not crash the gate on a test's output
        except Exception:                           # (UnicodeEncodeError printing a failing step, X4, 2026-11-01)
            pass
    global REPO
    if a.root is not None:
        REPO = a.root.resolve()
        if not (REPO / "tests").is_dir() or not (REPO / ".github" / "ci-leg.json").is_file():
            print("prepush-gate: {} is not a toolkit checkout (no tests/ or .github/ci-leg.json)".format(REPO))
            return 2
    py = a.python
    probe = subprocess.run([py, "-c", "import pytest"], capture_output=True)
    if probe.returncode != 0:
        print("prepush-gate: {} has no pytest; pass --python <interpreter with pytest>".format(py))
        return 2
    spec = json.loads((REPO / ".github" / "ci-leg.json").read_text(encoding="utf-8"))
    floor = spec["collected_floor"] + spec["parts"]["mutations"]["collected_floor"]
    base = base_rev(a.base)
    if base and not base_readable(base):
        print("prepush-gate: cannot read the base revision {}".format(base))
        return 2
    changed = changed_files(base)
    dirty = _git("status", "--porcelain", "--untracked-files=no")
    print("prepush-gate: {} @ {}  base {}  {} changed file(s){}".format(
        _git("rev-parse", "--abbrev-ref", "HEAD"), _git("rev-parse", "--short", "HEAD"), base or "(none)",
        len(changed), "  [DIRTY TREE: the gate runs on the tree, the push sends the commits]" if dirty else ""))
    g = Gate(py)
    pyt = [py, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q"]

    def collect_check(r):
        # Node ids never count: test_ci_leg's own parameter ids carry "collected 12 items"-shaped text (the first
        # version of this check read one of those and reported 12 < floor on a 17,018-test collection).
        lines = [l for l in r.stdout.splitlines() if "::" not in l]
        m = next((m for l in reversed(lines) for m in [re.search(r"(\d+) tests? collected", l)] if m), None)
        if not m:
            return ["no 'N tests collected' line"]
        n = int(m.group(1))
        out = []
        if any(re.search(r"\d+ errors?\b", l) for l in lines if "collected" in l or l.startswith("=")):
            out.append("collection reported errors: a module did not import")
        if n < floor:
            out.append("collected {} < floor {} (ci-leg.json suite + mutations)".format(n, floor))
        else:
            print("   collected {} (floor {})".format(n, floor))
        return out
    g.step("collection against the recorded floors", pyt + ["--collect-only", "tests/"], check=collect_check)
    g.step("generated agent files match agent/", [py, "-B", "scripts/gen_agent_trees.py", "--check"])
    g.step("GUARD-FILES.sha256 is fresh", [py, "-B", "scripts/gen-guard-hashes.py", "--check"])
    extra = [t for t in tests_for(changed) if t not in SENTINELS]
    missing = [s for s in SENTINELS if not (REPO / s).is_file()]
    if missing:
        print("   sentinel file(s) missing: {}".format(", ".join(missing)))
        g.failed.append("sentinels present")
    files = [s for s in SENTINELS if (REPO / s).is_file()] + extra
    if extra:
        print("   tests for changed files: " + ", ".join(extra))
    # Parallel processes, -v so a timeout names the running test. MEASURED 2026-10-31 (each file alone, 36% CPU
    # share): the corpus rows 531 s and the rule messages 548 s -- each fires every intended row through the real
    # wrapper -- against about 60 s for everything else and ~6 min for the timing files. In one process the set took
    # 25+ min and, twice, test_hook_deadline hung behind the others (cause open; a killed child's grandchild holding
    # the pipe is the suspect). As four processes the wall time is the slowest one, ~10 min, and a hang is isolated.
    verbose = [py, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-v", "-rfE", "--durations=10"]
    groups = [("timing and deadline tests", [f for f in files if f in TIMING_SENTINELS], 900),
              ("corpus rows", [f for f in files if f == "tests/test_guard_corpus.py"], 1200),
              ("rule messages", [f for f in files if f == "tests/test_rule_messages.py"], 1200)]
    rest = [f for f in files if f not in TIMING_SENTINELS
            and f not in ("tests/test_guard_corpus.py", "tests/test_rule_messages.py")]
    # The changed-file set grows with the branch (177 files on a peer's branch; a shared-core re-pin selects most of the suite). One
    # process per file, up to the policy's eight workers: fixed chunks had to guess each file's cost beforehand, and by file size one
    # chunk drew the slow tests and reached 79% in 30 minutes, three pushes running, while the others idled from minute 8 (2026-11-03).
    # The cap catches a hang in one file; it must not refuse a merely large push.
    steps = [(name + " ({} files)".format(len(fs)), verbose + fs, cap) for name, fs, cap in groups if fs]
    steps += [(name, verbose + [name], 1200) for name in rest]
    g.parallel(steps, workers=8)
    if not g.failed:
        twins = twin_steps(py, base, changed)
        if twins:
            g.parallel(twins)
    total = time.time() - g.t0
    if g.failed:
        print("prepush-gate: FAILED ({}) in {:.0f}s -- fix before pushing; CI would have gone red on it".format(
            ", ".join(g.failed), total))
        return 1
    print("prepush-gate: passed in {:.0f}s{}".format(total, "  (over 15 min: trim or shard this gate)" if total > 900 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
