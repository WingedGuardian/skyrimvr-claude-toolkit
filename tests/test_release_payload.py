"""tools/check-release-payload.py: one checker for both release gates (Plan 8 T10.2), and the leak grep (T10.3).

Every check is proven red on a payload missing its piece -- on ITS OWN line, not by luck -- and green on a
whole payload first (a gate that was never green proves nothing by going red). The Codex and adapter
sections keep their own tests in test_repo_invariants.py and test_adapter_payload.py.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO, copy_repo

CHECKER = REPO / "tools" / "check-release-payload.py"
SHA = "a" * 40


def _mod():
    spec = importlib.util.spec_from_file_location("check_release_payload", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_an_allowed_prefix_also_allows_the_spelling_the_filesystem_gives_it(tmp_path, monkeypatch):
    """windows-latest (CI run 38187755448): the workspace allowance was the 8.3 short spelling (`cygpath -m /tmp/x`)
    while setup wrote the long one, so 43 installed files were reported for naming their own folder. The long
    spelling realpath() gives is allowed too."""
    mod = _mod()
    short = "C:/Users/ZQXUSE~1/AppData/Local/Temp/verify-release.x"
    long_ = "C:/Users/zqxuserq/AppData/Local/Temp/verify-release.x"
    # The checker's OWN `os` is replaced, never the shared os.path: patching ntpath/posixpath globally also changed
    # pathlib, which calls realpath(p, strict=...) on 3.11+ and broke on a one-argument stand-in (CI run 38201545601).
    import types

    def realpath(p, *args, **kwargs):
        return long_ if p == short else os.path.realpath(p, *args, **kwargs)

    def isdir(p):
        return p == short or os.path.isdir(p)

    fake_path = types.SimpleNamespace(**{k: getattr(os.path, k) for k in dir(os.path) if not k.startswith("__")})
    fake_path.realpath, fake_path.isdir = realpath, isdir
    monkeypatch.setattr(mod, "os", types.SimpleNamespace(**{k: getattr(os, k) for k in dir(os) if not k.startswith("__")}))
    monkeypatch.setattr(mod.os, "path", fake_path)
    assert mod._with_long_names([short]) == [short, long_]
    assert mod._with_long_names(["/not/there"]) == ["/not/there"]
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "CLAUDE.md").write_text("- **Game root**: `" + long_ + "/scrubbed/game/`\n", encoding="utf-8")
    needles = mod.leak_needles(["zqxuserq"])
    assert mod.scan_leaks([scan], needles, [short]), "control: the short spelling alone allows nothing written long"
    assert not mod.scan_leaks([scan], needles, mod._with_long_names([short]))


def _check(root, *extra, env=None):
    return subprocess.run([sys.executable, "-B", str(CHECKER), str(root), *extra],
                          capture_output=True, text=True, timeout=120, env=env)


def _line(out, name):
    return next((l for l in out.splitlines() if name in l), "")


@pytest.fixture(scope="module")
def pristine(tmp_path_factory):
    """The tree as the builder ships it: tracked content (copy_repo drops tests/), minus every path the builder
    excludes (.github/, scripts/layer2-spike/), a BUILD-INFO."""
    root = copy_repo(tmp_path_factory.mktemp("payload") / "toolkit")
    shutil.rmtree(root / ".github")
    shutil.rmtree(root / "docs" / "superpowers")
    if (root / "scripts" / "layer2-spike").is_dir():
        shutil.rmtree(root / "scripts" / "layer2-spike")
    if (root / "scripts" / "guard-corpus").is_dir():
        shutil.rmtree(root / "scripts" / "guard-corpus")
    if (root / "scripts" / "guard-latency.py").exists():
        (root / "scripts" / "guard-latency.py").unlink()
    for junk in (".claude/settings.local.json", ".claude/skyrim-paths.env", "skyrim-paths.env", ".claude/backups",
                 "KNOWLEDGEBASE.local.md", ".superpowers", ".skyguard", "tools/resaver-cli/tests"):
        p = root / junk
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    (root / "BUILD-INFO").write_text("commit={}\nversion=9.9.9\n".format(SHA), encoding="utf-8")
    return root


@pytest.fixture()
def payload(tmp_path, pristine):
    root = tmp_path / "toolkit"
    shutil.copytree(pristine, root)
    return root


def test_a_whole_payload_passes(pristine):
    r = _check(pristine, "--expect-commit", SHA)
    assert r.returncode == 0 and "RELEASE PAYLOAD: OK" in r.stdout, r.stdout + r.stderr


def _mkdir(rel):
    def f(r):
        (r / rel).mkdir(parents=True, exist_ok=True)
        (r / rel / "x").write_text("x", encoding="utf-8")
    return f


def _first_claude_hook(r):
    return sorted((r / ".claude" / "hooks").glob("*.sh"))[0]


def _inert_stdin(r):
    h = _first_claude_hook(r)
    h.write_text(h.read_text(encoding="utf-8").replace("INPUT=$(cat)", "INPUT=$(cat /dev/stdin)"), encoding="utf-8")


def _heartbeat_only_in_comment(r):
    h = _first_claude_hook(r)
    lines = [("# " + l if "hook-heartbeat" in l and not l.lstrip().startswith("#") else l)
             for l in h.read_text(encoding="utf-8").splitlines()]
    h.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _codex_sh_inert(r):
    h = r / ".codex" / "hooks" / "skyguard-entry.sh"
    h.write_text(h.read_text(encoding="utf-8").replace("INPUT=$(cat)", "INPUT=$(cat /dev/fd/0)"), encoding="utf-8")


def _ps1_no_stdin(r):
    h = r / ".codex" / "hooks" / "skyguard-entry.ps1"
    h.write_text(h.read_text(encoding="utf-8").replace("[Console]::OpenStandardInput()", "$null"), encoding="utf-8")


def _ps1_no_heartbeat(r):
    h = r / ".codex" / "hooks" / "skyguard-entry.ps1"
    lines = [("# " + l if "hook-heartbeat" in l else l) for l in h.read_text(encoding="utf-8").splitlines()]
    h.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _forge_build_info(r):
    (r / "BUILD-INFO").write_text("commit=deadbeef\n", encoding="utf-8")


BREAKS = {
    "build-info-missing": (lambda r: (r / "BUILD-INFO").unlink(), "BUILD-INFO present"),
    "build-info-short-sha": (_forge_build_info, "BUILD-INFO names a full commit sha"),
    "build-info-other-commit": (lambda r: (r / "BUILD-INFO").write_text("commit={}\n".format("b" * 40)),
                                "BUILD-INFO commit is the expected one"),
    "setup-missing": (lambda r: (r / "setup.sh").unlink(), "setup.sh present"),
    "kb-missing": (lambda r: (r / "KNOWLEDGEBASE.md").unlink(), "KNOWLEDGEBASE.md present"),
    "canary-missing": (lambda r: (r / "tools" / "hook-canary.sh").unlink(), "tools/hook-canary.sh present"),
    "kb-guard-missing": (lambda r: (r / "tools" / "kb-guard.sh").unlink(), "tools/kb-guard.sh present"),
    "engine-missing": (lambda r: (r / "tools" / "skyguard" / "run.py").unlink(), "tools/skyguard/run.py present"),
    "generator-missing": (lambda r: (r / "scripts" / "gen_agent_trees.py").unlink(), "scripts/gen_agent_trees.py present"),
    "tests-shipped": (_mkdir("tests"), "tests NOT shipped"),
    "routes-harness-shipped": (_mkdir("tests/fixtures/routes-harness"), "tests/fixtures/routes-harness NOT shipped"),
    "github-shipped": (_mkdir(".github"), ".github NOT shipped"),
    "layer2-spike-shipped": (_mkdir("scripts/layer2-spike"), "scripts/layer2-spike NOT shipped"),
    "guard-corpus-shipped": (_mkdir("scripts/guard-corpus"), "scripts/guard-corpus NOT shipped"),
    "guard-latency-shipped": (lambda r: (r / "scripts" / "guard-latency.py").write_text("x"),
                              "scripts/guard-latency.py NOT shipped"),
    "git-shipped": (_mkdir(".git"), ".git NOT shipped"),
    "superpowers-shipped": (_mkdir(".superpowers"), ".superpowers NOT shipped"),
    "docs-superpowers-shipped": (_mkdir("docs/superpowers"), "docs/superpowers NOT shipped"),
    "skyguard-state-shipped": (_mkdir(".skyguard"), ".skyguard NOT shipped"),
    "local-kb-shipped": (lambda r: (r / "KNOWLEDGEBASE.local.md").write_text("mine"), "KNOWLEDGEBASE.local.md NOT shipped"),
    "backups-shipped": (_mkdir(".claude/backups"), ".claude/backups NOT shipped"),
    "settings-local-shipped": (lambda r: (r / ".claude" / "settings.local.json").write_text("{}"),
                               ".claude/settings.local.json NOT shipped"),
    "paths-env-shipped": (lambda r: (r / ".claude" / "skyrim-paths.env").write_text("X=1"),
                          "no file named skyrim-paths.env"),
    "paths-env-shipped-root": (lambda r: (r / "skyrim-paths.env").write_text("X=1"),    # Plan 8 T8: 4.0's location
                               "no file named skyrim-paths.env"),
    "paths-env-shipped-deep": (lambda r: (r / "agent" / "targets" / "skyrim-paths.env").write_text("X=1"),
                               "no file named skyrim-paths.env"),
    "nexus-key-shipped": (lambda r: (r / "tools" / ".nexus_api_key").write_text("k"), "no file named .nexus_api_key"),
    "claude-hook-missing": (lambda r: _first_claude_hook(r).unlink(), "Claude hook wrappers"),
    "codex-entry-missing": (lambda r: (r / ".codex" / "hooks" / "skyguard-entry.ps1").unlink(), "Codex hook entries"),
    "claude-hook-inert-stdin": (_inert_stdin, "reads stdin with a bare cat"),
    "claude-hook-heartbeat-in-comment": (_heartbeat_only_in_comment, "writes a heartbeat outside a comment"),
    "codex-sh-inert-stdin": (_codex_sh_inert, "skyguard-entry.sh reads stdin with a bare cat"),
    "codex-ps1-no-stdin": (_ps1_no_stdin, "skyguard-entry.ps1 reads the standard input stream"),
    "codex-ps1-no-heartbeat": (_ps1_no_heartbeat, "skyguard-entry.ps1 writes a heartbeat"),
    # Plan 6 (merge of master into plan6): the launchers README sends a user to, and the guard manifest.
    "skyguard-launcher-missing": (lambda r: (r / "tools" / "skyguard.sh").unlink(), "tools/skyguard.sh present"),
    "unlock-cmd-missing": (lambda r: (r / "tools" / "skyguard-unlock.cmd").unlink(), "tools/skyguard-unlock.cmd present"),
    "skydoctor-missing": (lambda r: (r / "tools" / "skydoctor").unlink(), "tools/skydoctor present"),
    "skyrim-paths-missing": (lambda r: (r / "tools" / "skyrim_paths.py").unlink(), "tools/skyrim_paths.py present"),
    "skse-load-status-missing": (lambda r: (r / "tools" / "skse-load-status.py").unlink(),
                                 "tools/skse-load-status.py present"),
    "archive-triage-missing": (lambda r: (r / "tools" / "archive-triage.py").unlink(), "tools/archive-triage.py present"),
    "mirror-logs-missing": (lambda r: (r / "tools" / "mirror-launch-logs.py").unlink(),
                            "tools/mirror-launch-logs.py present"),
    "housekeep-missing": (lambda r: (r / "tools" / "housekeep.py").unlink(), "tools/housekeep.py present"),
    "mod-install-missing": (lambda r: (r / "tools" / "mod-install.py").unlink(), "tools/mod-install.py present"),
    "nexus-sh-missing": (lambda r: (r / "tools" / "nexus.sh").unlink(), "tools/nexus.sh present"),
    "nexus-render-missing": (lambda r: (r / "tools" / "nexus-render.py").unlink(), "tools/nexus-render.py present"),
    "bench-vr-missing": (lambda r: (r / "tools" / "bench-vr.py").unlink(), "tools/bench-vr.py present"),
    "vr-headless-missing": (lambda r: (r / "tools" / "vr-headless.py").unlink(), "tools/vr-headless.py present"),
    "emu-build-shipped": (_mkdir("tools/vr-emu-driver/build"), "tools/vr-emu-driver/build NOT shipped"),
    "emu-work-shipped": (_mkdir("tools/vr-emu-driver/work"), "tools/vr-emu-driver/work NOT shipped"),
    # v4.0.0 ReSaver Plus: text only, the tested patch set, its Apache-2.0 notices, and none of the dev install's
    # local-only material.
    "resaver-plus-build-shipped": (_mkdir("tools/resaver-plus/build"), "tools/resaver-plus/build NOT shipped"),
    "resaver-plus-work-shipped": (_mkdir("tools/resaver-plus/work"), "tools/resaver-plus/work NOT shipped"),
    "resaver-plus-backend-shipped": (lambda r: (r / "tools" / "resaver-plus" / "backend").write_text("native"),
                                     "tools/resaver-plus/backend NOT shipped"),
    "resaver-plus-contributions-shipped": (_mkdir("tools/resaver-plus/contributions"),
                                           "tools/resaver-plus/contributions NOT shipped"),
    "resaver-port-shipped": (_mkdir("tools/resaver-port"), "tools/resaver-port NOT shipped"),
    "resaver-plus-binary-shipped": (lambda r: (r / "tools" / "resaver-plus" / "patches" / "resaverpp-cli.exe")
                                    .write_bytes(b"MZ"), "no binary under tools/resaver-plus"),
    "resaver-plus-pr-draft-shipped": (lambda r: (r / "tools" / "resaver-plus" / "analysis-PR.md").write_text("draft"),
                                      "no upstream PR draft"),
    "resaver-port-checker-shipped": (lambda r: (r / "tools" / "resaver-port-write-check.py").write_text("x"),
                                     "no ReSaver Plus qualification checker"),
    "resaver-plus-patch-missing": (lambda r: sorted((r / "tools" / "resaver-plus" / "patches").glob("*.patch"))[0]
                                   .unlink(), "carries the tested 27 patches"),
    "resaver-plus-license-missing": (lambda r: (r / "tools" / "resaver-plus" / "LICENSE").unlink(),
                                     "tools/resaver-plus/LICENSE present"),
    "resaver-plus-notice-missing": (lambda r: (r / "tools" / "resaver-plus" / "NOTICE.md").unlink(),
                                    "tools/resaver-plus/NOTICE.md present"),
    "resaver-plus-builder-missing": (lambda r: (r / "tools" / "resaver-plus" / "build.py").unlink(),
                                     "tools/resaver-plus/build.py present"),
    "guard-manifest-missing": (lambda r: (r / "GUARD-FILES.sha256").unlink(), "GUARD-FILES.sha256 present"),
    "guard-file-edited": (lambda r: (r / "tools" / "skyguard" / "engine.py").write_text(
        (r / "tools" / "skyguard" / "engine.py").read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8"),
        "guard files match GUARD-FILES.sha256"),
    "guard-file-dropped": (lambda r: (r / "tools" / "skyguard" / "self_rules.py").unlink(),
                           "guard files match GUARD-FILES.sha256"),
}


@pytest.mark.parametrize("brk", sorted(BREAKS))
def test_every_check_can_fail_on_its_own_line(payload, brk):
    damage, name = BREAKS[brk]
    damage(payload)
    r = _check(payload, "--expect-commit", SHA)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "!!" in _line(r.stdout, name), f"{brk}: the '{name}' line did not go red:\n{r.stdout}"


def test_the_hook_floor_is_the_tree_recounted():
    """6 Claude wrappers plus the Codex entries, counted from the tree, not retyped from an old workflow."""
    m = _mod()
    claude = sorted((REPO / ".claude" / "hooks").rglob("*.sh"))
    codex = sorted(p for p in (REPO / ".codex" / "hooks").rglob("*") if p.is_file())
    assert m.CLAUDE_HOOK_FLOOR == len(claude) == 6, claude
    assert m.CODEX_ENTRY_FLOOR == len(codex), codex
    assert m.HOOK_FLOOR == len(claude) + len(codex)
    sys.path.insert(0, str(REPO / "tools"))
    from skyguard import health
    assert len(health.CLAUDE_HOOKS) == m.CLAUDE_HOOK_FLOOR


def test_the_shape_lists_cover_what_the_builder_excludes():
    spec = importlib.util.spec_from_file_location("build_release_zip", REPO / "scripts" / "build_release_zip.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    assert set(b.EXCLUDE) <= set(_mod().ABSENT_PATHS)


def test_bad_usage_is_exit_2(tmp_path):
    assert _check(tmp_path / "nope").returncode == 2
    assert _check(tmp_path, "--section", "bogus").returncode == 2


# ------------------------------------------------------------------ leaks

NAME = "zqxuserq"   # a made-up account: this file never names a real person


def test_the_names_come_from_the_environment():
    m = _mod()
    assert m.leak_names({"USERNAME": NAME}, home="") == [NAME]
    assert m.leak_names({"USER": NAME}, home="") == [NAME]
    assert m.leak_names({}, home="C:" + m.BS + "Users" + m.BS + NAME) == [NAME]
    assert m.leak_names({"RELEASE_LEAK_NAMES": NAME + ",other"}, home="") == ["other", NAME]
    assert m.leak_names({}, home="") == []


@pytest.mark.parametrize("spelling", [
    "C:/Users/" + NAME + "/x", "C:" + "\\" + "Users" + "\\" + NAME + "\\x", '"C:\\\\Users\\\\' + NAME + '\\\\x"',
    "/c/Users/" + NAME + "/x", "/home/" + NAME + "/x", "c:/users/" + NAME.upper() + "/x", "hello " + NAME,
])
def test_every_spelling_of_the_home_is_found(tmp_path, spelling):
    m = _mod()
    (tmp_path / "f.md").write_text(spelling, encoding="utf-8")
    assert m.scan_leaks([tmp_path], m.leak_needles([NAME])), spelling


def test_utf16_is_read_too(tmp_path):
    m = _mod()
    (tmp_path / "f.txt").write_bytes(("C:/Users/" + NAME).encode("utf-16-le"))
    assert m.scan_leaks([tmp_path], m.leak_needles([NAME]))


def test_a_generic_account_is_searched_by_path_only(tmp_path):
    """On a CI runner the account is `runner`: the bare word is prose ("the CI runner"), its home is a leak."""
    m = _mod()
    (tmp_path / "prose.md").write_text("the CI runner runs it", encoding="utf-8")
    assert not m.scan_leaks([tmp_path], m.leak_needles(["runner"]))
    (tmp_path / "path.md").write_text("/home/runner/work", encoding="utf-8")
    assert [h[0].rsplit("/", 1)[-1].split(":")[0] for h in m.scan_leaks([tmp_path], m.leak_needles(["runner"]))] == ["path.md"]


def test_extra_paths_are_found_in_every_spelling(tmp_path):
    m = _mod()
    game = "D:/Games/Some Skyrim"
    for i, s in enumerate(("D:/Games/Some Skyrim/Data", "D:\\Games\\Some Skyrim", "/d/Games/Some Skyrim")):
        (tmp_path / "f{}.txt".format(i)).write_text(s, encoding="utf-8")
    assert len(m.scan_leaks([tmp_path], m.leak_needles([], [game]))) == 3


def test_an_allowed_prefix_is_blanked_but_anything_else_under_the_home_is_not(tmp_path):
    m = _mod()
    work = "C:/Users/" + NAME + "/AppData/Local/Temp/verify"
    (tmp_path / "gen.md").write_text("Game root: C:\\Users\\" + NAME + "\\AppData\\Local\\Temp\\verify\\game", encoding="utf-8")
    assert not m.scan_leaks([tmp_path], m.leak_needles([NAME]), [work])
    (tmp_path / "other.md").write_text("C:/Users/" + NAME + "/Desktop/secret", encoding="utf-8")
    hits = m.scan_leaks([tmp_path], m.leak_needles([NAME]), [work])
    assert [h[0].rsplit("/", 1)[-1].split(":")[0] for h in hits] == ["other.md"]


def _leaks(dirs, env, *extra):
    return subprocess.run([sys.executable, "-B", str(CHECKER), "--leaks", *map(str, dirs), *extra],
                          capture_output=True, text=True, timeout=120, env=env)


def _clean_env(**kv):
    env = {k: v for k, v in os.environ.items()
           if k not in ("USERNAME", "USER", "LOGNAME", "HOME", "USERPROFILE", "RELEASE_LEAK_NAMES", "RELEASE_LEAK_PATHS")}
    env.update(kv)
    return env


def test_leaks_mode_goes_red_and_names_the_file(tmp_path):
    (tmp_path / "a.md").write_text("see C:/Users/" + NAME + "/notes", encoding="utf-8")
    (tmp_path / "b.md").write_text("clean", encoding="utf-8")
    r = _leaks([tmp_path], _clean_env(USERNAME=NAME))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "a.md" in r.stdout and "b.md" not in r.stdout


def test_leaks_mode_is_green_on_a_clean_tree(tmp_path):
    (tmp_path / "b.md").write_text("clean", encoding="utf-8")
    r = _leaks([tmp_path], _clean_env(USERNAME=NAME))
    assert r.returncode == 0 and "LEAKS: none" in r.stdout, r.stdout + r.stderr


def test_leaks_mode_with_nothing_to_search_for_is_could_not_check(tmp_path):
    r = _leaks([tmp_path], _clean_env())
    assert r.returncode == 2 and "COULD NOT CHECK" in r.stdout, r.stdout + r.stderr


def test_leaks_mode_reads_release_leak_paths(tmp_path):
    (tmp_path / "a.md").write_text("E:\\Steam\\Skyrim VR\\Data", encoding="utf-8")
    r = _leaks([tmp_path], _clean_env(RELEASE_LEAK_PATHS="E:/Steam/Skyrim VR"))
    assert r.returncode == 1, r.stdout + r.stderr


def test_leaks_mode_honours_allow_prefix(tmp_path):
    (tmp_path / "a.md").write_text("/home/" + NAME + "/tmp/work/game", encoding="utf-8")
    env = _clean_env(USERNAME=NAME)
    assert _leaks([tmp_path], env).returncode == 1
    r = _leaks([tmp_path], env, "--allow-prefix", "/home/" + NAME + "/tmp/work")
    assert r.returncode == 0, r.stdout + r.stderr


# Release review T13 M3: an allowance is never the home folder, a folder above it, or a root -- it would blank the
# home paths the scan exists to find (a Python installed straight into the home folder measures exactly that).
_WINHOME = "C:/Users/" + NAME
_FLOOR = {   # case -> (allow-prefix, environment, extra args, planted text)
    "home": (_WINHOME, {"USERPROFILE": _WINHOME}, (), "C:\\Users\\" + NAME + "\\Desktop\\secret"),
    "home-trailing-slash": (_WINHOME + "/", {"USERPROFILE": _WINHOME}, (), "C:\\Users\\" + NAME + "\\Desktop\\x"),
    "msys-home": ("/c/Users/" + NAME, {"USERPROFILE": _WINHOME}, (), "C:\\Users\\" + NAME + "\\Desktop\\secret"),
    "above-home": ("C:/Users", {"USERPROFILE": _WINHOME}, (), "C:\\Users\\" + NAME + "\\Desktop\\secret"),
    "drive-root": ("C:/", {"USERPROFILE": _WINHOME}, (), "C:\\Users\\" + NAME + "\\Desktop\\secret"),
    "other-drive-root": ("D:/", {"USERPROFILE": _WINHOME}, (), "D:\\Users\\" + NAME + "\\Desktop\\secret"),
    "relative": (".", {"USERPROFILE": _WINHOME}, (), "C:\\Users\\" + NAME + "\\Desktop\\secret"),
    "posix-home": ("/home/" + NAME, {"HOME": "/home/" + NAME}, (), "/home/" + NAME + "/secret"),
    "home-argument": (_WINHOME, {}, ("--home", _WINHOME), "C:\\Users\\" + NAME + "\\Desktop\\secret"),
}


@pytest.mark.parametrize("case", sorted(_FLOOR))
def test_an_allow_prefix_at_or_above_the_home_or_a_root_is_never_applied(tmp_path, case):
    prefix, env, extra, text = _FLOOR[case]
    (tmp_path / "a.md").write_text(text, encoding="utf-8")
    r = _leaks([tmp_path], _clean_env(USERNAME=NAME, **env), *extra, "--allow-prefix", prefix)
    assert r.returncode == 1 and "an --allow-prefix was NOT applied" in r.stdout, r.stdout + r.stderr
    if len(prefix) > 3:                                   # a root or `.` is not an identity, and appears in prose
        assert prefix.rstrip("/").lower() not in r.stdout.lower(), "the refused path was printed"


def test_an_allow_prefix_below_the_home_is_still_applied(tmp_path):
    py = _WINHOME + "/AppData/Local/Programs/Python/Python310"
    (tmp_path / "a.md").write_text(py.replace("/", "\\") + "\\python.exe", encoding="utf-8")
    env = _clean_env(USERNAME=NAME, USERPROFILE=_WINHOME)
    assert _leaks([tmp_path], env).returncode == 1
    r = _leaks([tmp_path], env, "--allow-prefix", py)
    assert r.returncode == 0 and "NOT applied" not in r.stdout, r.stdout + r.stderr


def test_the_checker_itself_names_no_one():
    """The name is read at check time. A literal one in the repo would be the very leak this hunts."""
    text = CHECKER.read_text(encoding="utf-8").lower()
    for k in ("USERNAME", "USER", "LOGNAME"):
        n = os.environ.get(k, "")
        if len(n) >= 4 and n.lower() not in _mod().GENERIC_ACCOUNTS:
            assert n.lower() not in text, k
    assert json.dumps  # keep the import honest for linters


def test_changed_from_skips_only_byte_identical_files(tmp_path):
    """An installed copy is grepped for what setup GENERATED or changed; the payload's own files were grepped once."""
    m = _mod()
    base, inst = tmp_path / "payload", tmp_path / "install"
    for d in (base, inst):
        d.mkdir()
    leak = "C:/Users/" + NAME + "/x"
    (base / "same.md").write_text(leak, encoding="utf-8")
    (inst / "same.md").write_text(leak, encoding="utf-8")
    (base / "changed.md").write_text("clean", encoding="utf-8")
    (inst / "changed.md").write_text(leak, encoding="utf-8")
    (inst / "new.md").write_text(leak, encoding="utf-8")
    hits = sorted(h[0].rsplit("/", 1)[-1].split(":")[0] for h in m.scan_leaks([inst], m.leak_needles([NAME]), baseline=base))
    assert hits == ["changed.md", "new.md"], hits


