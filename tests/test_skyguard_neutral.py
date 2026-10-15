"""The neutral engine entry: strict request parsing, one verdict out, failures are denials."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import engine, neutral, paths  # noqa: E402
from skyguard.audit import Audit  # noqa: E402
from skyguard.verdict import Verdict  # noqa: E402

_BASE = '{"v":1,"event":"pre_tool",'
DEEP = 100000   # far past the json module's C recursion limit


def req(**kw):
    d = {"v": 1, "event": "pre_tool", "kind": "write", "paths": ["/x/Data/Decoy.esp"]}
    d.update(kw)
    return json.dumps(d)


GOOD = [
    req(),
    req(kind="shell", paths=[], command="echo hi", shell=None),
    req(kind="shell", paths=[], command="Get-Date", shell="powershell"),
    req(kind="other", paths=[], tool_name="web_fetch"),
    req(kind="mcp", paths=[], mcp_tool="mcp__x__y", mcp_args="{}"),
    req(ops=["add"]),
]

BAD = {  # row -> (request text, fragment the refusal must name)
    "not-json": ("not json", "not JSON"),
    "array": ("[]", "JSON object"),
    "bom-less-empty": ("", "not JSON"),
    "unknown-key": (req(path="/x"), "unknown field"),
    "wrong-version": (req(v=2), "v must be 1"),
    "bool-version": (req(v=True), "v must be 1"),
    "string-version": (req(v="1"), "v must be 1"),
    "wrong-event": (req(event="post_tool"), "event must be pre_tool"),
    "bad-kind": (req(kind="file"), "kind must be"),
    "bad-shell": (req(kind="shell", shell="zsh", paths=[]), "shell must be"),
    "shell-on-write": (req(shell="bash"), "shell is only for kind shell"),
    "paths-not-list": (req(paths="/x"), "paths must be a list"),
    "path-not-string": (req(paths=[3]), "paths must be a list of non-empty strings"),
    "empty-path": (req(paths=[""]), "paths must be a list of non-empty strings"),
    "ops-length": (req(ops=["add", "add"]), "ops must be parallel"),
    "bad-op": (req(ops=["rewrite"]), "ops must be parallel"),
    "command-not-string": (req(kind="shell", paths=[], command=7), "command must be a string"),
    # Fix round 1
    "float-version": (req(v=1.0), "v must be 1"),
    "duplicate-paths": (_BASE + '"kind":"write","paths":["/x/a.esp"],"paths":["/x/b.txt"]}', "duplicate field"),
    "duplicate-kind": (_BASE + '"kind":"other","paths":["/x/a.esp"],"kind":"write"}', "duplicate field"),
    "duplicate-nested": (_BASE + '"kind":"write","paths":[{"a":1,"a":2}]}', "duplicate field"),
    "duplicate-in-mcp-args": (req(kind="mcp", paths=[], mcp_tool="mcp__devbench-vr__console",
                                  mcp_args='{"command":"help","command":"smp reset"}'), "duplicate field"),
    "deep-request": (_BASE + '"kind":"write","paths":' + "[" * DEEP + "]" * DEEP + "}", "too deeply nested"),
    "deep-mcp-args": (req(kind="mcp", paths=[], mcp_tool="mcp__devbench-vr__console",
                          mcp_args="[" * DEEP + "]" * DEEP), "too deeply nested"),
    "mcp-args-not-object": (req(kind="mcp", paths=[], mcp_tool="mcp__x__y", mcp_args="[1]"),
                            "mcp_args must be"),
    "mcp-args-not-json": (req(kind="mcp", paths=[], mcp_tool="mcp__x__y", mcp_args="{"), "mcp_args must be"),
    "agent-claims-codex": (req(agent="codex"), "agent must not be"),
    "agent-claims-claude": (req(agent="claude"), "agent must not be"),
}


@pytest.mark.parametrize("text", GOOD)
def test_valid_requests_parse(text):
    assert neutral.parse(text).event == "pre_tool"


@pytest.mark.parametrize("row", sorted(BAD))
def test_malformed_requests_are_refused_with_the_reason(row):
    text, fragment = BAD[row]
    with pytest.raises(neutral.BadRequest) as e:
        neutral.parse(text)
    assert fragment in str(e.value), f"{row}: refusal was {e.value!r}"


def test_ops_default_to_update_so_the_engine_takes_a_backup():
    assert neutral.parse(req(paths=["/a", "/b"])).ops == ("update", "update")


def test_nul_and_bom_are_tolerated_like_the_other_adapters():
    assert neutral.parse("\ufeff" + req().replace("{", "{\x00", 1)).kind == "write"


def test_render_is_ascii_one_line_and_total():
    v = Verdict("deny", "caf\u00e9 \u2013 no", "", "evaluated", "write.DENY_PLUGIN")
    out = neutral.render(v)
    out.encode("ascii")
    assert "\n" not in out and json.loads(out)["decision"] == "deny"
    bad = neutral.render(Verdict("maybe", "r"))
    assert json.loads(bad)["decision"] == "deny" and json.loads(bad)["status"] == "failed"


def test_handle_denies_a_decoy_plugin_write(tmp_path):
    text, v = neutral.handle(req(paths=[str(tmp_path / "Data" / "Decoy.esp")]), str(tmp_path),
                             lambda: None, Audit(str(tmp_path)))
    assert text.startswith("SKYGUARD-OK\n") and v.decision == "deny"
    assert json.loads(text.split("\n", 1)[1])["code"].startswith("write.")


def test_handle_turns_a_bad_request_into_a_failed_deny_never_a_raise(tmp_path):
    text, v = neutral.handle("{", str(tmp_path), lambda: None, Audit(str(tmp_path)))
    assert v.decision == "deny" and v.status == "failed" and v.code == "neutral.BAD_REQUEST"
    assert "NOTHING" in v.reason


def test_a_write_request_without_paths_is_denied_by_the_engine_not_allowed(tmp_path):
    _, v = neutral.handle(req(paths=[]), str(tmp_path), lambda: None, Audit(str(tmp_path)))
    assert v.decision == "deny" and "checked NOTHING" in v.reason


# ---- through the real CLI -------------------------------------------------------------------------
def _cli(root, stdin: bytes, *extra):
    return subprocess.run(
        [sys.executable, str(REPO / "tools" / "skyguard" / "run.py"), "neutral", "pre-tool",
         "--root", str(root), "--root-win", str(root), "--project", str(root), *extra],
        input=stdin, capture_output=True, timeout=60)


def test_cli_decoy_write_is_denied_with_marker_and_exit_zero(tmp_path):
    cp = _cli(tmp_path, req(paths=[str(tmp_path / "Data" / "Decoy.esp")]).encode())
    assert cp.returncode == 0 and cp.stdout.startswith(b"SKYGUARD-OK\n")
    body = json.loads(cp.stdout.split(b"\n", 1)[1])
    assert body["v"] == 1 and body["decision"] == "deny"


def test_cli_benign_write_is_allowed(tmp_path):
    cp = _cli(tmp_path, req(paths=[str(tmp_path / "notes.txt")]).encode())
    assert json.loads(cp.stdout.split(b"\n", 1)[1])["decision"] == "allow"


def test_cli_rejects_a_role_other_than_pre_tool_with_exit_1_and_no_stdout(tmp_path):
    cp = subprocess.run([sys.executable, str(REPO / "tools" / "skyguard" / "run.py"), "neutral",
                         "protect-bash", "--project", str(tmp_path)], input=b"{}", capture_output=True, timeout=60)
    assert cp.returncode == 1 and cp.stdout == b""


def test_cli_writes_telemetry_when_asked(tmp_path):
    hb = tmp_path / "hb"
    _cli(tmp_path, req(paths=[str(tmp_path / "n.txt")]).encode(), "--invocation", "1.1-a", "--telemetry", str(hb))
    # Its own role name: a neutral record must never land on (or be read as) the Codex hook's pre-tool record.
    assert (hb / "neutral-pre-tool.evaluated").is_file()
    assert not (hb / "pre-tool.evaluated").exists()


# ---- fix round 1 -----------------------------------------------------------------------------------
def _roots(tmp_path):
    return lambda: paths.roots(tmp_path.as_posix(), str(tmp_path), {})


def _unhooked_log(tmp_path):
    return tmp_path / ".claude" / "backups" / engine.UNHOOKED_LOG


def test_a_judged_neutral_shell_call_is_not_logged_as_unhooked(tmp_path):
    (tmp_path / "Data").mkdir()
    for cmd, want in (('echo x > "{}"'.format((tmp_path / "Data" / "Decoy.esp").as_posix()), "deny"),
                      ("Get-Date", "allow")):
        _, v = neutral.handle(req(kind="shell", paths=[], command=cmd, cwd=tmp_path.as_posix()), str(tmp_path),
                              _roots(tmp_path), Audit(str(tmp_path)))
        assert v.decision == want, (cmd, v)
    assert not _unhooked_log(tmp_path).exists()


def test_the_devbench_crash_command_is_denied_through_the_mcp_rules(tmp_path):
    args = json.dumps({"action": "exec", "command": "smp reset"})
    _, v = neutral.handle(req(kind="mcp", paths=[], mcp_tool="mcp__devbench-vr__console", mcp_args=args),
                          str(tmp_path), _roots(tmp_path), Audit(str(tmp_path)))
    # The registry codes this rule under the hook that owns it (shell.); the rule id is what matters.
    assert v.decision == "deny" and v.code.endswith(".S-DEVBENCH-CRASH"), v


def test_a_harmless_devbench_call_and_another_mcp_server_are_allowed(tmp_path):
    for tool, args in (("mcp__devbench-vr__console", {"action": "exec", "command": "help"}),
                       ("mcp__other__thing", {"command": "smp reset"})):
        _, v = neutral.handle(req(kind="mcp", paths=[], mcp_tool=tool, mcp_args=json.dumps(args)),
                              str(tmp_path), _roots(tmp_path), Audit(str(tmp_path)))
        assert v.decision == "allow", (tool, v)
    assert not _unhooked_log(tmp_path).exists()


@pytest.mark.parametrize("tool", ["mcp__other__thing", "mcp__filesystem__write_file", "not_three_parts",
                                  "mcp__devbench"])
def test_an_mcp_call_the_rules_do_not_judge_is_labelled_unjudged_not_evaluated(tmp_path, tool):
    """Final review M6: a call to a non-DevBench server came back status 'evaluated', so an adapter logging status
    would record it as checked. It is allowed, as before, but labelled like kind other."""
    text, v = neutral.handle(req(kind="mcp", paths=[], mcp_tool=tool, mcp_args='{"command": "smp reset"}'),
                             str(tmp_path), _roots(tmp_path), Audit(str(tmp_path)))
    assert (v.decision, v.status) == ("allow", "partial"), v
    assert (v.code, v.context) == ("neutral.UNJUDGED_MCP", neutral.UNJUDGED_MCP)
    assert neutral.UNJUDGED_MCP.startswith("NOT JUDGED")
    assert json.loads(text.splitlines()[-1])["status"] == "partial"


@pytest.mark.parametrize("tool", ["mcp__dev-bench__console", "mcp__DevBench-vr__console", "mcp__skyrim-devbench__console"])
def test_a_devbench_server_by_any_spelling_is_judged_not_labelled_unjudged(tmp_path, tool):
    """Merge of Plan 5 (rt-fix-b is_devbench_server) into Plan 7: every spelling the MCP rules judge is judged on the
    neutral entry too, so its NOT JUDGED label and the decision can never disagree."""
    _, v = neutral.handle(req(kind="mcp", paths=[], mcp_tool=tool, mcp_args='{"command": "smp reset"}'),
                          str(tmp_path), _roots(tmp_path), Audit(str(tmp_path)))
    assert (v.decision, v.status) == ("deny", "evaluated"), v


def test_a_devbench_call_is_still_evaluated(tmp_path):
    _, v = neutral.handle(req(kind="mcp", paths=[], mcp_tool="mcp__devbench-vr__console",
                              mcp_args=json.dumps({"action": "exec", "command": "help"})),
                          str(tmp_path), _roots(tmp_path), Audit(str(tmp_path)))
    assert (v.decision, v.status) == ("allow", "evaluated"), v


def test_an_mcp_request_without_a_tool_name_is_a_failed_deny(tmp_path):
    _, v = neutral.handle(req(kind="mcp", paths=[]), str(tmp_path), _roots(tmp_path), Audit(str(tmp_path)))
    assert (v.decision, v.status) == ("deny", "failed"), v


def test_kind_other_is_allowed_but_never_reads_as_a_verdict(tmp_path):
    text, v = neutral.handle(req(kind="other", paths=[], tool_name="web_fetch"), str(tmp_path),
                             _roots(tmp_path), Audit(str(tmp_path)))
    assert (v.decision, v.code, v.context) == ("allow", "neutral.UNJUDGED", neutral.UNJUDGED)
    assert neutral.UNJUDGED == "NOT JUDGED: this tool kind is not guarded; WARN AND ALLOW"
    assert json.loads(text.split("\n", 1)[1])["code"] == "neutral.UNJUDGED"
    assert not _unhooked_log(tmp_path).exists()


@pytest.mark.parametrize("row", ["no-command", "blank-command", "no-root"])
def test_inert_shell_refusals_name_no_other_agents_hook(tmp_path, row):
    command = {"no-command": "", "blank-command": "   ", "no-root": "rm -rf x"}[row]
    roots = (lambda: paths.Roots("", "")) if row == "no-root" else _roots(tmp_path)
    _, v = neutral.handle(req(kind="shell", paths=[], command=command), str(tmp_path), roots, Audit(str(tmp_path)))
    assert (v.decision, v.status) == ("deny", "failed"), v
    assert v.code in ("shell.NO_COMMAND_DENY", "shell.NO_ROOT_DENY"), v
    assert "protect-bash" not in v.reason and "Codex" not in v.reason and "GUARD INERT" in v.reason
    assert len(v.reason.splitlines()) <= 2


def test_the_engine_translate_refusal_names_no_agent():
    assert "Codex" not in engine.INERT_TRANSLATE and "protect-bash" not in engine.INERT_TRANSLATE


def test_invalid_utf8_is_a_bad_request_not_a_replacement_character(tmp_path):
    raw = (_BASE + '"kind":"write","paths":["/x/a\xff.txt"]}').encode("latin-1")
    text, v = neutral.handle(raw, str(tmp_path), _roots(tmp_path), Audit(str(tmp_path)))
    assert (v.decision, v.code) == ("deny", "neutral.BAD_REQUEST") and "UTF-8" in v.reason, v


def test_cli_invalid_utf8_is_a_bad_request(tmp_path):
    cp = _cli(tmp_path, (_BASE + '"kind":"write","paths":["/x/a\xff.txt"]}').encode("latin-1"))
    assert cp.returncode == 0 and cp.stdout.startswith(b"SKYGUARD-OK\n"), cp.stderr
    body = json.loads(cp.stdout.split(b"\n", 1)[1])
    assert (body["decision"], body["code"]) == ("deny", "neutral.BAD_REQUEST")


def test_cli_deeply_nested_request_is_a_bad_request_not_a_crash(tmp_path):
    cp = _cli(tmp_path, (_BASE + '"kind":"write","paths":' + "[" * DEEP + "]" * DEEP + "}").encode())
    assert cp.returncode == 0, cp.stderr[-300:]
    assert json.loads(cp.stdout.split(b"\n", 1)[1])["code"] == "neutral.BAD_REQUEST"


def test_cli_devbench_crash_command_is_denied(tmp_path):
    args = json.dumps({"action": "exec", "command": "smp reset"})
    cp = _cli(tmp_path, req(kind="mcp", paths=[], mcp_tool="mcp__devbench-vr__console", mcp_args=args).encode())
    body = json.loads(cp.stdout.split(b"\n", 1)[1])
    assert body["decision"] == "deny" and body["code"].endswith(".S-DEVBENCH-CRASH"), body


def test_an_unknown_key_is_named_on_one_short_line():
    """A key is adapter input: a newline or a huge name in it must not make the refusal multi-line or long."""
    with pytest.raises(neutral.BadRequest) as e:
        neutral.parse(req(**{"evil\nkey" + "x" * 500: 1}))
    assert "\n" not in str(e.value) and len(str(e.value)) < 120


def test_cli_unreadable_paths_file_is_a_neutral_failed_deny(tmp_path):
    """The env file exists but cannot be read (here: not UTF-8; paths.read_config raises). The refusal must not
    name another agent's hook (run.ENV_UNREADABLE says protect-bash.sh, the Codex one says the Codex hook)."""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "skyrim-paths.env").write_bytes(b"SKYRIM_ROOT=\xff\xfe\xff\n")
    cp = _cli(tmp_path, req(kind="shell", paths=[], command="echo hi").encode())
    assert cp.returncode == 0, cp.stderr
    body = json.loads(cp.stdout.split(b"\n", 1)[1])
    assert body["decision"] == "deny" and body["status"] == "failed" and body["code"] == "run.ENV_UNREADABLE"
    assert body["reason"] == neutral.ENV_UNREADABLE
    assert "protect-bash" not in body["reason"] and "Codex" not in body["reason"]


def test_the_engine_still_parses_on_python_3_8():
    import ast
    src = (REPO / "tools" / "skyguard" / "neutral.py").read_text(encoding="utf-8")
    ast.parse(src, feature_version=(3, 8))
