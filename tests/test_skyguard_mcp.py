"""Plan 5 Task 11: the DevBench guard -- the MCP route and the curl / CLI route to the same two tools.

Two halves, as in test_skyguard_t09:
  - every hand-authored t11 intended row, fired IN-PROCESS (decision, rule trace, exact text), parametrized by row id
    so a mutation can name the one row it must turn red;
  - the helpers, one behaviour per test, and the captured M5b payloads through the adapter.
"""
import json
import os
import sys
import tempfile

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, mcp_rules, paths, rules, shell_rules, shellparse  # noqa: E402
from skyguard.adapters import claude  # noqa: E402

GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
SHELL = {"Bash": "bash", "PowerShell": "powershell"}
FIXTURES = REPO / "tests" / "fixtures" / "payloads" / "claude"


def _rows():
    text = (REPO / "tests" / "guard_corpus" / "intended.jsonl").read_text(encoding="utf-8")
    return [r for r in (json.loads(x) for x in text.split("\n") if x.strip()) if r["task"] == "T11"]


ROWS = _rows()


# Documents lives under USERPROFILE: every test points it at an EMPTY folder (never the machine's own) unless it builds one.
NOHOME = tempfile.mkdtemp(prefix="t11-nohome-")
ENV0 = {"USERPROFILE": NOHOME}


def _home(base, game="Skyrim VR"):
    """A USERPROFILE whose Documents/My Games/<game>/Saves holds Existing.ess; returns the env mapping."""
    d = base / "Documents" / "My Games" / game / "Saves"
    d.mkdir(parents=True)
    (d / "Existing.ess").write_bytes(b"x")
    return {"USERPROFILE": str(base)}


@pytest.fixture(scope="module")
def cfg(tmp_path_factory):
    """A config dir whose Saves folder holds one save, Existing.ess (the corpus's `saves` layout, on disk)."""
    d = tmp_path_factory.mktemp("cfg")
    (d / "Saves").mkdir()
    (d / "Saves" / "Existing.ess").write_bytes(b"x")
    return d.as_posix()


def _roots(layout, cfg_dir):
    env = {"SKYRIM_GAME_ROOT": GAME}
    if layout == "saves":
        env["SKYRIM_CONFIG_DIR"] = cfg_dir
    return paths.roots("C:/t11/Skyrim VR", "", env)


def _audit():
    a = audit.Audit("")
    a.raw = lambda line: None                             # no audit log on disk from a unit test
    return a


def _got(v):
    if v.decision == "allow":
        return ("advise", v.context) if v.context else ("allow", "")
    return (v.decision, v.reason)


def mcp(tool_name, tool_input, roots=None, env=None):
    a = _audit()
    v = mcp_rules.decide(tool_name, tool_input, a, roots=roots or paths.roots("C:/t11/Skyrim VR", "", {}),
                         env=ENV0 if env is None else env)
    return _got(v), {rid for rid, _ in a.fired}


def shell(command, roots=None, shell_name="bash", env=None):
    a = _audit()
    r = roots or paths.roots("C:/t11/Skyrim VR", "", {"SKYRIM_GAME_ROOT": GAME})
    v = shell_rules.decide(command, r, a, shell=shell_name, env=ENV0 if env is None else env)
    return _got(v), {rid for rid, _ in a.fired}


def test_there_are_t11_rows():
    assert len(ROWS) >= 30, len(ROWS)
    assert {r["decision"] for r in ROWS} == {"allow", "ask", "deny"}


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_t11_row(row, cfg, tmp_path):
    p, roots = row["payload"], _roots(row["layout"], cfg)
    env = _home(tmp_path) if row["layout"] == "docs" else None
    if row["hook"] == "protect-mcp.sh":
        got, fired = mcp(p["tool_name"], p["tool_input"], roots, env)
    else:
        got, fired = shell(p["tool_input"]["command"], roots, SHELL[p["tool_name"]], env)
    assert got == (row["decision"], row["text"])
    assert fired == set(row["rules"])


