"""scripts/thirdparty-audit.py: the third-party version and integration-drift gate (Plan 8 T15b).

Everything here runs OFFLINE: upstream answers are synthetic (tests/fixtures/thirdparty-audit/responses.json, plus
zips built in memory), loaded into a temp response cache that the tool reads with --offline -- the same path a real
offline run takes. No real key is used; the one "secret" below is a made-up string the tool must never write out.

The four scenarios the brief names, each with a control that is green so the red means something:
  a newer upstream -> reported (NEWER), not failed          test_a_newer_upstream_is_reported_not_failed
  a pinned path missing upstream -> exit 1                  test_a_documented_path_the_release_no_longer_ships_is_drift
  an unreachable source -> COULD NOT CHECK, exit 2          test_an_unreachable_source_is_could_not_check_with_exit_2
  a README command differing from the pin file -> exit 1    test_a_readme_install_version_that_differs_from_the_pin_is_drift
"""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import urllib.error
import zipfile

import pytest

from conftest import REPO

SCRIPT = REPO / "scripts" / "thirdparty-audit.py"
FIXTURES = REPO / "tests" / "fixtures" / "thirdparty-audit" / "responses.json"
FAKE_SECRET = "FAKE-nexus-key-0123456789abcdef"   # synthetic; proves redaction, is no one's key


def _load():
    spec = importlib.util.spec_from_file_location("thirdparty_audit", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ta = _load()


def _zip(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, b"" if n.endswith("/") else b"x")
    return buf.getvalue()


def make_cache(tmp_path, drop=(), extra=()):
    """The synthetic responses as a response cache; `drop` removes URLs, `extra` adds (url, status, body)."""
    cache = tmp_path / "cache"
    f = ta.Fetcher(cache, offline=True, env={})
    for e in json.loads(FIXTURES.read_text(encoding="utf-8"))["responses"]:
        if e["url"] in drop:
            continue
        if "zip" in e:
            body = _zip(e["zip"])
        elif "json" in e:
            body = json.dumps(e["json"]).encode()
        else:
            body = e["text"].encode()
        f.put(e["url"], e["status"], body, e.get("headers"), e.get("range"))
    for url, status, body, *rng in extra:
        f.put(url, status, body, None, rng[0] if rng else None)
    return cache


def make_repo(tmp_path, files):
    root = tmp_path / "repo"
    for rel, text in files.items():
        p = root / rel
        if text is None:
            p.mkdir(parents=True, exist_ok=True)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    root.mkdir(exist_ok=True)
    return root


def audit(repo, cache, only):
    rows = ta.run(repo, ta.Fetcher(cache, offline=True, env={}), only)
    return rows, ta.exit_code(rows)


def cli(repo, cache, *args):
    proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "--repo", str(repo), "--cache-dir", str(cache),
                           "--offline", *args], capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout + proc.stderr


def by(rows, status):
    return [r for r in rows if r.status == status]


# ---------------------------------------------------------------- synthetic trees

CODEX = {
    "tests/fixtures/codex/0.160.0": None,
    "tests/fixtures/payloads/codex/0.160.0": None,
    "tools/skyguard/codex_trust.py": '"""MEASURED on codex-cli 0.160.0: reproduces the hashes."""\n',
}

PYNIFLY_DOC = ("- **PyNifly** -- download `io_scene_nifly.zip` from the latest release of PyNifly and extract it so "
               "the DLL lands at `tools/pynifly/{path}` (the version this was verified with: 29.1.0).\n")
PYNIFLY_SKILL = "Verified on **PyNifly 29.1.0**: the DLL ships with it.\n"


def pynifly_repo(tmp_path, path):
    return make_repo(tmp_path, {"docs/installing-tools.md": PYNIFLY_DOC.format(path=path),
                                "agent/skills/skyrim-nif/SKILL.md": PYNIFLY_SKILL})