GENERIC = "runneradmin"   # a CI account: only its home-path spellings are searched, so each must stand alone


@pytest.mark.parametrize("spelling", [
    "C:/Users/" + GENERIC + "/x", "C:" + chr(92) + "Users" + chr(92) + GENERIC + chr(92) + "x",
    '"C:' + chr(92) * 2 + "Users" + chr(92) * 2 + GENERIC + '"', "/c/Users/" + GENERIC + "/x", "/home/" + GENERIC + "/x",
])
def test_every_home_spelling_is_found_without_the_bare_name(tmp_path, spelling):
    """The bare name would mask a missing spelling for a person; for a generic account each spelling must hold."""
    m = _mod()
    assert GENERIC in m.GENERIC_ACCOUNTS
    (tmp_path / "f.md").write_text(spelling, encoding="utf-8")
    assert m.scan_leaks([tmp_path], m.leak_needles([GENERIC])), spelling


# ------------------------------------------------------------------ fix round 1 (review I1, I2, I3, M3, M5, M8)

def test_leak_tokens_split_names_from_paths():
    m = _mod()
    names, paths = m.leak_tokens({"LEAK_TOKENS": NAME + "\r\nD:/Games/My Skyrim\n\nE:" + m.BS + "x\n"})
    assert names == [NAME] and paths == ["D:/Games/My Skyrim", "E:" + m.BS + "x"]
    assert m.leak_tokens({}) == ([], [])


