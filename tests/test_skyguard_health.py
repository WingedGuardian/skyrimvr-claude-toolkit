"""Hook health from staged telemetry: a failure clears only on a NEWER COMPLETED evaluation."""
import sys
import time

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import health  # noqa: E402

NOW = 1_800_000_000.0


def _w(d, hook, stage, line):
    d.mkdir(parents=True, exist_ok=True)
    (d / "{}.{}".format(hook, stage)).write_text(line + "\n", encoding="utf-8")


def _complete(d, t, inv="1-1", status="evaluated"):
    _w(d, "h", "invoked", "{} {}".format(t - 0.2, inv))
    _w(d, "h", "received", "{} {} bytes=40".format(t - 0.1, inv))
    _w(d, "h", "evaluated", "{} {} {} allow allow".format(t, inv, status))
    _w(d, "h", "emitted", "{} {} ok".format(t + 0.01, inv))


def test_nothing_is_not_exercised(tmp_path):
    assert health.hook_state(tmp_path, "h", NOW)[0] == "NOT EXERCISED"


def test_a_completed_evaluation_is_healthy(tmp_path):
    _complete(tmp_path, NOW - 5)
    assert health.hook_state(tmp_path, "h", NOW)[0] == "HEALTHY"


def test_a_failure_after_the_last_completion_is_degraded(tmp_path):
    _complete(tmp_path, NOW - 50)
    _w(tmp_path, "h", "failed", "{} 2-2 engine exit 1".format(NOW - 10))
    assert health.hook_state(tmp_path, "h", NOW)[0] == "DEGRADED"


def test_a_newer_receipt_does_not_clear_a_failure(tmp_path):
    """Twin of the recovery rule: payload arrival is not proof that a verdict was reached."""
    _w(tmp_path, "h", "failed", "{} 2-2 engine exit 1".format(NOW - 10))
    _w(tmp_path, "h", "invoked", "{} 3-3".format(NOW - 5))
    _w(tmp_path, "h", "received", "{} 3-3 bytes=40".format(NOW - 5))
    assert health.hook_state(tmp_path, "h", NOW)[0] == "DEGRADED"


def test_a_newer_completed_evaluation_clears_it_and_says_so(tmp_path):
    _w(tmp_path, "h", "failed", "{} 2-2 engine exit 1".format(NOW - 10))
    _complete(tmp_path, NOW - 5, inv="3-3")
    state, detail = health.hook_state(tmp_path, "h", NOW)
    assert state == "HEALTHY" and "recovered" in detail


def test_equal_timestamps_prove_no_recovery(tmp_path):
    _complete(tmp_path, NOW - 5, inv="3-3")
    _w(tmp_path, "h", "failed", "{} 2-2 engine exit 1".format(NOW - 5))
    assert health.hook_state(tmp_path, "h", NOW)[0] == "DEGRADED"


def test_an_evaluation_never_emitted_is_not_a_completion(tmp_path):
    _w(tmp_path, "h", "failed", "{} 2-2 emission not JSON".format(NOW - 10))
    _w(tmp_path, "h", "evaluated", "{} 3-3 evaluated deny write.DENY_PLUGIN".format(NOW - 5))
    _w(tmp_path, "h", "emitted", "{} 2-2 ok".format(NOW - 11))
    assert health.hook_state(tmp_path, "h", NOW)[0] == "DEGRADED"


def test_an_invocation_that_never_finished_is_degraded(tmp_path):
    _complete(tmp_path, NOW - 1000, inv="1-1")
    _w(tmp_path, "h", "invoked", "{} 9-9".format(NOW - 400))
    state, detail = health.hook_state(tmp_path, "h", NOW)
    assert state == "DEGRADED" and "never finished" in detail


def test_a_partial_evaluation_is_degraded(tmp_path):
    _complete(tmp_path, NOW - 5, status="partial")
    assert health.hook_state(tmp_path, "h", NOW)[0] == "DEGRADED"


def test_an_old_completion_is_stale(tmp_path):
    _complete(tmp_path, NOW - 9000)
    assert health.hook_state(tmp_path, "h", NOW)[0] == "STALE"


def test_a_truncated_record_is_unmeasurable_never_green(tmp_path):
    _complete(tmp_path, NOW - 5)
    _w(tmp_path, "h", "failed", "20260909_15")
    assert health.hook_state(tmp_path, "h", NOW)[0] == "UNMEASURABLE"


