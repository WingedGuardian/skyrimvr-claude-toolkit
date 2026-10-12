"""tools/verify-release.sh --local [--scrubbed] (Plan 8 T10.3): the artifact a user extracts, verified from outside.

Each test runs the real script on a zip. The leak grep's names come from the environment, so every run here
sets a made-up account (USERNAME, HOME, USERPROFILE) and turns the registry lookup off: the test then says
nothing about the real machine, and a planted spelling is the only thing that can be found.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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
    for junk in (".github", "scripts/layer2-spike", ".claude/backups", ".superpowers", ".skyguard"):
        if (root / junk).exists():
            shutil.rmtree(root / junk)
    for junk in (".claude/settings.local.json", ".claude/skyrim-paths.env", "skyrim-paths.env", "KNOWLEDGEBASE.local.md"):
        if (root / junk).exists():
            (root / junk).unlink()
    # Only what a release can ship: git-tracked files. CI tees behavioral.log / collect.log into the checkout
    # BEFORE this suite runs (ci.yml), and those logs name the runner's home -- copied in, they were reported as a
    # leak in the "artifact" (CI run 38178385999, 5 tests), a defect of this fixture and not of any release.
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=str(REPO), capture_output=True, check=True).stdout
    keep = {t for t in tracked.decode("utf-8").split("\0") if t}
    for f in sorted(root.rglob("*"), reverse=True):
        if f.is_file() and f.relative_to(root).as_posix() not in keep:
            f.unlink()
    (root / "BUILD-INFO").write_text("commit={}\nversion=dev-test\n".format(SHA), encoding="utf-8")
    return root


def _env(tmp_path):
    home = tmp_path / NAME
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items()
           if k not in ("USER", "LOGNAME", "RELEASE_LEAK_NAMES", "RELEASE_LEAK_PATHS", "SKSE_CRASH_DIR")}
    env.update(USERNAME=NAME, HOME=str(home), USERPROFILE=str(home), RELEASE_LEAK_REGISTRY="0",
               VERIFY_WORK_ROOT=str(tmp_path))
    # verify-release runs `esp-verify-wrapper.sh --help`, which (since T6b) probes the repo-local Spriggit with
    # `dotnet tool run spriggit -- --version`. Under the launcher `dotnet` is the package-manager tripwire, so point
    # the wrapper's own seam at a dotnet that does not exist: the probe fails, the --help line SKIPs as it does on
    # any machine without Spriggit, and no test here ever reaches a real or stub dotnet.
    env["ESP_VERIFY_DOTNET"] = str(tmp_path / "no-dotnet-here")
    return env


def _verify(tmp_path, zip_path, *args):
    return subprocess.run([BASH, str(VERIFY), "--local", str(zip_path), *args], capture_output=True, text=True,
                          env=_env(tmp_path), timeout=900, cwd=str(tmp_path))


def _line(out, name):
    return next((l for l in out.splitlines() if name in l), "")


def _variant(tmp_path, payload, damage=None, top="skyrim-ai-assistant-toolkit-dev-test"):
    src = tmp_path / "src"
    shutil.copytree(payload, src)
    if damage:
        damage(src)
    return _zip(src, tmp_path / "t.zip", top)


def test_a_whole_artifact_passes(tmp_path, payload):
    r = _verify(tmp_path, _variant(tmp_path, payload), "--expect-commit", SHA)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr
    assert "RESULT: OK" in r.stdout


def test_verification_does_not_create_identity_bearing_payload_bytecode(tmp_path, payload, monkeypatch):
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    workspace = tmp_path / NAME
    workspace.mkdir()
    r = _verify(workspace, _variant(tmp_path, payload), "--expect-commit", SHA)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no home path, user name or key in the payload" in r.stdout


def test_scrubbed_verification_accepts_the_measured_venv_interpreter(tmp_path, payload, monkeypatch):
    import venv
    runtime = tmp_path / NAME / "runtime"
    venv.EnvBuilder(with_pip=False).create(runtime)
    executable = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    monkeypatch.setenv("PYTHON", str(executable))
    r = _verify(tmp_path, _variant(tmp_path, payload), "--scrubbed", "--expect-commit", SHA)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no home path or user name in the installed files" in r.stdout


def test_a_failed_product_slug_read_refuses_before_artifact_work(tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / "tools").mkdir(parents=True)
    script = checkout / "tools/verify-release.sh"
    script.write_bytes(VERIFY.read_bytes())
    (checkout / "package.json").write_text("not json", encoding="utf-8")
    env = _env(tmp_path)
    env["PYTHON"] = sys.executable.replace("\\", "/")
    result = subprocess.run([BASH, str(script), "--local", str(tmp_path / "absent.zip")],
                            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not list(tmp_path.glob("verify-release.*")), "artifact work began after a failed identity read"


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


def _setup_documents_below_login_home():
    """Does setup.sh's Documents folder sit below C:/Users/<whoami> here? Whenever PowerShell gives no answer (Linux,
    or Windows under the test harness's made-up profile -- MEASURED: an empty line) setup writes its
    C:/Users/<whoami>/Documents fallback; otherwise when the real Documents is not redirected elsewhere."""
    login = subprocess.run([BASH, "-c", "whoami"], capture_output=True, text=True, timeout=30).stdout.strip()
    docs = ""
    if os.name == "nt":
        docs = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('MyDocuments')"],
                              capture_output=True, text=True, timeout=60).stdout.strip().replace("\\", "/")
    return bool(login) and (not docs or docs.lower().startswith(("c:/users/" + login + "/").lower()))


def test_the_config_folder_setup_writes_is_allow_listed_in_the_scrubbed_install(tmp_path, payload):
    """CI run 38178385999 (ubuntu-latest): setup.sh wrote its fallback C:/Users/runner/Documents/My Games/Skyrim VR
    while the real home was /home/runner, so the validated config folder was NOT allow-listed and the six files
    naming it were reported as leaks. verify-release now also validates it below C:/Users/<whoami>. Here the test's
    home is a made-up one, so only that second home can allow-list the folder setup really wrote."""
    if not _setup_documents_below_login_home():
        pytest.skip("this machine's Documents folder is not below C:/Users/<whoami>; the precondition does not hold")
    r = _verify(tmp_path, _variant(tmp_path, payload), "--scrubbed")
    assert "allow-listed the config folder" in r.stdout and "NOT allow-listed" not in r.stdout, r.stdout[-4000:]
    assert r.returncode == 0, r.stdout[-4000:]


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
    """Review M4: any skyrim-ai-assistant-toolkit-* root passed, and BUILD-INFO's version was never compared."""
    r = _verify(tmp_path, _variant(tmp_path, payload), "--tag", "v9.9.9")
    assert r.returncode == 1
    assert "!!" in _line(r.stdout, "root folder names v9.9.9"), r.stdout[-3000:]
    assert "!!" in _line(r.stdout, "BUILD-INFO version"), r.stdout[-3000:]