def test_require_tokens_fails_closed_without_them(tmp_path):
    """Review I1: on a CI runner the environment names `runner`; a release grep without the maintainer's tokens
    must refuse, never pass."""
    (tmp_path / "a.md").write_text("clean", encoding="utf-8")
    r = _leaks([tmp_path], _clean_env(USERNAME="runner"), "--require-tokens")
    assert r.returncode == 2 and "LEAK_TOKENS" in r.stdout, r.stdout + r.stderr
    r = _leaks([tmp_path], _clean_env(USERNAME="runner", LEAK_TOKENS="  \n "), "--require-tokens")
    assert r.returncode == 2, r.stdout


def test_a_token_name_is_found_even_when_the_runner_is_generic(tmp_path):
    (tmp_path / "a.md").write_text("notes by " + NAME, encoding="utf-8")
    r = _leaks([tmp_path], _clean_env(USERNAME="runner", LEAK_TOKENS=NAME), "--require-tokens")
    assert r.returncode == 1 and "a.md" in r.stdout, r.stdout
    (tmp_path / "a.md").write_text("see D:/Games/My Skyrim VR/Data", encoding="utf-8")
    r = _leaks([tmp_path], _clean_env(USERNAME="runner", LEAK_TOKENS="D:/Games/My Skyrim VR"), "--require-tokens")
    assert r.returncode == 1, r.stdout