def test_3x_heartbeat_files_are_ignored(tmp_path):
    (tmp_path / "protect-bash.blind").write_text("20260101_000000 NO PAYLOAD\n")
    assert health.hook_state(tmp_path / "claude", "protect-bash", NOW)[0] == "NOT EXERCISED"


# ---- twins and edges beyond the plan's key tests ----------------------------------------------------
def test_an_invocation_still_inside_the_kill_window_is_not_degraded(tmp_path):
    """Twin of the never-finished rule: a call in flight for 10 s is normal, not a kill."""
    _complete(tmp_path, NOW - 1000, inv="1-1")
    _w(tmp_path, "h", "invoked", "{} 9-9".format(NOW - 10))
    assert health.hook_state(tmp_path, "h", NOW)[0] == "HEALTHY"


def test_an_older_unfinished_invocation_does_not_outrank_a_newer_emission(tmp_path):
    """Parallel tool calls overwrite each other's files: an `invoked` OLDER than the last emission
    is not a kill."""
    _w(tmp_path, "h", "invoked", "{} 9-9".format(NOW - 400))
    _complete(tmp_path, NOW - 100, inv="1-1")
    assert health.hook_state(tmp_path, "h", NOW)[0] == "HEALTHY"


def test_a_failed_evaluation_status_is_degraded_too(tmp_path):
    """An INERT refusal (decision deny, status failed) means nothing was checked."""
    _complete(tmp_path, NOW - 5, status="failed")
    state, detail = health.hook_state(tmp_path, "h", NOW)
    assert state == "DEGRADED" and "failed" in detail


def test_a_fresh_wrapper_failure_stage_is_named(tmp_path):
    _w(tmp_path, "h", "failed", "{} 2-2 python cannot run Python".format(NOW - 1))
    state, detail = health.hook_state(tmp_path, "h", NOW)
    assert state == "DEGRADED" and "python" in detail and "cannot run Python" in detail


def test_receipt_only_with_no_failure_is_not_healthy(tmp_path):
    """A payload arriving proves nothing about a verdict."""
    _w(tmp_path, "h", "invoked", "{} 3-3".format(NOW - 2))
    _w(tmp_path, "h", "received", "{} 3-3 bytes=40".format(NOW - 2))
    assert health.hook_state(tmp_path, "h", NOW)[0] != "HEALTHY"


@pytest.mark.parametrize("stage,line", [
    ("invoked", "abc 1-1"),
    ("invoked", "nan 1-1"),
    ("received", "1.0 1-1"),
    ("evaluated", "1.0 1-1 evaluated allow"),
    ("evaluated", "1.0 1-1 bogus allow allow"),
    ("emitted", "1.0"),
    ("failed", ""),
])
def test_unparseable_records_are_unmeasurable(tmp_path, stage, line):
    _w(tmp_path, "h", stage, line)
    assert health.hook_state(tmp_path, "h", NOW)[0] == "UNMEASURABLE"


def test_record_writes_a_line_the_reader_accepts_and_never_raises(tmp_path):
    assert health.record(tmp_path / "x", "h", "evaluated", "7-7", "evaluated allow -")
    t, inv, rest = health.read(tmp_path / "x" / "h.evaluated", "evaluated")
    assert inv == "7-7" and rest == ["evaluated", "allow", "-"] and abs(t - time.time()) < 60
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert health.record(blocker / "sub", "h", "failed", "1-1", "x") is False  # OSError swallowed


def test_latency_percentiles_and_trim(tmp_path):
    lines = ["i{} {} {}".format(n, 100.0, 100.0 + n / 1000.0) for n in range(1, 101)]
    (tmp_path / "h.latency").write_text("\n".join(lines) + "\ngarbage\n")
    n, p50, p95 = health.latency(tmp_path, "h")
    assert n == 100 and 45 <= p50 <= 55 and 90 <= p95 <= 100
    big = tmp_path / "h.latency"
    big.write_text(("i 1.0 1.001\n" * 30000))
    assert big.stat().st_size > health.LATENCY_MAX_BYTES
    health.trim_latency(tmp_path, "h")
    assert len(big.read_text().splitlines()) == health.LATENCY_KEEP_LINES


def test_a_malformed_timestamp_with_enough_fields_is_unmeasurable(tmp_path):
    """The twin of the truncated-record test: only the TIMESTAMP check can reject this one."""
    _complete(tmp_path, NOW - 5)
    _w(tmp_path, "h", "failed", "20260909_15 2-2 engine exit 1")
    assert health.hook_state(tmp_path, "h", NOW)[0] == "UNMEASURABLE"