SPRIGGIT_BASE = {
    ".config/dotnet-tools.json": json.dumps({"version": 1, "tools": {"spriggit.cli": {"version": "0.41.0"}}}),
    ".devcontainer/Dockerfile": "RUN ./dotnet-install.sh --channel 10.0 --install-dir /usr/local/dotnet\n",
    ".github/workflows/ci.yml": "      - with:\n          dotnet-version: \"10.0.x\"\n",
    "docs/installing-tools.md": ("spriggit serialize --InputPath a --OutputPath b --GameRelease SkyrimSE "
                                 "--PackageName Spriggit.Yaml --PackageVersion \"0.41.0\"\n"),
}


def spriggit_repo(tmp_path, readme_line, extra=None):
    files = dict(SPRIGGIT_BASE, **{"README.md": readme_line + "\n"})
    files.update(extra or {})
    return make_repo(tmp_path, files)


# ------------------------------------------------------------------ 1. a newer upstream is reported, never failed

def test_a_newer_upstream_is_reported_not_failed(tmp_path):
    repo = make_repo(tmp_path, CODEX)
    rows, code = audit(repo, make_cache(tmp_path), ["Codex CLI"])
    newer = by(rows, ta.NEWER)
    assert [(r.pinned, r.upstream) for r in newer] == [("0.160.0", "0.161.0")], [r.as_dict() for r in rows]
    assert "re-capture" in newer[0].detail
    assert code == 0 and not by(rows, ta.DRIFT) and not by(rows, ta.UNCHECKED)


def test_the_same_version_upstream_is_ok(tmp_path):
    repo = make_repo(tmp_path, CODEX)
    cache = make_cache(tmp_path, extra=[("https://registry.npmjs.org/@openai/codex/latest", 200, b'{"version":"0.160.0"}')])
    rows, code = audit(repo, cache, ["Codex CLI"])
    assert code == 0 and [r.status for r in rows] == [ta.OK]


def test_a_pin_ahead_of_upstream_is_drift():
    assert ta.version_status("0.42.0", "0.41.0")[0] == ta.DRIFT
    assert ta.version_status("0.41.0", "0.42.0")[0] == ta.NEWER
    assert ta.version_status("v1.11.2", "1.11.2")[0] == ta.OK


# ------------------------------------------------------------------ 2. a documented path the release no longer ships

def test_a_documented_path_the_release_no_longer_ships_is_drift(tmp_path):
    """The manual audit's B2: docs sent users to io_scene_nifly/pyn/NiflyDLL.dll after 29.x moved it."""
    repo = pynifly_repo(tmp_path, "io_scene_nifly/pyn/NiflyDLL.dll")
    rows, code = audit(repo, make_cache(tmp_path), ["PyNifly"])
    drift = by(rows, ta.DRIFT)
    assert code == 1, [r.as_dict() for r in rows]
    assert len(drift) == 1 and "io_scene_nifly/pyn/NiflyDLL.dll not in V29.1.0/io_scene_nifly.zip" in drift[0].detail
    assert "docs/installing-tools.md:1" in drift[0].detail


def test_a_documented_path_the_release_ships_is_ok(tmp_path):
    repo = pynifly_repo(tmp_path, "io_scene_nifly/NiflyDLL.dll")
    rows, code = audit(repo, make_cache(tmp_path), ["PyNifly"])
    assert code == 0, [r.as_dict() for r in rows]
    assert any(r.check == "upstream path" and r.status == ta.OK for r in rows)


def test_a_historical_path_mention_is_not_an_instruction(tmp_path):
    text = (PYNIFLY_DOC.format(path="io_scene_nifly/NiflyDLL.dll")
            + "Releases before 29.x need an explicit load first:\n"
            + "    pynifly.NifFile.Load('tools/pynifly/io_scene_nifly/pyn/NiflyDLL.dll')\n")
    repo = make_repo(tmp_path, {"docs/installing-tools.md": text, "agent/skills/skyrim-nif/SKILL.md": PYNIFLY_SKILL})
    rows, code = audit(repo, make_cache(tmp_path), ["PyNifly"])
    assert code == 0, [r.as_dict() for r in rows]