# ------------------------------------------------------------------------------------------ the captured payloads
def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name,want", [
    ("claude-PreToolUse-mcp__devbench-vr__console-1.json", "allow"),
    ("claude-PreToolUse-mcp__devbench-vr__game-1.json", "allow"),
])
def test_the_captured_devbench_payloads_are_allowed(name, want):
    """Task 0's real payloads (a console `help`, a game `list`) go through the adapter and the rule: allow."""
    p = claude.extract(json.dumps(_fixture(name)))
    got, fired = mcp(p["tool_name"], p["mcp_input"])
    assert got == (want, "") and not fired


def test_the_captured_console_payload_with_a_denied_command_is_denied():
    """The same capture, command swapped: proves the real payload SHAPE (no `action` key) reaches the rule."""
    d = _fixture("claude-PreToolUse-mcp__devbench-vr__console-1.json")
    d["tool_input"]["command"] = "smp reset"
    p = claude.extract(json.dumps(d))
    got, fired = mcp(p["tool_name"], p["mcp_input"])
    assert got[0] == "deny" and fired == {"S-DEVBENCH-CRASH"}


# ------------------------------------------------------------------------------------------------ the deny list
def test_the_deny_list_is_data_in_rules_and_is_exactly_smp_reset():
    assert rules.DEVBENCH_DENY == (("smp", "reset"),)


def test_a_command_added_to_the_data_list_is_denied_with_no_code_change(monkeypatch):
    monkeypatch.setattr(rules, "DEVBENCH_DENY", (("smp", "reset"), ("coc", "somewhere")))
    assert mcp_rules.deny_op("COC  Somewhere") == "coc somewhere"
    assert mcp_rules.deny_op("coc elsewhere") == ""


@pytest.mark.parametrize("command,want", [
    ("smp reset", "smp reset"), ("SMP RESET", "smp reset"), ("  smp   reset  ", "smp reset"), ("smp\treset", "smp reset"),
    ("help; smp reset", "smp reset"), ("smp reset\nhelp", "smp reset"),
    ("smp reset 0x14", ""), ("smp resetx", ""), ("smp", ""), ("reset smp", ""), ("", ""), ("smp list", ""),
    ("player.smp reset", ""),                          # a different token: not the measured global form
])
def test_deny_op_compares_whole_commands_on_words(command, want):
    assert mcp_rules.deny_op(command) == want


def test_the_console_save_word_is_case_insensitive(cfg):
    assert mcp("mcp__devbench-vr__console", {"command": "SAVE Existing"}, _roots("saves", cfg))[1] == {"S-DEVBENCH-SAVE"}
    assert mcp("mcp__devbench-vr__console", {"command": "saveall Existing"}, _roots("saves", cfg))[0] == ("allow", "")


def test_a_save_name_is_read_trimmed(cfg):
    # rt-fix-b: the trailing-dot strip trims only the right; a leading space is still the same save.
    got = mcp("mcp__devbench-vr__game", {"action": "save", "name": "  Existing"}, _roots("saves", cfg))
    assert got == (("ask", "Overwrites save Existing.\nYes goes ahead; an overwritten save is not kept."),
                   {"S-DEVBENCH-SAVE"})


def test_a_non_dict_tool_input_is_judged_on_its_strings():
    # rt-fix-b (routes s75): superseded the Task 11 ruling "a non-dict tool_input is not judged". A deny-listed console
    # line is the same command wherever in the call it sits; nothing else in a non-dict input is judged.
    assert mcp("mcp__devbench-vr__console", ["smp reset"])[1] == {"S-DEVBENCH-CRASH"}
    assert mcp("mcp__devbench-vr__console", ["help"])[0] == ("allow", "")
    assert mcp("mcp__devbench-vr__console", None)[0] == ("allow", "")


