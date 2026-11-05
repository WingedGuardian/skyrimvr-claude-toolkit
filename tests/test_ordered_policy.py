"""The profile may retire only an exact retained policy through reviewed settings."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import os
from dataclasses import asdict

from conftest import BASH, REPO
sys.path.insert(0, str(REPO / "tools"))
from skyguard import guard_hash, local_policy
from modding_harness import contracts
pytestmark = pytest.mark.skipif(os.name != "nt", reason="reviewed personal native policy is Windows-specific")


def test_unqualified_personal_policy_keeps_original_payload_shape(tmp_path):
    policy = tmp_path / local_policy.LEGACY
    policy.parent.mkdir(parents=True)
    policy.write_text("def decide(payload):\n"
                      " if set(payload) != {'tool_name','tool_input','cwd','session_id'}:\n"
                      "  return {'kind':'deny','reason':'unexpected payload contract change'}\n"
                      " return {'kind':'allow'}\n")
    registration = tmp_path / local_policy.CONFIG
    registration.parent.mkdir(parents=True)
    registration.write_text(json.dumps({'v': 1, 'kind': local_policy.KIND,
        'path': local_policy.LEGACY, 'sha256': hashlib.sha256(policy.read_bytes()).hexdigest()}))
    action = contracts.Action('future', 'Bash', 'shell', shell='bash', cwd=str(tmp_path), command='echo contract')
    result = local_policy.evaluate(action, tmp_path)
    assert result.decision == 'allow', result


@pytest.fixture(params=['original', 'audit', 'helper-boundary', 'source-effects'])
def ordered(tmp_path, request):
    root, source, bundle = tmp_path / "installed", tmp_path / "source", tmp_path / "review"
    root.mkdir()
    (source / "tools").mkdir(parents=True)
    for name in ("skyguard", "modding_harness"):
        shutil.copytree(REPO / "tools" / name, source / "tools" / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(REPO / "tools/agent-harness.py", source / "tools/agent-harness.py")
    shutil.copy2(REPO / "GUARD-FILES.sha256", source / "GUARD-FILES.sha256")
    for name in (".claude/settings.json", ".codex/hooks.json", ".opencode/hooks/skyguard_adapter.py", ".opencode/plugins/skyguard.js",
                 *guard_hash.CONFIGURED):
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((REPO / name).read_bytes())
    policy = root / ".claude/hooks/protect-save-access.py"
    policy.parent.mkdir(parents=True)
    policy.write_text("import json\ndef decide(payload):\n"
                      " if 'POLICY_CANARY' in json.dumps(payload): return {'kind':'deny','reason':'retained personal policy'}\n"
                      " return {'kind':'allow'}\n"
                      "if __name__=='__main__': raise RuntimeError('legacy main must never run')\n")
    wrapper = root / ".claude/hooks/save-access-entry.ps1"
    wrapper.write_bytes((REPO / "tests/fixtures/ordered-policy/save-access-entry.ps1").read_bytes())
    controller = root / ".claude/scripts/guarded-runtime-load.py"
    controller.parent.mkdir()
    controller.write_text("raise RuntimeError('inert controller must never execute')\n")
    module = source / "tools/skyguard/local_policy.py"
    qualified_hash = {'original': local_policy.TRANSFER_POLICY_SHA256, 'audit': local_policy.AUDIT_POLICY_SHA256,
                      'helper-boundary': local_policy.HELPER_BOUNDARY_POLICY_SHA256,
                      'source-effects': local_policy.SOURCE_EFFECTS_POLICY_SHA256}[request.param]
    module.write_text(module.read_text().replace(qualified_hash, hashlib.sha256(policy.read_bytes()).hexdigest())
                      .replace(local_policy.CONTROLLED_LOAD_SHA256, hashlib.sha256(controller.read_bytes()).hexdigest()))
    command = 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + root.as_posix() + '/.claude/hooks/save-access-entry.ps1"'
    handler = {"type": "command", "command": command, "timeout": 30,
               "statusMessage": "Save guard: backing up saves before changes"}
    for name in (".claude/settings.json", ".codex/hooks.json"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        value = {**handler, **({"commandWindows": command} if name.startswith('.codex') else {})}
        target.write_text(json.dumps({"personal": "retained", "hooks": {"PreToolUse": [{"matcher": ".*", "hooks": [value]}],
             "PostToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": "personal post"}]}]}}))
    config = tmp_path / "render.json"
    config.write_text(json.dumps({"v": 1, "config_dir": (root / 'profile').as_posix(), "loadorder_dir": (root / 'profile').as_posix(),
           "skyrim_folder": "Skyrim VR", "python": Path(sys.executable).as_posix(), "bash": Path(BASH).as_posix(),
           "mod_manager": {"kind": "stock"}}))
    (root / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + (root / 'profile').as_posix() + "'\n")
    manifest = tmp_path / "files.json"
    manifest.write_text(json.dumps({"v": 1, "files": {path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in source.rglob('*') if path.is_file()}}))
    def cli(*args, installed=False):
        entry = (root if installed else source) / "tools/agent-harness.py"
        completed = subprocess.run([sys.executable, "-B", str(entry), *args], capture_output=True, timeout=45)
        return completed, json.loads(completed.stdout)
    def stage(destination=None):
        return cli("upgrade-stage", "--installed-root", str(root), "--source", str(source), "--candidate", str(manifest),
                   "--destination", str(destination or bundle), "--render-config", str(config), "--integrate-local-policy")
    return root, source, bundle, policy, wrapper, controller, cli, stage


def test_actual_cli_stages_and_applies_reviewed_personal_policy_with_rollback(ordered, tmp_path):
    root, source, bundle, policy, wrapper, controller, cli, stage = ordered
    before = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    completed, response = stage()
    assert completed.returncode == 0 and response["ordered_integration_available"], response
    assert {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()} == before
    review = json.loads((bundle / "complete.json").read_bytes())
    integration = review["ordered_integration"]
    assert set(integration["files"]) == {".claude/settings.json", ".codex/hooks.json", ".modding-agent-harness/local-policies.json"}
    assert integration["sources"][".claude/hooks/protect-save-access.py"] == hashlib.sha256(policy.read_bytes()).hexdigest()
    assert json.loads((bundle / 'reviewed' / local_policy.CONFIG).read_bytes())['sha256'] == hashlib.sha256(policy.read_bytes()).hexdigest()
    choices = {row["path"]: "candidate" for row in review["plan"]["files"] if row["candidate_sha256"] is not None}
    choices.update({name: "reviewed_settings" for name in integration["files"]})
    choice_file = tmp_path / "choices.json"
    choice_file.write_text(json.dumps(choices))
    completed, applied = cli("upgrade-apply", "--installed-root", str(root), "--bundle", str(bundle),
         "--review-sha256", hashlib.sha256((bundle / "complete.json").read_bytes()).hexdigest(), "--choices", str(choice_file), "--quiescent")
    assert completed.returncode == 0 and applied["host_review_required"], applied
    for name in (".claude/settings.json", ".codex/hooks.json"):
        actual = json.loads((root / name).read_bytes())
        # The personal hook is RETAINED (order is immaterial: every PostToolUse hook runs; since 2026-10-30 the toolkit
        # has its own PostToolUse entry, the save sentinel, so the personal one is no longer necessarily last).
        post = [h["command"] for g in actual["hooks"]["PostToolUse"] for h in g["hooks"]]
        assert actual["personal"] == "retained" and "personal post" in post, post
        assert actual["hooks"]["PreToolUse"] == json.loads((bundle / "candidate" / name).read_bytes())["hooks"]["PreToolUse"]
    for path in (policy, wrapper, controller):
        assert path.read_bytes() == before[path.relative_to(root).as_posix()]
    def check(action):
        completed = subprocess.run([sys.executable, "-B", str(root / 'tools/agent-harness.py'), "check"],
              input=json.dumps(asdict(action)).encode(), capture_output=True, timeout=30)
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)
    refused = check(contracts.Action("future", "Bash", "shell", cwd=str(root), shell="bash", command="echo POLICY_CANARY"))
    assert refused["result"]["decision"] == "deny" and refused["result"]["reason"] == "retained personal policy"
    saves = root / 'profile/Saves'
    saves.mkdir(parents=True)
    ess, skse = saves / 'Fixture.ess', saves / 'Fixture.skse'
    ess.write_bytes(b'opaque ESS')
    skse.write_bytes(b'opaque SKSE')
    allowed = check(contracts.Action("future", "edit", "files", cwd=str(root), paths=(str(ess),), operations=("write",)))
    assert allowed["result"]["decision"] == "allow" and allowed["result"]["preservation"] == "verified", allowed
    assert len(allowed["transactions"]) == 1 and ess.read_bytes() == b'opaque ESS' and skse.read_bytes() == b'opaque SKSE'
    completed, repeated = stage(bundle.with_name('repeat-review'))
    assert completed.returncode == 0 and repeated['ordered_integration_available'], repeated
    completed, rolled = cli("upgrade-rollback", applied["operation"], "--installed-root", str(root), "--quiescent")
    assert completed.returncode == 0, rolled
    for name, raw in before.items():
        assert (root / name).read_bytes() == raw
    assert not (root / ".modding-agent-harness/local-policies.json").exists()
    assert (root / '.modding-agent-harness/backups/saves').is_dir()


@pytest.mark.parametrize("fault", ["policy", "wrapper", "controller", "restore-helper", "matcher", "timeout", "async",
                                  "windows-command", "local-handler", "candidate-matcher", "missing-runtime", "empty-registration"])
def test_unqualified_source_groups_or_partial_runtime_cannot_produce_a_migration(ordered, fault):
    root, source, bundle, policy, wrapper, controller, _cli, stage = ordered
    if fault in ("policy", "wrapper", "controller"):
        path = {"policy": policy, "wrapper": wrapper, "controller": controller}[fault]
        path.write_bytes(path.read_bytes() + b'\n# unqualified change\n')
    elif fault == "restore-helper":
        policy.with_name('restore-save-backup.py').write_text('unqualified helper')
    elif fault == "empty-registration":
        registry = root / '.modding-agent-harness/local-policies.json'
        registry.parent.mkdir()
        registry.write_text('{}')
    elif fault == "local-handler":
        (root / '.claude/settings.local.json').write_text(json.dumps({'hooks': {'PreToolUse': [
             {'matcher': '.*', 'hooks': [{'type':'command', 'command':'unknown policy'}]}]}}))
    elif fault in ("candidate-matcher", "missing-runtime"):
        inventory = source.parent / 'files.json'
        data = json.loads(inventory.read_text())
        if fault == "missing-runtime":
            data['files'].pop('tools/skyguard/engine.py')
        else:
            path = source / '.claude/settings.json'
            value = json.loads(path.read_bytes())
            value['hooks']['PreToolUse'][0]['matcher'] = 'Bash'
            path.write_text(json.dumps(value))
            data['files']['.claude/settings.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
        inventory.write_text(json.dumps(data))
    else:
        path = root / ('.codex/hooks.json' if fault == 'windows-command' else '.claude/settings.json')
        value = json.loads(path.read_bytes())
        group = value['hooks']['PreToolUse'][0]
        if fault == 'matcher':
            group['matcher'] = 'Write'
        else:
            group['hooks'][0][{'timeout':'timeout','async':'async','windows-command':'commandWindows'}[fault]] = {
                 'timeout': 29, 'async': True, 'windows-command': 'unknown Windows policy'}[fault]
        path.write_text(json.dumps(value))
    before = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    completed, response = stage()
    assert completed.returncode == 2, response
    assert not (bundle / 'complete.json').exists()
    assert {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()} == before


@pytest.mark.parametrize('variant', ['python-path', 'crlf-bom'])
def test_only_documented_wrapper_normalizations_are_accepted(ordered, variant):
    _root, _source, _bundle, _policy, wrapper, _controller, _cli, stage = ordered
    raw = wrapper.read_bytes()
    if variant == 'python-path':
        raw = raw.replace(b'C:/Tools/Python/python.exe', b'C:/Other Python/python.exe')
    else:
        # From LF first: `*.ps1 eol=crlf` checks the fixture out CRLF, and CRLF -> `\r\r\n` is not a documented
        # normalization (CI run 39167733692, every Windows leg). A BOM plus exactly CRLF is.
        raw = b'\xef\xbb\xbf' + raw.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
    wrapper.write_bytes(raw)
    completed, response = stage()
    assert completed.returncode == 0 and response['ordered_integration_available'], response


@pytest.mark.parametrize('fault', ['keep-runtime', 'missing-choice', 'late-policy', 'late-native-settings'])
def test_ordered_application_refuses_unreviewed_runtime_or_late_edits_before_publication(ordered, tmp_path, fault):
    root, _source, bundle, policy, _wrapper, _controller, cli, stage = ordered
    completed, response = stage()
    assert completed.returncode == 0, response
    review = json.loads((bundle / 'complete.json').read_bytes())
    choices = {row['path']: 'candidate' for row in review['plan']['files'] if row['candidate_sha256'] is not None}
    choices.update({name: 'reviewed_settings' for name in review['ordered_integration']['files']})
    if fault == 'keep-runtime':
        choices['tools/skyguard/local_policy.py'] = 'keep'
    elif fault == 'missing-choice':
        choices.pop('.modding-agent-harness/local-policies.json')
    else:
        path = policy if fault == 'late-policy' else root / '.codex/hooks.json'
        path.write_bytes(path.read_bytes() + b' ')
    before = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    choice_file = tmp_path / 'choices.json'
    choice_file.write_text(json.dumps(choices))
    completed, response = cli('upgrade-apply', '--installed-root', str(root), '--bundle', str(bundle),
             '--review-sha256', hashlib.sha256((bundle / 'complete.json').read_bytes()).hexdigest(), '--choices', str(choice_file), '--quiescent')
    assert completed.returncode == 2, response
    assert {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()} == before


@pytest.mark.parametrize("legacy_version", ["v3.9.1", "pre-h1"])
def test_published_legacy_groups_migrate_to_current_blocking_guards(ordered, legacy_version):
    root, _source, _bundle, _policy, _wrapper, _controller, _cli, stage = ordered
    if legacy_version == "v3.9.1":
        fixture = REPO / "tests/fixtures/migrate-3x/v3.9.1/settings.json.fixture"
        legacy = json.loads(fixture.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    else:
        fixture = REPO / "tests/fixtures/ordered-policy/claude-pre-h1-groups.json.fixture"
        legacy = json.loads(fixture.read_text(encoding="utf-8"))["groups"]
    path = root / ".claude/settings.json"
    settings = json.loads(path.read_bytes())
    settings["hooks"]["PreToolUse"].extend(legacy)
    path.write_text(json.dumps(settings), encoding="utf-8")
    completed, response = stage()
    assert completed.returncode == 0 and response["ordered_integration_available"], response
