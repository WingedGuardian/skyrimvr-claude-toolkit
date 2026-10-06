"""tools/verify-release.sh --local [--scrubbed] (Plan 8 T10.3): the artifact a user extracts, verified from outside.

Each test runs the real script on a zip. The leak grep's names come from the environment, so every run here
sets a made-up account (USERNAME, HOME, USERPROFILE) and turns the registry lookup off: the test then says
nothing about the real machine, and a planted spelling is the only thing that can be found.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from conftest import BASH, REPO, copy_repo

VERIFY = REPO / "tools" / "verify-release.sh"
NAME = "zqxuserq"
SHA = "c" * 40


def _zip(src: Path, out: Path, top: str) -> Path:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                zf.write(f, top + "/" + f.relative_to(src).as_posix())
    return out


@pytest.fixture(scope="module")
def payload(tmp_path_factory):
    root = copy_repo(tmp_path_factory.mktemp("vr") / "p")
    for junk in (".github", ".claude/backups", ".superpowers", ".skyguard"):
        if (root / junk).exists():
            shutil.rmtree(root / junk)
    for junk in (".claude/settings.local.json", ".claude/skyrim-paths.env", "KNOWLEDGEBASE.local.md"):
        if (root / junk).exists():
            (root / junk).unlink()
    (root / "BUILD-INFO").write_text("commit={}\nversion=dev-test\n".format(SHA), encoding="utf-8")
    return root


def _env(tmp_path):
    home = tmp_path / NAME
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if k not in ("USER", "LOGNAME", "RELEASE_LEAK_NAMES", "RELEASE_LEAK_PATHS", "SKSE_CRASH_DIR")}
    env.update(USERNAME=NAME, HOME=str(home), USERPROFILE=str(home), RELEASE_LEAK_REGISTRY="0",
               VERIFY_WORK_ROOT=str(tmp_path))
    return env


def _verify(tmp_path, zip_path, *args):
    return subprocess.run([BASH, str(VERIFY), "--local", str(zip_path), *args], capture_output=True, text=True,
                          env=_env(tmp_path), timeout=900, cwd=str(tmp_path))


def _line(out, name):
    return next((l for l in out.splitlines() if name in l), "")


def _variant(tmp_path, payload, damage=None, top="skyrimvr-claude-toolkit-dev-test"):
    src = tmp_path / "src"
    shutil.copytree(payload, src)
    if damage:
        damage(src)
    return _zip(src, tmp_path / "t.zip", top)


def test_a_whole_artifact_passes(tmp_path, payload):
    r = _verify(tmp_path, _variant(tmp_path, payload), "--expect-commit", SHA)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr
    assert "RESULT: OK" in r.stdout


def test_a_payload_defect_is_red_on_the_payload_line(tmp_path, payload):
    r = _verify(tmp_path, _variant(tmp_path, payload, lambda s: (s / ".claude" / "hooks" / "protect-bash.sh").unlink()))
    assert r.returncode == 1
    assert "!!" in _line(r.stdout, "payload (check-release-payload.py)"), r.stdout[-3000:]


def test_the_expected_commit_is_enforced(tmp_path, payload):
    r = _verify(tmp_path, _variant(tmp_path, payload), "--expect-commit", "d" * 40)
    assert r.returncode == 1
    assert "!!" in _line(r.stdout, "BUILD-INFO commit is the expected one"), r.stdout[-3000:]


@pytest.mark.parametrize("spelling", ["C:/Users/" + NAME + "/x", "C:" + "\\" + "Users" + "\\" + NAME, "/c/Users/" + NAME])
def test_a_home_path_in_the_payload_is_red(tmp_path, payload, spelling):
    r = _verify(tmp_path, _variant(tmp_path, payload, lambda s: (s / "docs" / "leak.md").write_text(spelling)))
    assert r.returncode == 1
    assert "!!" in _line(r.stdout, "leak grep (payload)"), r.stdout[-3000:]
    assert "docs/leak.md" in r.stdout


def test_two_roots_are_refused(tmp_path, payload):
    z = _variant(tmp_path, payload)
    with zipfile.ZipFile(z, "a") as zf:
        zf.writestr("stray/x.txt", "x")
    r = _verify(tmp_path, z)
    assert r.returncode == 1 and "!!" in _line(r.stdout, "one root folder"), r.stdout[-2000:]


def test_bad_usage_is_exit_2(tmp_path):
    for args in ([], ["--local"], ["--tag", "nope", "--local", "x.zip"], ["--bogus"]):
        r = subprocess.run([BASH, str(VERIFY), *args], capture_output=True, text=True, timeout=60, env=_env(tmp_path))
        assert r.returncode == 2, (args, r.stdout, r.stderr)


def test_the_scrubbed_install_runs_and_its_canary_is_invisible(tmp_path, payload):
    """setup.sh, the canary, the drift check, the selftest and every entry, with the environment cleared. The
    canary line goes red if the parent's environment reaches the child (test_mutations twin: env -i dropped)."""
    r = _verify(tmp_path, _variant(tmp_path, payload), "--scrubbed")
    out = r.stdout
    for name in ("leak canary set in the parent (control)", "leak canary invisible inside the scrubbed env",
                 "no author variables inside", "repo checkout off the scrubbed PATH",
                 "setup.sh in the scrubbed env", "setup.sh again: idempotent", "no unrendered placeholder left by setup",
                 "gen_agent_trees --check on the payload", "skyguard engine selftest",
                 "protect-bash.sh answers deny", "protect-files.sh answers deny", "protect-mcp.sh answers deny",
                 "Codex skyguard-entry.sh denies", "no home path or user name in the installed files"):
        line = _line(out, name)
        assert line.endswith(" ok"), "{!r} not ok:\n{}".format(name, out[-4000:] + r.stderr[-2000:])
    assert r.returncode == 0, out[-4000:]


# ------------------------------------------------------------------ fix round 1 (review I1, M4)

def test_the_release_mode_fails_closed_without_leak_tokens(tmp_path, payload):
    """What release.yml runs: VERIFY_REQUIRE_LEAK_TOKENS=1 and the LEAK_TOKENS secret. Absent or empty, the step
    must go red -- a runner's own name says nothing about the maintainer."""
    z = _variant(tmp_path, payload)
    env = _env(tmp_path)
    env.update(VERIFY_REQUIRE_LEAK_TOKENS="1", USERNAME="runner")
    env.pop("LEAK_TOKENS", None)
    r = subprocess.run([BASH, str(VERIFY), "--local", str(z)], capture_output=True, text=True, env=env,
                       timeout=900, cwd=str(tmp_path))
    assert r.returncode != 0, r.stdout[-3000:]
    assert "!!" in _line(r.stdout, "leak grep (payload)") and "LEAK_TOKENS" in r.stdout, r.stdout[-3000:]
    env["LEAK_TOKENS"] = NAME
    r = subprocess.run([BASH, str(VERIFY), "--local", str(z)], capture_output=True, text=True, env=env,
                       timeout=900, cwd=str(tmp_path))
    assert r.returncode == 0, r.stdout[-3000:]


def test_a_tag_must_match_the_root_folder_and_build_info(tmp_path, payload):
    """Review M4: any skyrimvr-claude-toolkit-* root passed, and BUILD-INFO's version was never compared."""
    r = _verify(tmp_path, _variant(tmp_path, payload), "--tag", "v9.9.9")
    assert r.returncode == 1
    assert "!!" in _line(r.stdout, "root folder names v9.9.9"), r.stdout[-3000:]
    assert "!!" in _line(r.stdout, "BUILD-INFO version"), r.stdout[-3000:]