def test_a_non_string_console_command_is_not_judged():
    assert mcp("mcp__devbench-vr__console", {"command": ["smp", "reset"]})[0] == ("allow", "")
    assert mcp("mcp__devbench-vr__console", {"command": None})[0] == ("allow", "")
    assert mcp("mcp__devbench-vr__console", {})[0] == ("allow", "")


# ------------------------------------------------------------------------------------------------------ the saves
def test_save_exists_reads_the_saves_folder_case_insensitively(cfg):
    r = _roots("saves", cfg)
    assert mcp_rules.save_exists("Existing", r, ENV0) is True
    assert mcp_rules.save_exists("EXISTING", r, ENV0) is True
    assert mcp_rules.save_exists("Fresh", r, ENV0) is False


def test_save_exists_is_unknown_without_a_saves_folder(tmp_path):
    r = paths.roots("C:/t11/x", "", {"SKYRIM_CONFIG_DIR": tmp_path.as_posix()})
    assert mcp_rules.save_exists("Existing", r, ENV0) is None
    assert mcp_rules.save_exists("Existing", paths.roots("C:/t11/x", "", {}), ENV0) is None


def test_an_unreadable_saves_folder_is_unknown_not_a_crash(cfg, monkeypatch):
    def boom(_):
        raise OSError("denied")
    monkeypatch.setattr(os, "listdir", boom)
    assert mcp_rules.save_exists("Existing", _roots("saves", cfg), ENV0) is None


def test_a_scan_lists_each_saves_folder_once(cfg, monkeypatch):
    """A Saves folder holds thousands of files: many save calls in one command must not list it many times."""
    calls = []
    real = os.listdir
    monkeypatch.setattr(os, "listdir", lambda d: calls.append(d) or real(d))
    body = "'{\"action\":\"save\",\"name\":\"N%d\"}'"
    command = "; ".join(f"curl http://127.0.0.1:8921/api/tool/game -d {body % i}" for i in range(40))
    r = _roots("saves", cfg)
    mcp_rules.scan_shell(command, shellparse.parse(command, "bash"), r, ENV0)
    assert len(calls) == 1


def test_the_first_existing_saves_folder_wins(tmp_path, cfg):
    """A declared config dir with no Saves folder is skipped; the first one that HAS a Saves folder is the only one read."""
    empty = tmp_path / "other"
    (empty / "Saves").mkdir(parents=True)
    r = paths.roots("C:/t11/x", "", {"SKYRIM_CONFIG_DIR": empty.as_posix()})
    assert mcp_rules.save_exists("Existing", r, ENV0) is False
    r = r._replace(config_dirs=(tmp_path.as_posix() + "/none",) + r.config_dirs + (cfg,))
    assert mcp_rules.save_exists("Existing", r, ENV0) is False               # `other` has a Saves folder: it wins
    r = r._replace(config_dirs=(tmp_path.as_posix() + "/none", cfg, empty.as_posix()))
    assert mcp_rules.save_exists("Existing", r, ENV0) is True                # `none` skipped, `cfg` wins


def test_documents_is_the_fallback_when_no_config_dir_has_saves(tmp_path):
    env = _home(tmp_path)
    r = paths.roots("C:/t11/x", "", {})
    assert mcp_rules.save_exists("Existing", r, env) is True
    assert mcp_rules.save_exists("Fresh", r, env) is False
    assert mcp_rules.save_exists("Existing", r, ENV0) is None


def test_a_declared_config_dir_outranks_documents(tmp_path, cfg):
    env = _home(tmp_path)
    other = tmp_path / "cfg2" / "Saves"
    other.mkdir(parents=True)
    r = paths.roots("C:/t11/x", "", {"SKYRIM_CONFIG_DIR": (tmp_path / "cfg2").as_posix()})
    assert mcp_rules.save_exists("Existing", r, env) is False


