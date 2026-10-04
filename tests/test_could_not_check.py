"""scripts/check_could_not_check.py: CI fails on a COULD NOT CHECK skip nobody allowed."""
import subprocess
import sys

from conftest import REPO

SCRIPT = REPO / "scripts" / "check_could_not_check.py"
DOCKER = "SKIPPED [1] tests/test_harness_controls.py:140: COULD NOT CHECK: Docker daemon unavailable\n"
OTHER = "SKIPPED [1] tests/test_x.py:9: COULD NOT CHECK: some tool is missing\n"
PLAIN = "SKIPPED [1] tests/test_y.py:3: not applicable on this platform\n"
HEADER = "=== short test summary info ===\n"


def run(tmp_path, text, *allow, header=True, summary="==== 3 passed, 1 skipped in 1.00s ====\n"):
    log = tmp_path / "out.log"
    body = (HEADER if header and text else "") + text
    log.write_text(body + ("\n" + summary if text else ""), encoding="utf-8")
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


def test_counted_skips_without_a_short_summary_section_cannot_pass(tmp_path):
    # Run without -rs: pytest says "1 skipped" but lists nothing, so a COULD NOT CHECK skip would
    # be invisible and the check would pass vacuously.
    r = run(tmp_path, DOCKER, "Docker daemon", header=False)
    assert r.returncode == 2, r.stdout
    assert "-rs" in r.stdout


def test_a_run_with_no_skips_needs_no_short_summary_section(tmp_path):
    r = run(tmp_path, "collected\n", header=False, summary="==== 4 passed in 1.00s ====\n")
    assert r.returncode == 0, r.stdout