FAKE_SECRET = "zz" + "Q9fakeSECRETvalue" + "0123"   # synthetic, built here: no real key is in this repo


def test_a_secret_value_is_found_and_never_printed(tmp_path):
    """Review I3a: the machine's own key values, read at check time; the report names file and line only."""
    (tmp_path / "a.md").write_text("line one\nkey = " + FAKE_SECRET + "\n", encoding="utf-8")
    r = _leaks([tmp_path], _clean_env(USERNAME=NAME, X4_NEXUS_KEY=FAKE_SECRET))
    assert r.returncode == 1, r.stdout
    assert "a.md:2: secret value of X4_NEXUS_KEY" in r.stdout
    assert FAKE_SECRET not in r.stdout + r.stderr


def test_the_nexus_key_file_is_read(tmp_path):
    m = _mod()
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / ".nexus_api_key").write_text(FAKE_SECRET + "\n", encoding="utf-8")
    assert m.secret_values(env={}, checkout=tmp_path) == [("secret value of tools/.nexus_api_key", FAKE_SECRET)]
    assert m.secret_values(env={"GH_TOKEN": "short"}, checkout=tmp_path / "none") == []


def _shape(kind):
    a = "A" * 4 + "b" * 4
    return {
        "nexus": a * 4 + "==" + "--" + a * 2 + "--" + a * 2 + "==",
        "github-classic": "gh" + "p_" + a * 5,
        "github-pat": "github" + "_pat_" + a * 6,
        "openai": "s" + "k-" + "proj-" + a * 4,
        "aws": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    }[kind]