@pytest.mark.parametrize("game", ["Skyrim VR", "Skyrim Special Edition", "Skyrim"])
def test_every_game_folder_name_is_searched(tmp_path, game):
    assert mcp_rules.save_exists("Existing", paths.roots("C:/t11/x", "", {}), _home(tmp_path, game)) is True


def test_vr_outranks_the_other_game_folders(tmp_path):
    env = _home(tmp_path, "Skyrim")
    (tmp_path / "Documents" / "My Games" / "Skyrim VR" / "Saves").mkdir(parents=True)
    assert mcp_rules.save_exists("Existing", paths.roots("C:/t11/x", "", {}), env) is False


@pytest.mark.parametrize("key", ["HOME", "OneDrive", "OneDriveConsumer", "OneDriveCommercial"])
def test_a_redirected_documents_is_found(tmp_path, key):
    env = _home(tmp_path)
    env = {key: env["USERPROFILE"]}
    assert mcp_rules.save_exists("Existing", paths.roots("C:/t11/x", "", {}), env) is True


@pytest.mark.parametrize("args", [{"action": "SAVE", "name": "Existing"}, {"action": " save ", "name": "Existing.ess"}])
def test_the_save_action_is_read_leniently(args, cfg):
    assert mcp("mcp__devbench-vr__game", args, _roots("saves", cfg))[1] == {"S-DEVBENCH-SAVE"}


@pytest.mark.parametrize("action", ["load", "list", "loadLast", "", 7, None])
def test_other_game_actions_never_ask(action, cfg):
    assert mcp("mcp__devbench-vr__game", {"action": action, "name": "Existing"}, _roots("saves", cfg))[0] == ("allow", "")


@pytest.mark.parametrize("name", [None, "", "  ", "a/b", "../x", "a" + chr(92) + "b", "..", ".", "x:y", 7, "a*b", "a?b",
                                  'a"b', "a<b", "a>b", "a|b", "a" + chr(1) + "b"],
                         ids=["none", "empty", "blank", "slash", "dotdot-slash", "backslash", "dotdot", "dot", "colon",
                              "number", "star", "qmark", "quote", "angle", "angle2", "pipe", "control"])
def test_an_unusable_save_name_asks(name, cfg):
    got, fired = mcp("mcp__devbench-vr__game", {"action": "save", "name": name}, _roots("saves", cfg))
    assert fired == {"S-DEVBENCH-SAVE"} and got[1].startswith("Cannot tell which save this overwrites.")


def test_the_ask_is_two_plain_lines_and_does_not_echo_the_call(cfg):
    got, _ = mcp("mcp__devbench-vr__game", {"action": "save", "name": "Existing"}, _roots("saves", cfg))
    assert got[0] == "ask" and len(got[1].split("\n")) == 2 and "action" not in got[1] and "{" not in got[1]


# ---------------------------------------------------------------------------------------------- the MCP wrapper
def test_an_empty_payload_is_refused_not_allowed():
    a = _audit()
    v = mcp_rules.decide("", {}, a)
    assert v.decision == "deny" and "GUARD INERT" in v.reason and a.fired == [("S-INERT-NOINPUT", "deny")]
    assert mcp_rules.decide("unknown", {}, _audit()).decision == "deny"


@pytest.mark.parametrize("tool", ["mcp__other__console", "Bash", "mcp__devbenchless", "mcp", "xmcp__devbench-vr__console"])
def test_only_devbench_servers_are_judged(tool):
    assert mcp(tool, {"command": "smp reset"})[0] == ("allow", "")


@pytest.mark.parametrize("server", ["devbench-vr", "devbench", "devbench-se",
                                    # rt-fix-b (routes s106-s111): any case and any separator inside the name
                                    "DevBench-vr", "dev-bench", "dev_bench.vr", "skyrim-devbench", "plugin_devbench_x"])
def test_any_devbench_server_name_is_judged(server):
    assert mcp(f"mcp__{server}__console", {"command": "smp reset"})[1] == {"S-DEVBENCH-CRASH"}


