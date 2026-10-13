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
    """Only the TIMESTAMP pattern can reject this: float() reads the underscores as a plausible PAST time."""
    _complete(tmp_path, NOW - 5)
    _w(tmp_path, "h", "failed", "1_799_999_990 2-2 engine exit 1")
    assert health.hook_state(tmp_path, "h", NOW)[0] == "UNMEASURABLE"


# ---- fix round 1: clock faults, sequence ordering, session status -----------------------------------
def _complete_seq(d, t, seq, status="evaluated"):
    _complete(d, t, inv="{}.9-9".format(seq), status=status)


def test_a_completion_stamped_in_the_future_is_unmeasurable_not_fresh_forever(tmp_path):
    _complete(tmp_path, NOW + 365 * 86400)
    state, detail = health.hook_state(tmp_path, "h", NOW)
    assert state == "UNMEASURABLE" and "clock skew" in detail


def test_a_stamp_within_the_slack_is_fine(tmp_path):
    _complete(tmp_path, NOW + 200)
    assert health.hook_state(tmp_path, "h", NOW)[0] == "HEALTHY"


def test_a_failure_stamped_in_the_future_is_unmeasurable(tmp_path):
    _complete(tmp_path, NOW - 5)
    _w(tmp_path, "h", "failed", "{} 2-2 engine exit 1".format(NOW + 99999))
    assert health.hook_state(tmp_path, "h", NOW)[0] == "UNMEASURABLE"


def test_a_clock_stepped_back_cannot_hide_a_failure(tmp_path):
    """The failure is the LATER invocation (seq 5) but carries an OLDER stamp than the completion (seq 4)."""
    _complete_seq(tmp_path, NOW - 5, 4)
    _w(tmp_path, "h", "failed", "{} 5.9-9 engine exit 1".format(NOW - 60))
    assert health.hook_state(tmp_path, "h", NOW)[0] == "DEGRADED"


def test_a_later_sequence_clears_a_failure_whatever_the_stamps_say(tmp_path):
    _w(tmp_path, "h", "failed", "{} 5.9-9 engine exit 1".format(NOW - 10))
    _complete_seq(tmp_path, NOW - 100, 6)      # clock stepped back: older stamp, later invocation
    state, detail = health.hook_state(tmp_path, "h", NOW)
    assert state == "HEALTHY" and "recovered" in detail


def test_the_same_sequence_proves_no_recovery(tmp_path):
    _w(tmp_path, "h", "failed", "{} 5.9-9 receipt NO PAYLOAD".format(NOW - 1))
    _complete_seq(tmp_path, NOW - 5, 5)
    assert health.hook_state(tmp_path, "h", NOW)[0] == "DEGRADED"


def _session(tmp_path, kb_guard_body):
    from skyguard import audit as audit_mod, session
    from conftest import BASH
    (tmp_path / "tools").mkdir()
    if kb_guard_body is not None:
        (tmp_path / "tools" / "kb-guard.sh").write_text(kb_guard_body, encoding="utf-8", newline="\n")
    return session.run_status(str(tmp_path), "startup", BASH, audit_mod.Audit(str(tmp_path)))


def test_a_missing_kb_guard_is_a_failed_session_run(tmp_path):
    text, status, code = _session(tmp_path, None)
    assert "NOT RUN" in text and status == "failed" and code == "session.NOT_RUN"


def test_a_kb_guard_that_snapshotted_nothing_is_partial(tmp_path):
    text, status, code = _session(tmp_path, "echo cannot read the knowledgebase\nexit 2\n")
    assert status == "partial" and code == "session.NOT_SNAPSHOTTED"


def test_a_clean_kb_guard_is_evaluated(tmp_path):
    assert _session(tmp_path, "exit 0\n")[1:] == ("evaluated", "session")


# ---- fix-limits 8: two calls of one hook at the same moment take the same sequence number -----------------------
# The wrappers compute <seq> as the previous `invoked` record's seq + 1 with no lock, so two concurrent calls can both
# take N+1. Equal sequences with DIFFERENT invocation ids are concurrent calls: only their stamps can order them.

def test_a_concurrent_completion_after_a_failure_with_the_same_sequence_clears_it(tmp_path):
    _w(tmp_path, "h", "failed", "{} 5.111-1 receipt NO PAYLOAD".format(NOW - 10))
    _complete(tmp_path, NOW - 5, inv="5.222-2")
    state, detail = health.hook_state(tmp_path, "h", NOW)
    assert state == "HEALTHY" and "recovered" in detail, (state, detail)


def test_a_concurrent_completion_before_the_failure_does_not_clear_it(tmp_path):
    _complete(tmp_path, NOW - 10, inv="5.222-2")
    _w(tmp_path, "h", "failed", "{} 5.111-1 receipt NO PAYLOAD".format(NOW - 5))
    assert health.hook_state(tmp_path, "h", NOW)[0] == "DEGRADED"


