"""Drive the shipped V1 transport under Node; this does not qualify a running host."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))


DRIVER = r'''
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const [plugin, cases] = process.argv.slice(2);
const data = JSON.parse(readFileSync(cases, 'utf8'));
const mod = await import(pathToFileURL(plugin).href);
if (Object.keys(mod).length !== 1) throw Error('Expected one plugin export');
const hooks = await Object.values(mod)[0]({directory: data.directory});
const results = [];
for (const step of data.steps) {
  const output = step.output || {};
  try { await hooks[step.hook](step.input, output); results.push({output}); }
  catch (error) { results.push({error: error.message, output}); }
}
process.stdout.write(JSON.stringify(results));
'''


def run_plugin(tmp_path, steps, env=None):
    node = shutil.which("node")
    if not node:
        if os.environ.get("CI"):
            pytest.fail("Node is required for the OpenCode transport gate")
        pytest.skip("Node absent; plugin transport untested")
    root = tmp_path / "toolkit"
    if not root.exists():
        root.mkdir()
        for name in ("skyguard", "modding_harness"):
            shutil.copytree(REPO / "tools" / name, root / "tools" / name,
                            ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / ".opencode", root / ".opencode")
    driver = tmp_path / "drive.mjs"
    driver.write_text(DRIVER)
    case = tmp_path / "case.json"
    case.write_text(json.dumps({"directory": str(root), "steps": steps}))
    child_env = dict(os.environ, SKYRIM_PYTHON=sys.executable)
    child_env.update(env or {})
    result = subprocess.run([node, str(driver), str(root / ".opencode/plugins/skyguard.js"), str(case)],
                            capture_output=True, text=True, timeout=60,
                            env=child_env)
    assert result.returncode == 0, result.stderr
    return root, json.loads(result.stdout)


def before(tool, args, call="c"):
    return {"hook": "tool.execute.before", "input": {"tool": tool, "sessionID": "s", "callID": call},
            "output": {"args": args}}


def after(tool, args, call="c"):
    return {"hook": "tool.execute.after", "input": {"tool": tool, "sessionID": "s", "callID": call, "args": args},
            "output": {"output": "done"}}


def test_shipped_plugin_preserves_pairs_refuses_plugins_and_correlates_unknowns(tmp_path):
    root, _ = run_plugin(tmp_path, [])
    saves = tmp_path / "config/Saves"
    saves.mkdir(parents=True)
    (root / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + saves.parent.as_posix() + "'\n")
    for name in ("save.ess", "save.skse"):
        (saves / name).write_bytes(name.encode())
    steps = [before("write", {"filePath": str(saves / "save.ess")}),
             after("write", {"filePath": str(saves / "save.ess")}),
             before("write", {"filePath": str(root / "plugin.esp")})]
    for number in range(5):
        steps += [before("future-agent-tool", {}, str(number)), after("future-agent-tool", {}, str(number))]
    _, result = run_plugin(tmp_path, steps)
    assert "error" not in result[0], result
    assert "Save backup verified" in result[1]["output"]["output"]
    assert "validation did not run" in result[1]["output"]["output"]
    assert "error" in result[2], result
    notes = [row["output"]["output"] for row in result[4::2]]
    assert ["WARN AND ALLOW" in note for note in notes] == [True, True, False, True, False]
    from modding_harness.telemetry import Telemetry
    from skyguard import save_store
    assert Telemetry(root).report("s")["correlated_calls"] == 6
    transactions = list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))
    assert len(transactions) == 1
    record = save_store.store(root).load(transactions[0].stem)
    assert len(record["groups"][0]["members"]) == 2


def test_missing_adapter_refuses_known_tools_and_unknown_receipt_failure_warns(tmp_path):
    root, _ = run_plugin(tmp_path, [])
    (root / ".opencode/hooks/skyguard_adapter.py").unlink()
    _, result = run_plugin(tmp_path, [before("write", {"filePath": "harmless.txt"}),
                                    before("future-tool", {}), after("future-tool", {})])
    assert "GUARD INERT" in result[0]["error"]
    assert "error" not in result[1]
    assert "coverage receipt unavailable" in result[2]["output"]["output"]


@pytest.mark.parametrize("failure", ["no-python", "no-adapter", "nonzero", "bad-json", "timeout"])
def test_profile_known_devbench_tools_refuse_failed_transport(tmp_path, failure):
    root, _ = run_plugin(tmp_path, [])
    adapter = root / ".opencode/hooks/skyguard_adapter.py"
    body = {"nonzero": "print('SKYGUARD-OPENCODE {\"decision\":\"allow\"}'); raise SystemExit(7)\n",
            "bad-json": "print('SKYGUARD-OPENCODE broken')\n", "timeout": "import time; time.sleep(5)\n"}
    if failure == "no-adapter":
        adapter.unlink()
    elif failure in body:
        adapter.write_text(body[failure])
    known = ["write", "mcp__devbench-vr__console", "mcp__my__Dev-Bench__game"]
    unknown = ["mcp__future__tool", "future-tool"]
    steps = [step for tool in known + unknown for step in (before(tool, {}, tool), after(tool, {}, tool))]
    _, results = run_plugin(tmp_path, steps, env={
        "SKYRIM_PYTHON": str(root / "missing-python") if failure == "no-python" else sys.executable,
        "SKYRIM_NO_PYTHON_FALLBACK": "1", "SKYRIM_OPENCODE_TIMEOUT_S": "0.8"})
    for index, tool in enumerate(known + unknown):
        pre, post = results[index * 2:index * 2 + 2]
        if tool in known:
            assert "GUARD INERT" in pre.get("error", ""), (failure, tool, pre)
            assert "WARN AND ALLOW" not in post["output"]["output"]
        else:
            assert "error" not in pre
            assert "WARN AND ALLOW" in post["output"]["output"]
            assert "coverage receipt unavailable" in post["output"]["output"]


def test_healthy_devbench_transport_preserves_the_save_pair(tmp_path):
    root, _ = run_plugin(tmp_path, [])
    folder = tmp_path / "config/Saves"
    folder.mkdir(parents=True)
    (root / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + folder.parent.as_posix() + "'\n")
    for suffix in (".ess", ".skse"):
        (folder / ("Existing" + suffix)).write_bytes(suffix.encode())
    _, results = run_plugin(tmp_path, [before("mcp__devbench-vr__console", {"command": "save Existing"})])
    assert "error" not in results[0], results
    records = list((root / ".modding-agent-harness/backups/saves/transactions").glob("*.json"))
    assert len(records) == 1
    manifest = json.loads(records[0].read_text())
    assert len(manifest["groups"][0]["members"]) == 2


def test_system_context_reinjects_preferences_after_each_transform(tmp_path):
    root, _ = run_plugin(tmp_path, [])
    (root / "KNOWLEDGEBASE.local.md").write_text("## My preferences\nKeep my working notes.\n## Other\nUnclassified evidence.")
    transform = {"hook": "experimental.chat.system.transform", "input": {}, "output": {"system": []}}
    _, result = run_plugin(tmp_path, [transform, transform])
    for row in result:
        context = "\n".join(row["output"]["system"])
        assert "Keep my working notes." in context and "Unclassified evidence." not in context