@pytest.mark.parametrize("kind", ["nexus", "github-classic", "github-pat", "openai", "aws"])
def test_every_key_shape_is_found(tmp_path, kind):
    """Review I3b: a key this machine does not hold, by its shape. Synthetic values, assembled at run time."""
    m = _mod()
    (tmp_path / "f.md").write_text("x\nconfig " + _shape(kind) + " end\n", encoding="utf-8")
    hits = m.scan_leaks([tmp_path], [])
    assert hits and hits[0][0].endswith("f.md:2"), (kind, hits)


@pytest.mark.parametrize("text", [
    "bash tools/verify-release.sh --local x.zip --scrubbed --expect-commit abc",
    "the task-runner skips; disk-check; ask-me-anything-about-the-long-flag-names",
    "AKIA is the AWS prefix; ghp_ is GitHub's",
])
def test_ordinary_text_has_no_key_shape(tmp_path, text):
    m = _mod()
    (tmp_path / "f.md").write_text(text, encoding="utf-8")
    assert m.scan_leaks([tmp_path], []) == []


@pytest.mark.parametrize("cfg,ok", [
    ("C:/Users/" + NAME + "/Documents/My Games/Skyrim VR", True),
    ("C:/Users/" + NAME + "/OneDrive/Documents/My Games/Skyrim Special Edition", True),
    ("C:/Users/" + NAME, False),                                     # the home itself
    ("C:/Users/" + NAME + "/Desktop", False),                        # an odd folder
    ("C:/Users/" + NAME + "/Documents", False),                      # too shallow
    ("C:/Users/" + NAME + "/Documents/My Games/Skyrim VR/../../..", False),
    ("D:/Elsewhere/Documents/My Games/Skyrim VR", False),            # not below the home
    ("C:/Users/" + NAME + "XY/Documents/My Games/Skyrim VR", False),  # a sibling profile sharing the prefix
])
def test_the_config_folder_is_allow_listed_only_in_its_expected_shape(cfg, ok):
    """Review I2: the folder comes from setup's own output, so it is validated before it may hide anything."""
    m = _mod()
    assert m.config_dir_ok(cfg, "C:/Users/" + NAME) is ok
    assert m.config_dir_ok(cfg.replace("/", m.BS), "C:" + m.BS + "Users" + m.BS + NAME) is ok