def test_a_call_never_clears_its_own_failure_whatever_the_stamps_say(tmp_path):
    """The killed-run and empty-payload records are written under the CURRENT call's id on purpose."""
    _w(tmp_path, "h", "failed", "{} 5.9-9 killed run 4.1-1 never finished".format(NOW - 10))
    _complete(tmp_path, NOW - 5, inv="5.9-9")
    assert health.hook_state(tmp_path, "h", NOW)[0] == "DEGRADED"


# ---- fix-limits 3: the adapted agents an install declares ---------------------------------------------------------

def _adapter(root, folder, name_line):
    d = root / "tools" / "adapters" / folder
    d.mkdir(parents=True)
    (d / "adapter.py").write_text("# SLOTS-BEGIN\n" + name_line + "\n# SLOTS-END\nAGENT_NAME = 'not-this-one'\n",
                                  encoding="utf-8")


def test_an_adapter_declares_its_agent_by_its_agent_name_slot(tmp_path):
    _adapter(tmp_path, "mine", 'AGENT_NAME = "my-agent"   # names the heartbeat folder')
    _adapter(tmp_path, "odd", "AGENT_NAME = 'we ird/name'")
    assert health.adapted_agents(tmp_path) == [("my-agent", "my-agent"), ("we ird/name", "we_ird_name")]


def test_an_adapter_without_a_readable_name_is_still_listed_under_its_folder(tmp_path):
    _adapter(tmp_path, "nameless", "AGENT_NAME = compute()")
    assert health.adapted_agents(tmp_path) == [("nameless (no AGENT_NAME literal)", None)]


def test_no_adapters_folder_means_no_adapted_agents(tmp_path):
    assert health.adapted_agents(tmp_path) == []


def test_the_adapted_hook_set_comes_from_the_template_it_was_copied_from():
    """health's names for an adapted agent's records are the ones the template and the neutral entry write."""
    tpl = (REPO / "tools" / "adapting" / "adapter_template.py").read_text(encoding="ascii")
    from skyguard import neutral
    assert '"pre-tool." + stage' in tpl and health.ADAPTED_HOOKS == ("pre-tool",)
    assert health.ADAPTED_EVALUATED == neutral.TELEMETRY_ROLE
    assert '".hook-heartbeat", "adapted"' in tpl and health.ADAPTED_DIR == "adapted"


def test_an_adapted_agent_is_read_with_the_engines_evaluated_record(tmp_path):
    d = tmp_path / ".claude" / "backups" / ".hook-heartbeat" / "adapted" / "a"
    _w(d, "pre-tool", "invoked", "{} 1.5-1".format(NOW - 6))
    _w(d, "neutral-pre-tool", "evaluated", "{} 1.5-1 evaluated allow -".format(NOW - 5))
    _w(d, "pre-tool", "emitted", "{} 1.5-1 allow".format(NOW - 4))
    assert health.hook_state(d, "pre-tool", NOW, files={"evaluated": "neutral-pre-tool"})[0] == "HEALTHY"
    assert health.hook_state(d, "pre-tool", NOW)[0] == "NOT EXERCISED"     # the default names find no evaluation


def _report_json(root):
    import io
    import json
    out = io.StringIO()
    code = health.report(root, as_json=True, now=NOW, out=out)
    return code, json.loads(out.getvalue())


def test_a_nameless_adapter_reads_unmeasurable_in_the_report(tmp_path):
    _adapter(tmp_path, "nameless", "AGENT_NAME = compute()")
    code, data = _report_json(tmp_path)
    rows = [(r["agent"], r["state"]) for r in data["rows"]]
    assert rows == [("adapted/nameless (no AGENT_NAME literal)", "UNMEASURABLE")] and code == 2, data


def test_an_adapted_agent_gets_no_transcript_coverage_even_with_session_files(tmp_path):
    _adapter(tmp_path, "mine", 'AGENT_NAME = "my-agent"')
    d = tmp_path / ".claude" / "backups" / ".hook-heartbeat" / "adapted" / "my-agent"
    _w(d / "sessions", "s1", "log", "{} start startup t.jsonl".format(NOW - 9))
    _w(d, "pre-tool", "invoked", "{} 1.5-1".format(NOW - 6))
    _w(d, "neutral-pre-tool", "evaluated", "{} 1.5-1 evaluated allow -".format(NOW - 5))
    _w(d, "pre-tool", "emitted", "{} 1.5-1 allow".format(NOW - 4))
    code, data = _report_json(tmp_path)
    assert "adapted/my-agent" not in data["coverage"] and code == 0, data
    assert [(r["agent"], r["state"]) for r in data["rows"]] == [("adapted/my-agent", "HEALTHY")]
