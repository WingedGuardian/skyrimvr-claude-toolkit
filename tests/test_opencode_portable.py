"""Drive the shipped V1 transport under Node; this does not qualify a running host."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from conftest import REPO


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
    result = subprocess.run([node, str(driver), str(root / ".opencode/plugins/skyguard.js"), str(case)],
                            capture_output=True, text=True, timeout=60,
                            env=dict(os.environ, SKYRIM_PYTHON=sys.executable, **(env or {})))
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