def test_a_home_level_config_hides_nothing(tmp_path):
    """The circular allow-list the review measured: setup records the HOME as the config folder, and a file
    naming <home>/.ssh then passed. It must show."""
    home = "C:/Users/" + NAME
    (tmp_path / "gen.md").write_text(home + "/.ssh/id_rsa", encoding="utf-8")
    env = _clean_env(USERNAME=NAME)
    r = _leaks([tmp_path], env, "--allow-config", home, "--home", home)
    assert r.returncode == 1 and "NOT allow-listed" in r.stdout, r.stdout
    good = home + "/Documents/My Games/Skyrim VR"
    (tmp_path / "gen.md").write_text(good + "/Skyrim.ini", encoding="utf-8")
    r = _leaks([tmp_path], env, "--allow-config", good, "--home", home)
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("order", ["win-first", "posix-first"])
def test_the_linux_runner_config_folder_is_allow_listed_and_nothing_else(tmp_path, order):
    """CI run 38178385999 (ubuntu-latest): setup.sh cannot ask Windows for Documents there, so it writes its fallback
    C:/Users/<login>/Documents/My Games/Skyrim VR, while the real home is /home/<login>. verify-release passes BOTH
    homes; the folder is allow-listed when it has the validated shape below EITHER, in whichever order they come.
    Planted controls: any other path under either home is still a leak."""
    name = "runner"
    win, posix = "C:/Users/" + name, "/home/" + name
    homes = (win, posix) if order == "win-first" else (posix, win)
    cfg = win + "/Documents/My Games/Skyrim VR"
    home_args = ["--home", homes[0], "--home", homes[1]]
    env = _clean_env(USERNAME=name)
    (tmp_path / "gen.md").write_text("- configs: `" + cfg + "/` (Skyrim.ini)\n", encoding="utf-8")
    r = _leaks([tmp_path], env, "--allow-config", cfg, *home_args)
    assert r.returncode == 0 and "allow-listed the config folder" in r.stdout, r.stdout + r.stderr
    for planted in (posix + "/.ssh/id_rsa", win + "/Desktop/secret.txt"):
        (tmp_path / "planted.md").write_text("x\n" + planted + "\n", encoding="utf-8")
        r = _leaks([tmp_path], env, "--allow-config", cfg, *home_args)
        assert r.returncode == 1 and "planted.md:2" in r.stdout, (planted, r.stdout)
    (tmp_path / "planted.md").unlink()
    # The home setup did not write under is no allowance on its own (the shape is validated, never assumed).
    r = _leaks([tmp_path], env, "--allow-config", cfg, "--home", posix)
    assert r.returncode == 1 and "NOT allow-listed" in r.stdout, r.stdout