def test_a_history_word_far_from_the_reference_does_not_hide_it(tmp_path):
    """The history window is narrow: 'was' at the start of a long sentence does not excuse a wrong path at its end."""
    text = PYNIFLY_DOC.format(path="io_scene_nifly/pyn/NiflyDLL.dll").replace(
        "- **PyNifly** --", "- **PyNifly** (this was reorganised) -- a long sentence that keeps going and going --")
    repo = make_repo(tmp_path, {"docs/installing-tools.md": text, "agent/skills/skyrim-nif/SKILL.md": PYNIFLY_SKILL})
    rows, code = audit(repo, make_cache(tmp_path), ["PyNifly"])
    assert code == 1, [r.as_dict() for r in rows]


def test_a_new_clause_after_a_semicolon_is_not_history(tmp_path):
    """'was' in the previous clause does not make the next clause's instruction historical."""
    text = ("The layout was tested; the DLL lands at `tools/pynifly/io_scene_nifly/pyn/NiflyDLL.dll`. "
            "Get the latest release of PyNifly (the version this was verified with: 29.1.0).\n")
    repo = make_repo(tmp_path, {"docs/installing-tools.md": text, "agent/skills/skyrim-nif/SKILL.md": PYNIFLY_SKILL})
    rows, code = audit(repo, make_cache(tmp_path), ["PyNifly"])
    assert code == 1 and any("pyn/NiflyDLL.dll" in r.detail for r in by(rows, ta.DRIFT)), [r.as_dict() for r in rows]


def test_a_historical_install_mention_is_skipped_and_counted(tmp_path):
    repo = spriggit_repo(tmp_path, "v3 was installed with `dotnet tool install Spriggit.CLI --version 0.40.0`.\n"
                                   "Install: `dotnet tool install Spriggit.CLI --version 0.41.0`")
    rows, code = audit(repo, make_cache(tmp_path), ["Spriggit"])
    assert code == 0, [r.as_dict() for r in rows]
    assert any(r.check == "install refs" and "1 historical mention(s) skipped" in r.detail for r in rows)


def test_a_release_without_the_documented_asset_is_drift(tmp_path):
    url = "https://api.github.com/repos/BadDogSkyrim/PyNifly/releases/tags/V29.1.0"
    cache = make_cache(tmp_path, drop={url}, extra=[(url, 200, b'{"assets":[{"name":"pynifly-src.zip"}]}')])
    rows, code = audit(pynifly_repo(tmp_path, "io_scene_nifly/NiflyDLL.dll"), cache, ["PyNifly"])
    assert code == 1 and any("no longer ships io_scene_nifly.zip" in r.detail for r in by(rows, ta.DRIFT))


def test_install_from_latest_when_latest_is_not_the_verified_version_is_drift(tmp_path):
    url = "https://api.github.com/repos/BadDogSkyrim/PyNifly/releases/latest"
    cache = make_cache(tmp_path, drop={url}, extra=[
        (url, 200, b'{"tag_name":"V29.2.0"}'),
        ("https://api.github.com/repos/BadDogSkyrim/PyNifly/releases/tags/V29.2.0", 200, b'{"assets":[{"name":"io_scene_nifly.zip"}]}'),
        ("https://github.com/BadDogSkyrim/PyNifly/releases/download/V29.2.0/io_scene_nifly.zip", 200,
         _zip(["io_scene_nifly/NiflyDLL.dll"]), "0-0")])
    rows, code = audit(pynifly_repo(tmp_path, "io_scene_nifly/NiflyDLL.dll"), cache, ["PyNifly"])
    assert code == 1
    assert any(r.check == "install = pin" and "verified version is 29.1.0" in r.detail for r in by(rows, ta.DRIFT))


# ------------------------------------------------------------------ 3. unreachable / not checkable -> exit 2

def _unreachable(url, headers, secret):
    raise urllib.error.URLError("[Errno 11001] getaddrinfo failed")


def test_an_unreachable_source_is_could_not_check_with_exit_2(tmp_path):
    repo = make_repo(tmp_path, CODEX)
    f = ta.Fetcher(tmp_path / "empty-cache", transport=_unreachable, env={}, sleep=lambda s: None)
    rows = ta.run(repo, f, ["Codex CLI"])
    assert [r.status for r in rows] == [ta.UNCHECKED]
    assert "unreachable: registry.npmjs.org" in rows[0].detail
    assert ta.exit_code(rows) == 2
    assert f.calls == 2   # one retry, then a reason -- never a silent pass