# ------------------------------------------------------------------ release review T13 M5

def _product(name):
    def damage(src):
        p = src / "package.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        if name is None:
            d.pop("name", None)
        else:
            d["name"] = name
        p.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
    return damage


def test_the_expected_root_comes_from_the_artifact_and_another_product_is_named(tmp_path, payload):
    """A 3.x-named artifact checked from this checkout: its root matches its OWN product name, and the failing line
    names both products and the remedy -- not a bare `versioned root` mismatch."""
    old = "skyrimvr-claude-toolkit"
    r = _verify(tmp_path, _variant(tmp_path, payload, _product(old), top=old + "-dev-test"))
    assert r.returncode == 1
    assert _line(r.stdout, "extracted to a versioned root").endswith(" ok"), r.stdout[-3000:]
    line = _line(r.stdout, "the artifact is this checkout's product")
    assert "!!" in line and "'" + old + "'" in line and "its own release tag" in line, r.stdout[-3000:]


def test_an_artifact_root_that_is_not_its_own_product_is_red(tmp_path, payload):
    r = _verify(tmp_path, _variant(tmp_path, payload, top="something-else-dev-test"))
    assert r.returncode == 1 and "!!" in _line(r.stdout, "versioned root"), r.stdout[-3000:]


@pytest.mark.parametrize("name", [None, "Not A Slug"], ids=["absent", "not-a-slug"])
def test_an_artifact_without_a_valid_product_name_is_red(tmp_path, payload, name):
    r = _verify(tmp_path, _variant(tmp_path, payload, _product(name)))
    assert r.returncode == 1 and "!!" in _line(r.stdout, "the artifact names its product"), r.stdout[-3000:]

