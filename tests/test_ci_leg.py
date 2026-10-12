"""scripts/check_ci_leg.py: the per-leg CI tripwires (Plan 8 T10.4) -- each clause proven able to go red."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from conftest import REPO

SCRIPT = REPO / "scripts" / "check_ci_leg.py"
SPEC = {"collected_floor": 10, "skip_ceiling": {"Linux": 2, "Windows": 3}, "xfail_ceiling": 1,
        "required_modules": {"all": ["test_a.py"], "Windows": ["test_w.py"], "Linux": []},
        "forbidden_passing": ["test_load.py"]}


def _log(collected=12, skipped=1, xfailed=0, extra=(), drop=()):
    lines = ["collected {} items".format(collected), "",
             "tests/test_a.py::test_one PASSED                [ 10%]",
             "tests/test_w.py::test_win PASSED                [ 20%]",
             "tests/test_load.py::test_burn SKIPPED (opt-in)  [ 30%]", *extra,
             "=== {} passed, {} skipped, {} xfailed in 12.34s ===".format(collected - skipped - xfailed, skipped, xfailed)]
    return "\n".join(l for l in lines if not any(d in l for d in drop)) + "\n"


def _run(tmp_path, mode, text, leg="Windows"):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(SPEC), encoding="utf-8")
    log = tmp_path / "x.log"
    log.write_text(text, encoding="utf-8")
    return subprocess.run([sys.executable, "-B", str(SCRIPT), mode, str(log), "--leg", leg, "--spec", str(spec)],
                          capture_output=True, text=True, timeout=60)


def test_a_good_leg_passes(tmp_path):
    r = _run(tmp_path, "log", _log())
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("text,leg,why", [
    (_log(collected=9), "Windows", "collected < floor"),
    (_log(skipped=4), "Windows", "skipped > ceiling"),
    (_log(skipped=3), "Linux", "the ceiling is per leg"),
    (_log(xfailed=2), "Windows", "xfailed > ceiling"),
    (_log(drop=("test_a.py",)), "Windows", "a required module with no PASSED test"),
    (_log(drop=("test_w.py",)), "Windows", "the leg's own required module"),
    (_log(extra=("tests/test_load.py::test_burn2 PASSED  [ 40%]",)), "Windows", "the opt-in load test ran"),
    (_log(drop=("collected ",)), "Windows", "no collected line (not -v)"),
])
def test_each_tripwire_goes_red(tmp_path, text, leg, why):
    r = _run(tmp_path, "log", text, leg)
    assert r.returncode == 1, (why, r.stdout)
    assert "TRIPWIRE" in r.stdout, why


def test_the_real_pytest_collect_line_is_read(tmp_path):
    """`pytest -v` piped to a file prints `collecting ... collected N items` on ONE line (MEASURED 2026-10-06). The
    synthetic logs above start with `collected`, so the checker passed its tests and failed every real CI run."""
    real = _log().replace("collected 12 items", "collecting ... collected 12 items", 1)
    r = _run(tmp_path, "log", real)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_node_id_mentioning_collected_is_not_the_collect_line(tmp_path):
    """Only the collect line counts: a parametrized node id can carry the same words (test_ci_leg's own ids do)."""
    fake = "tests/test_a.py::test_x[collected 99 items] PASSED  [ 1%]"
    r = _run(tmp_path, "log", _log(extra=(fake,), drop=("collected 12",)))
    assert r.returncode == 1 and "collected N items" in r.stdout, r.stdout


def test_the_linux_leg_does_not_require_the_windows_module(tmp_path):
    assert _run(tmp_path, "log", _log(drop=("test_w.py",)), "Linux").returncode == 0


def test_no_summary_is_could_not_check(tmp_path):
    r = _run(tmp_path, "log", "collected 12 items\nnothing else\n")
    assert r.returncode == 2 and "COULD NOT CHECK" in r.stdout


@pytest.mark.parametrize("text,rc", [
    ("tests/test_a.py::x\n\n12 tests collected in 1.0s\n", 0),
    ("tests/test_a.py::x\n\n9 tests collected in 1.0s\n", 1),
    ("12 tests collected, 1 error in 1.0s\n", 1),
    ("nothing\n", 2),
    # A node id may itself carry summary-shaped text (this very file's ids do): only the summary line counts.
    ("tests/test_ci_leg.py::test_collect_mode[3 tests collected, 1 error in 1.0s\\n-1]\n\n12 tests collected in 1.0s\n",
     0),
])
def test_collect_mode(tmp_path, text, rc):
    assert _run(tmp_path, "collect", text).returncode == rc


def test_unknown_leg_and_bad_usage_are_exit_2(tmp_path):
    assert _run(tmp_path, "log", _log(), leg="Plan9").returncode == 2
    assert subprocess.run([sys.executable, "-B", str(SCRIPT)], capture_output=True, timeout=60).returncode == 2


def test_the_recorded_spec_is_well_formed_and_names_real_modules():
    spec = json.loads((REPO / ".github" / "ci-leg.json").read_text(encoding="utf-8"))
    assert set(spec["skip_ceiling"]) == {"Linux", "Windows"}
    mods = spec["required_modules"]["all"] + spec["required_modules"]["Windows"] + spec["required_modules"]["Linux"]
    mods += spec["forbidden_passing"]
    missing = [m for m in mods if not (REPO / "tests" / m).is_file()]
    assert not missing, missing
    assert "test_hook_deadline.py" in spec["required_modules"]["all"]       # T10: on both legs
    assert "test_hook_deadline_load.py" in spec["forbidden_passing"]         # T10: never in CI
    for harness in ("test_harness_controls.py", "test_harness_integrations.py"):
        assert harness in spec["required_modules"]["all"]


def test_a_missing_log_is_could_not_check_not_a_traceback(tmp_path):
    """CI run 38178385999: the suite step failed, so the always() tripwire read a behavioral.log nobody wrote."""
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(SPEC), encoding="utf-8")
    r = subprocess.run([sys.executable, "-B", str(SCRIPT), "log", str(tmp_path / "behavioral.log"), "--leg", "Linux",
                        "--spec", str(spec)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "Traceback" not in r.stderr, r.stderr
    assert "COULD NOT CHECK: cannot read" in r.stdout and "behavioral.log" in r.stdout, r.stdout