def test_offline_without_a_cached_answer_is_could_not_check(tmp_path):
    repo = make_repo(tmp_path, CODEX)
    rows, code = audit(repo, tmp_path / "empty-cache", ["Codex CLI"])
    assert code == 2 and rows[0].status == ta.UNCHECKED and "offline and not cached" in rows[0].detail


def test_the_whole_registry_offline_with_no_cache_is_never_ok(tmp_path):
    """Every integration, nothing reachable: not one row may read OK/NEWER/UNPINNED, and the exit is 2."""
    repo = make_repo(tmp_path, dict(CODEX, **SPRIGGIT_BASE))
    code, out = cli(repo, tmp_path / "empty-cache")
    assert code == 2, out
    body = out.split("\n\n")[0].splitlines()[1:]
    assert body and all("COULD NOT CHECK" in line or " OK " in line for line in body), out
    assert all("install refs" in line for line in body if " OK " in line), out   # only local, network-free rows


def test_nothing_audited_is_not_ok(tmp_path):
    assert ta.exit_code([]) == 2
    rows, code = audit(make_repo(tmp_path, {}), tmp_path / "c", ["no such tool"])
    assert code == 2 and rows[0].status == ta.UNCHECKED and "--only names no known integration" in rows[0].detail


def test_an_unreadable_declared_pin_site_is_could_not_check(tmp_path):
    files = dict(CODEX)
    files["tools/skyguard/codex_trust.py"] = '"""The prose was reworded; no version here."""\n'
    rows, code = audit(make_repo(tmp_path, files), make_cache(tmp_path), ["Codex CLI"])
    assert code == 2
    assert any(r.check == "pin sites" and "codex_trust.py" in r.detail for r in by(rows, ta.UNCHECKED))


