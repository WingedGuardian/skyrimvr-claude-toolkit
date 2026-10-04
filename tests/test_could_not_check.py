"""scripts/check_could_not_check.py: CI fails on a COULD NOT CHECK skip nobody allowed."""
import subprocess
import sys

from conftest import REPO

SCRIPT = REPO / "scripts" / "check_could_not_check.py"
DOCKER = "SKIPPED [1] tests/test_harness_controls.py:140: COULD NOT CHECK: Docker daemon unavailable\n"
OTHER = "SKIPPED [1] tests/test_x.py:9: COULD NOT CHECK: some tool is missing\n"
PLAIN = "SKIPPED [1] tests/test_y.py:3: not applicable on this platform\n"


def run(tmp_path, text, *allow):
    log = tmp_path / "out.log"
    summary = "\n==== 3 passed, 1 skipped in 1.00s ====\n" if text else ""
    log.write_text(text + summary, encoding="utf-8")
    cmd = [sys.executable, str(SCRIPT), str(log)]
    for a in allow:
        cmd += ["--allow", a]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def test_an_allowed_skip_passes(tmp_path):
    assert run(tmp_path, DOCKER, "Docker daemon").returncode == 0


def test_an_unallowed_skip_fails_and_is_named(tmp_path):
    r = run(tmp_path, DOCKER + OTHER, "Docker daemon")
    assert r.returncode == 1
    assert "some tool is missing" in r.stdout + r.stderr


def test_no_allowance_means_even_docker_fails(tmp_path):
    assert run(tmp_path, DOCKER).returncode == 1


def test_an_ordinary_skip_is_not_our_business(tmp_path):
    assert run(tmp_path, PLAIN).returncode == 0


def test_a_log_with_no_skip_summary_at_all_fails(tmp_path):
    # An empty log (no pytest summary line) must not read as "no bad skips": nothing was checked.
    assert run(tmp_path, "").returncode == 2