@pytest.mark.parametrize("spelling", ["D:/My%20Games/Some%20Skyrim/x", "D:" + "\\" + "/My Games" + "\\" + "/Some Skyrim"])
def test_url_and_json_slash_spellings_of_a_path(tmp_path, spelling):
    m = _mod()
    (tmp_path / "f.json").write_text(spelling, encoding="utf-8")
    assert m.scan_leaks([tmp_path], m.leak_needles([], ["D:/My Games/Some Skyrim"])), spelling


def test_utf16_big_endian_is_read_too(tmp_path):
    m = _mod()
    (tmp_path / "f.txt").write_bytes(("C:/Users/" + NAME).encode("utf-16-be"))
    assert m.scan_leaks([tmp_path], m.leak_needles([NAME]))


@pytest.mark.parametrize("rel", [".env", "tools/local.env", "config/.env.production", "x/app.env.local"])
def test_a_dotenv_file_is_refused(payload, rel):
    """Review M8: only two exact names were refused; a committed .env would have shipped its secrets."""
    p = payload / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("K=v", encoding="utf-8")
    r = _check(payload, "--section", "shape")
    assert r.returncode == 1 and "!!" in _line(r.stdout, "no dotenv-style file"), r.stdout


def test_an_example_env_is_not_a_dotenv(payload):
    (payload / "x.env.example").write_text("K=v", encoding="utf-8")
    assert "!!" not in _line(_check(payload, "--section", "shape").stdout, "no dotenv-style file")