def test_rate_limit_exhaustion_stops_calling_the_host(tmp_path):
    calls = []

    def limited(url, headers, secret):
        calls.append(url)
        return ta.Response(403, b"{}", {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1791257090"})
    f = ta.Fetcher(tmp_path / "c", transport=limited, env={}, sleep=lambda s: None)
    for _ in range(3):
        with pytest.raises(ta.CouldNotCheck, match="rate limit exhausted"):
            f.get("https://api.github.com/repos/a/b/releases/latest")
    assert len(calls) == 1


# ------------------------------------------------------------------ 4. install commands vs the pin files

def test_a_readme_install_version_that_differs_from_the_pin_is_drift(tmp_path):
    repo = spriggit_repo(tmp_path, "Install: `dotnet tool install Spriggit.CLI --version 0.40.0`")
    rows, code = audit(repo, make_cache(tmp_path), ["Spriggit"])
    assert code == 1, [r.as_dict() for r in rows]
    assert [r.detail for r in by(rows, ta.DRIFT)] == ["README.md:1 says 0.40.0"]


def test_a_readme_install_matching_the_pin_is_ok(tmp_path):
    repo = spriggit_repo(tmp_path, "Install: `dotnet tool install Spriggit.CLI --version 0.41.0`")
    rows, code = audit(repo, make_cache(tmp_path), ["Spriggit"])
    assert code == 0, [r.as_dict() for r in rows]
    checks = {r.check for r in rows if r.status == ta.OK}
    assert {"version", "pin published", "install refs", "cli flags", "nupkg tfm"} <= checks


def test_an_unpinned_install_of_a_pinned_tool_is_drift(tmp_path):
    """The manual audit's B1: a fresh user following an unpinned command gets whatever is newest."""
    repo = spriggit_repo(tmp_path, "Install: `dotnet tool install Spriggit.CLI`")
    rows, code = audit(repo, make_cache(tmp_path), ["Spriggit"])
    assert code == 1
    assert [r.detail for r in by(rows, ta.DRIFT)] == ["README.md:1 Spriggit.CLI install without --version"]


def test_an_install_line_filled_from_the_pin_variable_is_ok(tmp_path):
    repo = spriggit_repo(tmp_path, "x", {"tools/w.sh": 'die "or dotnet tool install -g Spriggit.CLI --version $PKGVER"\n'})
    rows, code = audit(repo, make_cache(tmp_path), ["Spriggit"])
    assert code == 0, [r.as_dict() for r in rows]


def test_a_documented_cli_flag_missing_upstream_is_drift(tmp_path):
    repo = spriggit_repo(tmp_path, "Run `spriggit serialize --InputPath a --OutputPath b --NoSuchFlag`")
    rows, code = audit(repo, make_cache(tmp_path), ["Spriggit"])
    assert code == 1 and any("--NoSuchFlag" in r.detail for r in by(rows, ta.DRIFT))


def test_a_package_without_the_pinned_sdk_framework_is_drift(tmp_path):
    files = dict(SPRIGGIT_BASE)
    repo = spriggit_repo(tmp_path, "x", {".devcontainer/Dockerfile": "RUN x --channel 9.0\n",
                                         ".github/workflows/ci.yml": files[".github/workflows/ci.yml"].replace("10.0", "9.0")})
    rows, code = audit(repo, make_cache(tmp_path), ["Spriggit"])
    assert code == 1 and any("ships no tools/net9.0/" in r.detail for r in by(rows, ta.DRIFT))


def test_pin_sites_that_disagree_are_drift(tmp_path):
    files = dict(CODEX)
    files["tests/fixtures/payloads/codex/0.159.0"] = None
    del files["tests/fixtures/payloads/codex/0.160.0"]
    rows, code = audit(make_repo(tmp_path, files), make_cache(tmp_path), ["Codex CLI"])
    assert code == 1 and any(r.check == "pin sites" and "disagree" in r.detail for r in by(rows, ta.DRIFT))


def test_a_major_version_reference_is_held_to_the_runtime_pin(tmp_path):
    repo = make_repo(tmp_path, {".node-version": "24.19.0\n", "package.json": '{"engines": {"node": ">=24"}}',
                                ".devcontainer/Dockerfile": "RUN curl https://deb.nodesource.com/setup_20.x | bash -\n"})
    hits = [h for ref in ta.NODE_REFS for h in ta.scan_refs(ta.Repo(repo), ref)]
    assert {(h[0], h[2]) for h in hits} == {("package.json", "24"), (".devcontainer/Dockerfile", "20")}
    rows = []
    for ref in ta.NODE_REFS:
        ta.audit_refs(ta.Repo(repo), "Node.js", ref, "24.19.0", rows)
    assert [r.detail for r in rows] == [".devcontainer/Dockerfile:1 says 20"]


# ------------------------------------------------------------------ AutoMod / actions / packages

AUTOMOD = {
    "tools/automod-cli.sh": ('# PINNED. The toolkit was tested with AutoMod v1.11.2 = commit e1c34ea; that is it.\n'
                             'PIN_COMMIT="e1c34ea34cae4b9a1a547ea6a90c8a91412d689e"\n'
                             'CLI_PROJ="$AUTOMOD/src/SpookysAutomod.Cli"\n'
                             'PROPS="$AUTOMOD/Directory.Build.props"\n'),
    "docs/installing-tools.md": ("- AutoMod: `git clone --branch v1.11.2 https://github.com/SpookyPirate/spookys-automod-toolkit "
                                 "tools/automod` (v1.11.2 = commit `e1c34ea34cae4b9a1a547ea6a90c8a91412d689e`).\n"),
}


def test_automod_pinned_tag_commit_and_paths_are_ok(tmp_path):
    rows, code = audit(make_repo(tmp_path, AUTOMOD), make_cache(tmp_path), ["AutoMod"])
    assert code == 0, [r.as_dict() for r in rows]
    assert {"version", "install refs", "tag -> commit", "upstream path"} <= {r.check for r in rows}


def test_a_moved_tag_is_drift(tmp_path):
    url = "https://api.github.com/repos/SpookyPirate/spookys-automod-toolkit/git/ref/tags/v1.11.2"
    cache = make_cache(tmp_path, drop={url}, extra=[(url, 200, b'{"object":{"type":"commit","sha":"' + b"b" * 40 + b'"}}')])
    rows, code = audit(make_repo(tmp_path, AUTOMOD), cache, ["AutoMod"])
    assert code == 1 and any(r.check == "tag -> commit" and "tag moved" in r.detail for r in by(rows, ta.DRIFT))


def test_a_path_missing_at_the_pinned_commit_is_drift(tmp_path):
    url = ("https://api.github.com/repos/SpookyPirate/spookys-automod-toolkit/contents/Directory.Build.props"
           "?ref=e1c34ea34cae4b9a1a547ea6a90c8a91412d689e")
    cache = make_cache(tmp_path, drop={url}, extra=[(url, 404, b'{"message":"Not Found"}')])
    rows, code = audit(make_repo(tmp_path, AUTOMOD), cache, ["AutoMod"])
    assert code == 1 and any("Directory.Build.props" in r.detail for r in by(rows, ta.DRIFT))


def test_an_unpinned_automod_clone_is_drift(tmp_path):
    files = dict(AUTOMOD)
    files["README.md"] = "git clone https://github.com/SpookyPirate/spookys-automod-toolkit tools/automod\n"
    rows, code = audit(make_repo(tmp_path, files), make_cache(tmp_path), ["AutoMod"])
    assert code == 1 and any("AutoMod clone without --branch" in r.detail for r in by(rows, ta.DRIFT))


WF = "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@{ref}  # v7.0.1\n"


def test_an_action_sha_matching_its_tag_comment_is_ok(tmp_path):
    repo = make_repo(tmp_path, {".github/workflows/ci.yml": WF.format(ref="3d3c42e5aac5ba805825da76410c181273ba90b1")})
    rows, code = audit(repo, make_cache(tmp_path), ["action actions/checkout"])
    assert code == 0, [r.as_dict() for r in rows]


def test_an_action_sha_that_is_not_its_tag_is_drift(tmp_path):
    repo = make_repo(tmp_path, {".github/workflows/ci.yml": WF.format(ref="c" * 40)})
    rows, code = audit(repo, make_cache(tmp_path), ["action actions/checkout"])
    assert code == 1 and any(r.check == "tag -> commit" for r in by(rows, ta.DRIFT))


def test_an_action_by_tag_not_sha_is_drift(tmp_path):
    repo = make_repo(tmp_path, {".github/workflows/ci.yml": WF.format(ref="v7")})
    rows, code = audit(repo, make_cache(tmp_path), ["action actions/checkout"])
    assert code == 1 and any(r.check == "sha pin" for r in by(rows, ta.DRIFT))


def test_npm_exact_pin_and_lock_agree_is_ok(tmp_path):
    repo = make_repo(tmp_path, {"package.json": '{"dependencies": {"zod": "3.25.76"}}',
                                "package-lock.json": '{"packages": {"node_modules/zod": {"version": "3.25.76"}}}'})
    rows, code = audit(repo, make_cache(tmp_path), ["npm zod"])
    assert code == 0, [r.as_dict() for r in rows]


def test_an_npm_range_is_drift(tmp_path):
    repo = make_repo(tmp_path, {"package.json": '{"dependencies": {"zod": "^3.23.8"}}'})
    rows, code = audit(repo, make_cache(tmp_path), ["npm zod"])
    assert code == 1 and any(r.check == "exact pin" for r in by(rows, ta.DRIFT))


def test_an_npm_lock_that_disagrees_is_drift(tmp_path):
    repo = make_repo(tmp_path, {"package.json": '{"dependencies": {"zod": "3.25.76"}}',
                                "package-lock.json": '{"packages": {"node_modules/zod": {"version": "3.23.8"}}}'})
    rows, code = audit(repo, make_cache(tmp_path), ["npm zod"])
    assert code == 1 and any(r.check == "lock agrees" for r in by(rows, ta.DRIFT))


def test_a_python_lock_that_disagrees_is_drift(tmp_path):
    repo = make_repo(tmp_path, {"requirements.txt": "PyYAML==6.0.3\n", "requirements.lock": "pyyaml==6.0.1 \\\n    --hash=x\n"})
    rows, code = audit(repo, make_cache(tmp_path), ["pypi PyYAML"])
    assert code == 1 and any(r.check == "lock agrees" for r in by(rows, ta.DRIFT))
    ok = make_repo(tmp_path / "ok", {"requirements.txt": "PyYAML==6.0.3\n", "requirements.lock": "pyyaml==6.0.3 \\\n"})
    assert audit(ok, make_cache(tmp_path / "ok"), ["pypi PyYAML"])[1] == 0


# ------------------------------------------------------------------ keys, cache, CLI

def test_a_key_echoed_by_an_upstream_is_never_cached_or_printed(tmp_path):
    def echo(url, headers, secret):
        assert secret == {"apikey": FAKE_SECRET}          # header only, and only to Nexus
        assert FAKE_SECRET not in url and FAKE_SECRET not in json.dumps(headers)
        return ta.Response(200, json.dumps({"version": "1.0", "key": FAKE_SECRET}).encode(), {})
    f = ta.Fetcher(tmp_path / "c", transport=echo, env={"X4_NEXUS_KEY": FAKE_SECRET}, sleep=lambda s: None)
    assert ta.nexus_version(f, "skyrimspecialedition", 1) == "1.0"
    stored = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "c").iterdir())
    assert FAKE_SECRET not in stored and "<redacted>" in stored
    assert FAKE_SECRET not in ta.render([ta.Row("x", "y", None, FAKE_SECRET, ta.OK)], f.secrets)


