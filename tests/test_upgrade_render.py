"""Review destination-specific bytes before changing any installed instructions/hooks."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from conftest import BASH, REPO
sys.path.insert(0, str(REPO / "tools"))
from modding_harness import installation, upgrade
from skyguard import codex_definitions, guard_hash, upgrade_render


def config(base, manager="stock"):
    result = {"v": 1, "config_dir": (base / "config").as_posix(), "loadorder_dir": (base / "loadorder").as_posix(),
              "skyrim_folder": "Skyrim VR", "python": (base / "Python 火'quote/python.exe").as_posix(),
              "bash": Path(BASH).as_posix(), "mod_manager": {"kind": "stock"}}
    if manager == "mo2":
        profile = (base / "MO2/profiles/current").as_posix()
        result.update(config_dir=profile, loadorder_dir=profile,
            mod_manager={"kind": "mo2", "instance": (base / "MO2").as_posix(), "profile": "current",
                         "mods": (base / "MO2/mods").as_posix(), "overwrite": (base / "MO2/overwrite").as_posix(),
                         "profile_dir": profile})
    return result


def fixture(base, names=None):
    root, source, bundle = base / "destination [fixture]", base / "source", base / "review"
    root.mkdir()
    source.mkdir()
    names = names or ("AGENTS.md", "CLAUDE.md", *((".codex/hooks.json",) if os.name == "nt" else ()), *guard_hash.CONFIGURED)
    for name in names:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((REPO / name).read_bytes())
    return root, source, bundle, {"v": 1, "files": {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in names}}


@pytest.mark.parametrize("manager", ["stock", "mo2"])
def test_private_rendered_candidate_applies_and_rolls_back_without_rewriting_source(tmp_path, manager):
    root, source, bundle, manifest = fixture(tmp_path)
    data = config(tmp_path, manager)
    review = upgrade.stage_review_bundle(root, source, bundle, manifest,
        candidate_transform=lambda installed, staged, files: upgrade_render.prepare(installed, staged, files, data), reserve=0)
    assert not list(root.iterdir())
    assert review["source_files"] == manifest["files"]
    assert all(hashlib.sha256((source / name).read_bytes()).hexdigest() == digest for name, digest in manifest["files"].items())
    doc = (bundle / "candidate/AGENTS.md").read_text(encoding="utf-8")
    assert not any(token in doc for token in upgrade_render.DOC_TOKENS)
    assert data["config_dir"] in doc and data["loadorder_dir"] in doc and root.as_posix() in doc
    if manager == "mo2":
        assert "MO2 has no real" in doc and data["mod_manager"]["mods"] in doc
    else:
        assert upgrade_render.STOCK in doc
    if ".codex/hooks.json" in manifest["files"]:
        expected = codex_definitions.render((source / ".codex/hooks.json").read_text(), root.as_posix(),
                                           "/" + root.drive[0].lower() + root.as_posix()[2:])
        assert (bundle / "candidate/.codex/hooks.json").read_text() == expected
    hook = bundle / "candidate/.codex/hooks/skyguard-entry.ps1"
    assert hook.read_bytes().startswith(upgrade_render.BOM)
    assert guard_hash.digest(".codex/hooks/skyguard-entry.ps1", hook.read_bytes())[0] == guard_hash.digest(
        ".codex/hooks/skyguard-entry.ps1", (source / ".codex/hooks/skyguard-entry.ps1").read_bytes())[0]
    result = installation.apply(root, bundle, hashlib.sha256((bundle / "complete.json").read_bytes()).hexdigest(),
                                quiescent=True, reserve=0, candidate_validator=upgrade_render.validate)
    assert (root / "AGENTS.md").read_bytes() == (bundle / "candidate/AGENTS.md").read_bytes()
    rolled = installation.rollback(root, result["operation"], quiescent=True)
    assert "AGENTS.md" in rolled["retained_new_files"] and not (root / "AGENTS.md").exists()


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
def test_rendered_interpreter_literals_roundtrip_unicode_and_single_quotes(tmp_path, shell):
    if shell == "bash":
        name = ".codex/hooks/skyguard-entry.sh"
        executable = BASH
    else:
        name = ".codex/hooks/skyguard-entry.ps1"
        executable = shutil.which(shell)
        if not executable:
            pytest.skip(shell + " unavailable; interpreter literal not qualified")
    data = config(tmp_path)
    raw = upgrade_render._hook(name, (REPO / name).read_bytes(), data)
    lines = raw.decode("utf-8-sig").split("\n")
    assignments = [line for line in lines if line.startswith(("PY=", "BASH_EXE=", "$PY = ", "$BASHEXE = "))]
    if shell == "bash":
        command = [executable, "-c", "\n".join(assignments) + '\nprintf "%s|%s" "$PY" "$BASH_EXE"']
    else:
        script = tmp_path / "literal.ps1"
        text = "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding\n" + "\n".join(assignments) + "; [Console]::Write($PY + '|' + $BASHEXE)"
        script.write_bytes((upgrade_render.BOM if raw.startswith(upgrade_render.BOM) else b"") + text.encode("utf-8"))
        command = [executable, "-NoProfile", "-NonInteractive", "-File", str(script)]
    # A hang guard, not a speed check: Windows PowerShell 5.1 cold starts passed 20 s on loaded CI runners.
    result = subprocess.run(command, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode("utf-8") == data["python"] + "|" + data["bash"]


def test_unrendered_transport_refuses_before_any_installed_publication(tmp_path):
    root, source, bundle, manifest = fixture(tmp_path)
    upgrade.stage_review_bundle(root, source, bundle, manifest, reserve=0)
    with pytest.raises(ValueError, match="destination rendering"):
        installation.apply(root, bundle, hashlib.sha256((bundle / "complete.json").read_bytes()).hexdigest(),
                           quiescent=True, reserve=0, candidate_validator=upgrade_render.validate)
    assert not list(root.iterdir())


@pytest.mark.parametrize("problem", ["relative", "control", "incomplete_mo2", "wrong_profile", "conflicting_installed"])
def test_invalid_or_conflicting_configuration_leaves_no_complete_review(tmp_path, problem):
    root, source, bundle, manifest = fixture(tmp_path, ("AGENTS.md",))
    data = config(tmp_path, "mo2" if "mo2" in problem or problem == "wrong_profile" else "stock")
    local_files = ()
    if problem == "relative":
        data["config_dir"] = "relative/config"
    elif problem == "control":
        data["python"] += "\ncommand"
    elif problem == "incomplete_mo2":
        data["mod_manager"].pop("mods")
    elif problem == "wrong_profile":
        data["config_dir"] = (tmp_path / "other profile").as_posix()
    else:
        (root / "skyrim-paths.env").write_text("SKYRIM_CONFIG_DIR='" + (tmp_path / "existing").as_posix() + "'\n")
        local_files = ("skyrim-paths.env",)
    with pytest.raises(ValueError):
        upgrade.stage_review_bundle(root, source, bundle, manifest, local_files=local_files,
            candidate_transform=lambda installed, staged, files: upgrade_render.prepare(installed, staged, files, data), reserve=0)
    assert not (bundle / "complete.json").exists()
    assert all(hashlib.sha256((source / name).read_bytes()).hexdigest() == digest for name, digest in manifest["files"].items())


def test_runtime_cli_renders_a_private_component_review(tmp_path):
    root, source, bundle, manifest = fixture(tmp_path, ("AGENTS.md",))
    config_path, inventory_path = tmp_path / "render.json", tmp_path / "files.json"
    config_path.write_text(json.dumps(config(tmp_path)))
    inventory_path.write_text(json.dumps(manifest))
    result = subprocess.run([sys.executable, "-B", str(REPO / "tools/agent-harness.py"), "upgrade-stage",
        "--installed-root", str(root), "--source", str(source), "--candidate", str(inventory_path),
        "--destination", str(bundle), "--render-config", str(config_path)], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["rendered_files"] == ["AGENTS.md"]
    assert not list(root.iterdir())


def test_every_configured_hook_template_is_in_the_canonical_slot_inventory():
    templates = {p.relative_to(REPO).as_posix() for prefix in (".claude/hooks", ".codex/hooks")
                 for p in (REPO / prefix).iterdir() if p.is_file() and b"{{PYTHON_PATH}}" in p.read_bytes()}
    assert templates == set(guard_hash.CONFIGURED)


def test_setup_encoding_preserves_utf8_bytes_and_is_idempotent(tmp_path):
    path = tmp_path / ".codex/hooks/skyguard-entry.ps1"
    path.parent.mkdir(parents=True)
    raw = "$PY = 'C:/Python 火/python.exe'\r\n".encode("utf-8")
    path.write_bytes(raw)
    assert upgrade_render.encode_powershell(path)
    assert path.read_bytes() == upgrade_render.BOM + raw
    assert not upgrade_render.encode_powershell(path)
    assert not list(path.parent.glob(".encoding-*"))


@pytest.mark.parametrize("defect", ["readonly", "invalid_utf8", "foreign_filename"])
def test_setup_encoding_refuses_without_changing_protected_or_invalid_input(tmp_path, defect):
    path = tmp_path / ".codex/hooks" / ("foreign.ps1" if defect == "foreign_filename" else "skyguard-entry.ps1")
    path.parent.mkdir(parents=True)
    raw = b"\x81\xff" if defect == "invalid_utf8" else "$PY = 'C:/火/python.exe'\r\n".encode("utf-8")
    path.write_bytes(raw)
    if defect == "readonly":
        path.chmod(0o444)
    mode = path.stat().st_mode
    try:
        with pytest.raises((ValueError, UnicodeDecodeError)):
            upgrade_render.encode_powershell(path)
        assert path.read_bytes() == raw and path.stat().st_mode == mode
        assert not list(path.parent.glob(".encoding-*"))
    finally:
        path.chmod(0o644)


@pytest.mark.parametrize("declared", [False, True])
def test_worktree_instructions_distinguish_the_game_from_the_toolkit_root(tmp_path, declared):
    root, source, bundle, manifest = fixture(tmp_path, ("AGENTS.md",))
    game = tmp_path / "actual game"
    data = config(tmp_path)
    if declared:
        data["game_root"] = game.as_posix()
    else:
        (root / "skyrim-paths.env").write_text("SKYRIM_GAME_ROOT='" + game.as_posix() + "'\n")
    upgrade.stage_review_bundle(root, source, bundle, manifest, local_files=("skyrim-paths.env",), reserve=0,
        candidate_transform=lambda installed, staged, files: upgrade_render.prepare(installed, staged, files, data))
    doc = (bundle / "candidate/AGENTS.md").read_text(encoding="utf-8")
    assert "- **Game root**: `" + game.as_posix() + "/`" in doc