def test_a_registered_hook_missing_from_the_payload_is_named(payload):
    """Review M5: counted is not named. A stray extra *.sh must not stand in for a registered hook."""
    (payload / ".claude" / "hooks" / "protect-mcp.sh").unlink()
    (payload / ".claude" / "hooks" / "stray.sh").write_text("INPUT=$(cat)\n: hook-heartbeat\n", encoding="utf-8")
    r = _check(payload, "--section", "hooks")
    line = _line(r.stdout, ".claude/settings.json registers the hooks")
    assert r.returncode == 1 and "!!" in line and "protect-mcp.sh" in line, r.stdout


# ------------------------------------------------------------------ fix round 2 (re-review N1, N3)

TOKEN_NAME = "Qorvath"                          # synthetic identity: never the maintainer's
TOKEN_PATH = "D:" + "\\" + "Qgames" + "\\" + "Skyrim Zelvane Edition"   # shares no text with TOKEN_NAME


def _all_spellings():
    m = _mod()
    out = {TOKEN_NAME, TOKEN_PATH} | set(m.path_spellings(TOKEN_PATH))
    out |= {"Users/" + TOKEN_NAME, "Users" + m.BS + TOKEN_NAME, "/home/" + TOKEN_NAME}
    return {x.lower() for x in out}


def test_a_hit_never_prints_the_identity_in_any_spelling(tmp_path):
    """Re-review N1: a failing CI run must not publish the identity the gate guards. Every spelling is planted,
    each in its own file; the report names file:line and `identity token #k (kind)` only."""
    m = _mod()
    for i, (kind, sp) in enumerate(m.path_spelling_kinds(TOKEN_PATH)):
        (tmp_path / "p{}.md".format(i)).write_text("x\nsee " + sp + "/Data\n", encoding="utf-8")
    (tmp_path / "n.md").write_text("by " + TOKEN_NAME + " at C:/Users/" + TOKEN_NAME + "/x", encoding="utf-8")
    env = _clean_env(USERNAME="runner", LEAK_TOKENS=TOKEN_NAME + "\n" + TOKEN_PATH)
    r = _leaks([tmp_path], env, "--require-tokens", "--allow-config", "C:/Users/runner", "--home", "C:/Users/runner")
    assert r.returncode == 1, r.stdout + r.stderr
    out = (r.stdout + r.stderr).lower()
    assert "identity token #" in out and ":2: identity token" in out
    leaked = sorted(sp for sp in _all_spellings() if sp in out)
    assert not leaked, "the report printed identity text ({} spelling(s))".format(len(leaked))


def test_a_file_name_holding_the_identity_is_withheld(tmp_path):
    (tmp_path / (TOKEN_NAME + "-notes.md")).write_text("hello " + TOKEN_NAME, encoding="utf-8")
    r = _leaks([tmp_path], _clean_env(USERNAME="runner", LEAK_TOKENS=TOKEN_NAME))
    assert r.returncode == 1 and "withheld" in r.stdout and TOKEN_NAME.lower() not in r.stdout.lower(), r.stdout


def test_the_config_folder_is_never_printed(tmp_path):
    cfg = "C:/Users/" + TOKEN_NAME + "/Documents/My Games/Skyrim VR"
    (tmp_path / "a.md").write_text("clean", encoding="utf-8")
    for home in ("C:/Users/" + TOKEN_NAME, "C:/Users/other"):
        r = _leaks([tmp_path], _clean_env(USERNAME="runner", LEAK_TOKENS=TOKEN_NAME), "--allow-config", cfg, "--home", home)
        assert TOKEN_NAME.lower() not in (r.stdout + r.stderr).lower(), r.stdout


def test_mask_lines_cover_every_token_and_spelling():
    """What release.yml prints (as ::add-mask:: commands, which GitHub consumes) before any step can print."""
    m = _mod()
    masked = {ln[len("::add-mask::"):].lower() for ln in m.mask_lines({"LEAK_TOKENS": TOKEN_NAME + "\n" + TOKEN_PATH})}
    assert _all_spellings() <= masked, sorted(_all_spellings() - masked)
    assert m.mask_lines({}) == []


@pytest.mark.parametrize("tokens,bad", [
    ("abc", "token #1"), ("----", "token #1"), (":", "token #1"), ("/", "token #1"), ("C:" + "\\", "token #1"),
    ("C:" + "\\" + "Games", "token #1"), (TOKEN_NAME + "\n..", "token #2"),
])
def test_a_vague_token_is_rejected_by_number_never_text(tmp_path, tokens, bad):
    """Re-review N3: a 3-letter name or a bare separator matches nearly every file and blocks every release."""
    (tmp_path / "a.md").write_text("clean", encoding="utf-8")
    r = _leaks([tmp_path], _clean_env(USERNAME="runner", LEAK_TOKENS=tokens), "--require-tokens")
    assert r.returncode == 2 and bad in r.stdout, r.stdout


def test_sound_tokens_pass_the_floor():
    assert _mod().token_problems({"LEAK_TOKENS": TOKEN_NAME + "\n" + TOKEN_PATH + "\n/home/" + TOKEN_NAME}) == []