def test_nexus_without_a_key_is_could_not_check_and_makes_no_call(tmp_path):
    calls = []
    f = ta.Fetcher(tmp_path / "c", transport=lambda *a: calls.append(a), env={})
    with pytest.raises(ta.CouldNotCheck, match="X4_NEXUS_KEY"):
        ta.nexus_version(f, "skyrimspecialedition", 58101)
    assert calls == []


def test_github_token_goes_only_to_the_api_host(tmp_path):
    seen = []

    def rec(url, headers, secret):
        seen.append((url.split("/")[2], dict(secret)))
        return ta.Response(200, b'{"tag_name":"v1"}', {})
    f = ta.Fetcher(tmp_path / "c", transport=rec, env={"GH_TOKEN": "tok"}, sleep=lambda s: None)
    f.get("https://api.github.com/repos/a/b/releases/latest")
    f.get("https://raw.githubusercontent.com/a/b/v1/README.md")
    assert seen == [("api.github.com", {"Authorization": "Bearer tok"}), ("raw.githubusercontent.com", {})]


def test_cli_exit_codes_and_table(tmp_path):
    repo = spriggit_repo(tmp_path, "Install: `dotnet tool install Spriggit.CLI --version 0.40.0`")
    code, out = cli(repo, make_cache(tmp_path), "--only", "Spriggit")
    assert code == 1 and "README.md:1 says 0.40.0" in out and "exit 1: DRIFT" in out, out
    code, out = cli(repo, make_cache(tmp_path), "--only", "Spriggit", "--json")
    data = json.loads(out)
    assert data["exit"] == 1 and any(r["status"] == "DRIFT" for r in data["rows"])


def test_the_registry_covers_the_manual_audit_floor():
    names = {s["name"] for s in ta.REGISTRY}
    assert set(ta.AUDIT_FLOOR) <= names
    assert len(names) == len(ta.REGISTRY)
    for spec in ta.REGISTRY:
        assert spec.get("upstream"), spec["name"]
        for check, _args in spec.get("checks", []):
            assert check in ta.CHECKS


def test_the_registry_reads_pins_from_the_repo_never_from_itself():
    """No pinned version is written into the registry: a version literal there would go stale silently."""
    src = SCRIPT.read_text(encoding="utf-8")
    registry = src[src.index("REGISTRY = ["):src.index("AUDIT_FLOOR = (")]
    assert not __import__("re").search(r"\"\d+\.\d+\.\d+\"|[0-9a-f]{40}", registry)


def test_the_real_tree_declares_readable_pin_sites():
    """On this tree every declared pin site resolves (a reworded doc fails here first, not at release time)."""
    repo = ta.Repo(REPO)
    unread = [(s["name"], site[1]) for s in ta.REGISTRY for site in s.get("pins", []) + s.get("tag_pins", [])
              if ta.read_pin_site(repo, site)[1] is None]
    assert unread == [], unread