def test_run_py_protect_mcp_end_to_end(tmp_path):
    """The engine entry point itself (what the wrapper runs): a verdict as the runtime's JSON, exit 0."""
    import subprocess
    d = _fixture("claude-PreToolUse-mcp__devbench-vr__console-1.json")
    d["tool_input"] = {"action": "exec", "command": "smp reset"}
    r = subprocess.run([sys.executable, str(REPO / "tools" / "skyguard" / "run.py"), "claude", "protect-mcp",
                        "--root", str(tmp_path), "--project", str(tmp_path)],
                       input=json.dumps(d).encode("utf-8"), capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr
    h = json.loads(r.stdout)["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny" and h["permissionDecisionReason"].startswith("Blocked: 'smp reset'")


# ------------------------------------------------------------------------------------------- curl / CLI parsing
CONSOLE = "http://127.0.0.1:8921/api/tool/console"


RESET_BODY = "'{\"command\":\"smp reset\"}'"
SENDS = {
    "d": f"curl {CONSOLE} -d {RESET_BODY}",
    "data": f"curl {CONSOLE} --data {RESET_BODY}",
    "data-raw": f"curl {CONSOLE} --data-raw {RESET_BODY}",
    "data-binary": f"curl {CONSOLE} --data-binary {RESET_BODY}",
    "data-ascii": f"curl {CONSOLE} --data-ascii {RESET_BODY}",
    "data-urlencode": f"curl {CONSOLE} --data-urlencode {RESET_BODY}",
    "json-flag": f"curl {CONSOLE} --json {RESET_BODY}",
    "short-cluster": f"curl -sd {RESET_BODY} {CONSOLE}",                 # short flags ending in d
    "attached-body": f"curl {CONSOLE} -d'{{\"command\":\"smp reset\"}}'",      # the body attached to the flag
    "two-bodies": f"curl -X POST -H 'Content-Type: application/json' {CONSOLE} -d '{{\"command\":\"help\"}}' -d {RESET_BODY}",
    "query-string": f"curl {CONSOLE}?x=1 -d {RESET_BODY}",
    "trailing-slash": f"curl {CONSOLE}/ -d {RESET_BODY}",
    "other-host-port": f"curl http://localhost:9000/api/tool/console -d {RESET_BODY}",
    "ipv6-curl-exe": f"curl.exe http://[::1]:8920/api/tool/console -d {RESET_BODY}",
    "path-to-curl": f"/usr/bin/curl {CONSOLE} -d {RESET_BODY}",
    "env-wrapper": f"env X=1 curl {CONSOLE} -d {RESET_BODY}",
    "timeout-wrapper": f"timeout 5 curl {CONSOLE} -d {RESET_BODY}",
    "bash-c": f"bash -c \"curl {CONSOLE} -d '{{\\\"command\\\":\\\"smp reset\\\"}}'\"",
    "after-and": f"echo hi && curl {CONSOLE} -d {RESET_BODY}",
    "full-body": f"curl {CONSOLE} -d '{{\"action\":\"exec\",\"command\":\"smp reset\",\"capture\":true}}'",
    # rt-fix-b: superseded the Task 11 ruling "not the console tool" (was PASSES[papyrus-tool]); see the note there.
    "papyrus-tool": f"curl http://127.0.0.1:8921/api/tool/papyrus -d {RESET_BODY}",
}


@pytest.mark.parametrize("command", SENDS.values(), ids=list(SENDS))
def test_curl_forms_that_send_the_denied_command(command):
    assert shell(command)[1] == {"S-DEVBENCH-CRASH"}, command


PASSES = {
    "body-file": f"curl {CONSOLE} -d @body.json",
    "body-stdin": f"curl {CONSOLE} --data-binary @-",
    "body-variable": f"curl {CONSOLE} -d \"$BODY\"",
    "targeted-reset": f"curl {CONSOLE} -d '{{\"command\":\"smp reset 0x14\"}}'",
    "ordinary-command": f"curl {CONSOLE} -d '{{\"command\":\"help\"}}'",
    "no-body": f"curl {CONSOLE}",
    # rt-fix-b: `papyrus-tool` (a reset body posted to the papyrus tool) moved to SENDS. The guard does not model which
    # keys each DevBench tool reads (a papyrus call runs console lines through ExecuteCommand-style functions), so a
    # deny-listed line anywhere in a DevBench call denies. Corpus row rtb-curl-papyrus-any-key records the ruling.
    "health-url": f"curl -d {RESET_BODY} http://127.0.0.1:8921/api/health",
    "other-api": f"curl -d {RESET_BODY} http://example.com/api/other",
    "body-file-unnamed": f"echo hi > body.txt; curl {CONSOLE} -d @body.txt",                                    # the gap
    "wget": f"wget -qO- {CONSOLE}",
}


@pytest.mark.parametrize("command", PASSES.values(), ids=list(PASSES))
def test_curl_forms_that_do_not_send_it(command):
    assert shell(command)[0] == ("allow", ""), command


def test_a_damaged_json_body_is_still_read_for_the_action_and_name(cfg):
    """A trailing comma makes the body invalid JSON; the action and name are still pulled out by pattern, so a save
    through it is still judged (a bare reset in it is caught by the unreadable-body rule, tested below)."""
    body = "'{\"action\":\"save\",\"name\":\"Existing\",}'"
    got, fired = shell(f"curl http://127.0.0.1:8921/api/tool/game -d {body}", _roots("saves", cfg))
    assert fired == {"S-DEVBENCH-SAVE"} and got[1].startswith("Overwrites save Existing.")


def test_a_damaged_body_with_a_bad_escape_does_not_crash():
    assert shell(f"curl {CONSOLE} -d '{{\"command\":\"help" + chr(92) + "q\",}}'")[0] == ("allow", "")


def test_a_json_body_that_is_not_an_object_is_not_a_call():
    assert shell(f"curl {CONSOLE} -d '[\"help\"]'")[0] == ("allow", "")
    assert shell("bash tools/devbench-cli.sh tool console '[1]'")[0] == ("allow", "")


def test_the_cli_joins_its_arguments_like_the_script_does():
    assert shell("bash tools/devbench-cli.sh exec smp reset")[1] == {"S-DEVBENCH-CRASH"}
    assert shell("bash tools/devbench-cli.sh exec smp reset 0x14")[0] == ("allow", "")
    assert shell("./tools/devbench-cli.sh exec 'SMP RESET'")[1] == {"S-DEVBENCH-CRASH"}
    assert shell("devbench-cli exec smp reset")[1] == {"S-DEVBENCH-CRASH"}


@pytest.mark.parametrize("command", [
    "bash tools/devbench-cli.sh tool console 'not json'",
    "bash tools/devbench-cli.sh tool console",
    "bash tools/devbench-cli.sh exec",
    "bash tools/devbench-cli.sh call Utility GetCurrentGameTime",
    "bash tools/devbench-cli.sh health",
])
def test_cli_forms_that_carry_no_readable_denied_call(command):
    assert shell(command)[0] == ("allow", ""), command


def test_a_url_held_in_a_same_command_variable_is_resolved():
    assert shell(f"U={CONSOLE}; curl \"$U\" -d '{{\"command\":\"smp reset\"}}'")[1] == {"S-DEVBENCH-CRASH"}


def test_the_powershell_cmdlets_are_read():
    for cmd in (f"Invoke-RestMethod -Uri {CONSOLE} -Method Post -Body '{{\"command\":\"smp reset\"}}'",
                f"irm {CONSOLE} -Body '{{\"command\":\"smp reset\"}}'",
                f"Invoke-WebRequest -Uri {CONSOLE} -Body '{{\"command\":\"smp reset\"}}' -Method POST"):
        assert shell(cmd, shell_name="powershell")[1] == {"S-DEVBENCH-CRASH"}, cmd
    assert shell(f"irm {CONSOLE}", shell_name="powershell")[0] == ("allow", "")


def test_a_command_that_names_neither_is_not_scanned(monkeypatch):
    """The prefilter: without `api/tool` or `devbench` in the text the scan returns before reading any exec."""
    called = []
    monkeypatch.setattr(mcp_rules.deletes, "substituted", lambda e: called.append(e) or e)
    mcp_rules.scan_shell("curl http://x/y -d smp", shellparse.parse("curl http://x/y -d smp"), None)
    assert not called
    mcp_rules.scan_shell("curl http://x/api/tool/y -d smp", shellparse.parse("curl http://x/api/tool/y -d smp"), None)
    assert called


# ----------------------------------------------------------------------------------- not safe by default (R4)
def test_an_unreadable_command_naming_the_endpoint_and_the_denied_command_denies():
    assert shell(f"curl {CONSOLE} -d '{{\"command\":\"smp reset\"}}' && (echo")[1] == {"S-DEVBENCH-CRASH"}


def test_an_unreadable_command_that_only_names_one_of_them_does_not():
    assert shell("grep 'smp reset' notes.txt && (echo")[0] == ("allow", "")
    assert shell("grep 'smp reset' docs/devbench.md && (echo")[0] == ("allow", "")      # names devbench, not the console
    assert shell(f"curl {CONSOLE} -d 'help' && (echo")[0] == ("allow", "")


def test_an_opaque_program_next_to_the_endpoint_and_the_denied_command_denies():
    assert shell(f"$CURL {CONSOLE} -d '{{\"command\":\"smp reset\"}}'")[1] == {"S-DEVBENCH-CRASH"}


def test_interpreter_code_is_a_ledgered_gap_not_a_text_match():
    """Priced over history, the co-occurrence of the endpoint and `smp reset` in interpreter code hit only scripts that
    TEST this rule (strings, not calls). It was dropped; the gap is recorded in mcp_rules.py and the pricing ledger."""
    code = f"python -c \"import requests; requests.post('{CONSOLE}', json={{'command': 'smp reset'}})\""
    assert shell(code)[0] == ("allow", "")
    assert shell("python -c \"print('smp reset')\"")[0] == ("allow", "")


def test_text_that_merely_contains_the_words_is_allowed():
    for cmd in (f"echo 'curl {CONSOLE} -d smp reset' >> notes.txt",
                "git commit -m 'devbench: deny smp reset'",
                "grep -rn 'smp reset' docs",
                "cat docs/devbench.md"):
        assert shell(cmd)[0] == ("allow", ""), cmd


def test_a_deny_outranks_an_ask_in_one_command(cfg):
    r = _roots("saves", cfg)
    cmd = ("bash tools/devbench-cli.sh tool game '{\"action\":\"save\",\"name\":\"Existing\"}'; "
           "bash tools/devbench-cli.sh exec 'smp reset'")
    assert shell(cmd, r)[1] == {"S-DEVBENCH-CRASH"}


def test_a_resaver_ask_outranks_a_devbench_ask(cfg):
    r = _roots("saves", cfg)
    cmd = ("bash tools/resaver-cli.sh clean x.ess --apply; "
           "bash tools/devbench-cli.sh tool game '{\"action\":\"save\",\"name\":\"Existing\"}'")
    assert shell(cmd, r)[1] == {"S-RESAVER-APPLY"}


# ------------------------------------------------------------------ fix round 1: a body with no definite command
@pytest.mark.parametrize("tail", ['smp reset"', "smp reset", "smp reset)", "smp reset;", "smp reset > x", "smp reset'",
                                  "smp reset" + chr(92) + '"', "smp reset|", "smp reset]", "smp reset`", "smp reset&", "SMP  RESET",
                                  "smp reset}", "smp reset<x", "smp reset  "],
                         ids=["quote", "end", "paren", "semicolon", "redirect", "squote", "escaped-quote", "pipe",
                              "bracket", "backtick", "amp", "case", "brace", "input", "trailing-space"])
def test_an_unreadable_console_body_naming_a_bare_reset_denies(tail):
    """What follows `smp reset` in a body the guard cannot parse decides whether it is the bare command. Tested on the
    recogniser itself: a whole command with a stray quote would be denied by the parse-failure fallback instead."""
    assert mcp_rules._unreadable_names_reset("x " + tail), tail


def test_a_bare_reset_in_an_unreadable_body_denies_end_to_end():
    assert shell(f"curl -s {CONSOLE} -d 'command=smp reset'")[1] == {"S-DEVBENCH-CRASH"}


@pytest.mark.parametrize("text", ["smp reset 0x14", "smp resetx", "xsmp reset", "smp list"])
def test_an_unreadable_console_body_naming_something_else_is_allowed(text):
    assert shell(f"curl -s {CONSOLE} -d 'command={text}'")[0] == ("allow", "")


def test_a_body_whose_command_is_not_a_string_is_unreadable_not_ignored():
    got = shell(f"curl -s {CONSOLE} -d '{{\"action\":\"exec\",\"command\":[\"smp reset\"]}}'")
    assert got[1] == {"S-DEVBENCH-CRASH"}
    # rt-fix-b: a list element that is the whole line is judged as a string anyway, so the case this test guards is a
    # readable body whose command is no string while the command text names a bare reset elsewhere.
    got = shell(f"curl -s {CONSOLE} -d '{{\"action\":\"exec\",\"command\":1}}' -H 'X-Note: smp reset'")
    assert got[1] == {"S-DEVBENCH-CRASH"}


def test_an_unreadable_body_is_only_judged_for_the_console_tool():
    assert shell("curl -s http://127.0.0.1:8921/api/tool/papyrus -d 'command=smp reset'")[0] == ("allow", "")
    assert shell("bash tools/devbench-cli.sh tool papyrus 'command=smp reset'")[0] == ("allow", "")


def test_the_cli_tool_console_with_an_unreadable_body_naming_a_reset_denies():
    assert shell("bash tools/devbench-cli.sh tool console 'command=smp reset'")[1] == {"S-DEVBENCH-CRASH"}
    assert shell("bash tools/devbench-cli.sh tool console 'command=help'")[0] == ("allow", "")
    assert shell("bash tools/devbench-cli.sh tool CONSOLE 'command=smp reset'")[1] == {"S-DEVBENCH-CRASH"}


def test_the_whole_command_is_read_for_the_reset_not_just_the_exec():
    assert shell(f"B=$(printf 'smp reset'); curl -s {CONSOLE} -d \"$B\"")[1] == {"S-DEVBENCH-CRASH"}


def test_a_readable_body_is_not_second_guessed_by_the_text():
    """A body that parses to a definite, harmless command stays allowed even if the rest of the command mentions it."""
    assert shell(f"echo 'smp reset is blocked'; curl -s {CONSOLE} -d '{{\"command\":\"help\"}}'")[0] == ("allow", "")


def test_the_console_tool_name_is_case_insensitive_on_the_wire():
    assert shell("curl -s http://127.0.0.1:8921/api/tool/Console -d 'command=smp reset'")[1] == {"S-DEVBENCH-CRASH"}


def test_an_empty_documents_variable_is_not_read_relative_to_the_cwd(tmp_path, monkeypatch):
    _home(tmp_path)                                           # Documents/My Games/Skyrim VR/Saves under tmp_path
    monkeypatch.chdir(tmp_path)
    env = {"USERPROFILE": "", "OneDrive": ""}
    assert mcp_rules.save_exists("Existing", paths.roots("C:/t11/x", "", {}), env) is None
