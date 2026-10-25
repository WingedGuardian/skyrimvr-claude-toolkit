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
# The Pinned downloads rows; the synthetic GitHub release JSON publishes these same (made-up) digests.
SHA_A, SHA_B = "a" * 64, "b" * 64
PYNIFLY_TABLE = ("| PyNifly | 29.1.0 | `io_scene_nifly.zip` | `%s` | "
                 "<https://github.com/BadDogSkyrim/PyNifly/releases/download/V29.1.0/io_scene_nifly.zip> |\n"
                 "| PyNifly Blender add-on | 27.2.0 | `io_scene_nifly.zip` | `%s` | "
                 "<https://github.com/BadDogSkyrim/PyNifly/releases/download/V27.2.0/io_scene_nifly.zip> |\n")


def pynifly_repo(tmp_path, path):
    return make_repo(tmp_path, {"docs/installing-tools.md": PYNIFLY_DOC.format(path=path) + PYNIFLY_TABLE % (SHA_A, SHA_B),
                                "agent/skills/skyrim-nif/SKILL.md": PYNIFLY_SKILL})


SPRIGGIT_BASE = {
    ".config/dotnet-tools.json": json.dumps({"version": 1, "tools": {"spriggit.cli": {"version": "0.41.0"}}}),
    ".devcontainer/Dockerfile": "ARG DOTNET_SDK_VERSION=10.0.401\n",
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


# Git for Windows tags a release v<git>.windows.<n> and names its installer Git-<git>-64-bit.exe when n is 1 and
# Git-<git>.<n>-64-bit.exe otherwise; the pin is the installer's version, so the tag must compare in that form.
@pytest.mark.parametrize("pinned,latest,status", [
    ("2.55.0.5", "v2.55.0.windows.5", ta.OK),
    ("2.48.1", "v2.48.1.windows.1", ta.OK),
    ("2.55.0.5", "v2.55.0.windows.6", ta.NEWER),
    ("2.55.0.5", "v2.56.0.windows.2", ta.NEWER),
    ("2.55.0.5", "v2.55.0.windows.4", ta.DRIFT),
])
def test_a_git_for_windows_tag_compares_as_its_installer_version(pinned, latest, status):
    assert ta.version_status(pinned, latest)[0] == status


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


@pytest.mark.parametrize("wrong_release", [False, True])
def test_addon_download_url_must_belong_to_the_release_whose_checksum_is_published(tmp_path, wrong_release):
    root = pynifly_repo(tmp_path, "io_scene_nifly/NiflyDLL.dll")
    path = root / "docs/installing-tools.md"
    if wrong_release:
        path.write_text(path.read_text(encoding="utf-8").replace(
            "/download/V27.2.0/io_scene_nifly.zip", "/download/V27.1.0/io_scene_nifly.zip"), encoding="utf-8")
    rows, code = audit(root, make_cache(tmp_path), ["PyNifly"])
    hashes = [r for r in rows if r.check == "pinned hash"]
    assert [r.status for r in hashes] == [ta.OK, ta.DRIFT if wrong_release else ta.OK]
    assert code == (1 if wrong_release else 0), [r.as_dict() for r in rows]


@pytest.mark.parametrize("published_url", [None, "https://github.com/example/tool/releases/download/v2/selected.zip"])
def test_release_asset_download_url_is_bound_including_repeated_filenames(published_url):
    item = {"label": "selected", "published_format": "github-release"}
    entry = {"name": "selected.zip", "digest": "sha256:" + SHA_A}
    if published_url is not None:
        entry["browser_download_url"] = published_url
    response = ta.Response(200, json.dumps({"assets": [entry]}).encode())
    api = "https://api.github.com/repos/example/tool/releases/tags/v2"
    for version in ("v1", "v2"):
        fields = {"url": "https://github.com/example/tool/releases/download/" + version + "/selected.zip"}
        assert ta._published_checksum(response, api, item, fields, 64) == (SHA_A if version == "v2" else None)


def test_a_historical_path_mention_is_not_an_instruction(tmp_path):
    text = (PYNIFLY_DOC.format(path="io_scene_nifly/NiflyDLL.dll")
            + "Releases before 29.x need an explicit load first:\n"
            + "    pynifly.NifFile.Load('tools/pynifly/io_scene_nifly/pyn/NiflyDLL.dll')\n")
    repo = make_repo(tmp_path, {"docs/installing-tools.md": text + PYNIFLY_TABLE % (SHA_A, SHA_B),
                                "agent/skills/skyrim-nif/SKILL.md": PYNIFLY_SKILL})
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


def test_a_truncated_body_is_could_not_check_not_a_crash(tmp_path):
    """v4.0.0 review L4b L4: http.client.IncompleteRead is an HTTPException, not an OSError; it killed the whole run
    with the DRIFT exit code."""
    import http.client

    def truncated(url, headers, secret):
        raise http.client.IncompleteRead(b"partial", 100)

    repo = make_repo(tmp_path, CODEX)
    f = ta.Fetcher(tmp_path / "empty-cache", transport=truncated, env={}, sleep=lambda s: None)
    rows = ta.run(repo, f, ["Codex CLI"])
    assert [r.status for r in rows] == [ta.UNCHECKED] and "unreachable" in rows[0].detail
    assert ta.exit_code(rows) == 2 and f.calls == 2


def test_offline_without_a_cached_answer_is_could_not_check(tmp_path):
    repo = make_repo(tmp_path, CODEX)
    rows, code = audit(repo, tmp_path / "empty-cache", ["Codex CLI"])
    assert code == 2 and rows[0].status == ta.UNCHECKED and "offline and not cached" in rows[0].detail


def test_the_whole_registry_offline_with_no_cache_is_never_ok(tmp_path):
    """Every integration, nothing reachable: not one row may read OK/NEWER/UNPINNED, and the exit is 2.
    The only rows that may be decided are network-free: install refs, the delivery-path scan and NOT DELIVERED."""
    repo = make_repo(tmp_path, dict(CODEX, **SPRIGGIT_BASE))
    code, out = cli(repo, tmp_path / "empty-cache")
    assert code == 2, out
    body = out.split("\n\n")[0].splitlines()[1:]
    assert body and all("COULD NOT CHECK" in line or " OK " in line or "NOT DELIVERED" in line for line in body), out
    assert all("install refs" in line or line.startswith("delivery paths") for line in body if " OK " in line), out
    assert "UNPINNED" not in out.split("\n\n")[0], out


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
    repo = spriggit_repo(tmp_path, "x", {".devcontainer/Dockerfile": "ARG DOTNET_SDK_VERSION=9.0.100\n",
                                         ".github/workflows/ci.yml": files[".github/workflows/ci.yml"].replace("10.0", "9.0")})
    rows, code = audit(repo, make_cache(tmp_path), ["Spriggit"])
    assert code == 1 and any("ships no tools/net9.0/" in r.detail for r in by(rows, ta.DRIFT))


def test_pin_sites_that_disagree_are_drift(tmp_path):
    files = dict(CODEX)
    files["tests/fixtures/payloads/codex/0.159.0"] = None
    del files["tests/fixtures/payloads/codex/0.160.0"]
    rows, code = audit(make_repo(tmp_path, files), make_cache(tmp_path), ["Codex CLI"])
    assert code == 1 and any(r.check == "pin sites" and "disagree" in r.detail for r in by(rows, ta.DRIFT))


def test_an_instruction_under_a_lead_in_with_an_earlier_history_sentence_is_compared(tmp_path):
    """v4.0.0 review L4b M1: README:127/128. A lead-in ending ':' whose EARLIER sentence says 'was tested with' made
    the winget line under it "historical", so the primary Node install was never compared with the pin."""
    readme = ("Install Node.js **24.19.0**, the version this toolkit was tested with. For you to run, in Windows "
              "Terminal:\n"
              "    winget install --id OpenJS.NodeJS.LTS --exact --version 22.11.0 --no-upgrade\n")
    repo = make_repo(tmp_path, {".node-version": "24.19.0\n", "README.md": readme})
    hits = [h for ref in ta.NODE_REFS for h in ta.scan_refs(ta.Repo(repo), ref)]
    assert ("README.md", 2, "22.11.0") in {h[:3] for h in hits}, hits


def test_a_lead_in_whose_last_clause_is_history_still_marks_the_line_under_it(tmp_path):
    """The twin: 'Releases before 29.x need ...:' is one clause, and it IS history."""
    readme = "Releases before 24.x needed this:\n    winget install --id OpenJS.NodeJS.LTS --exact --version 22.11.0\n"
    repo = make_repo(tmp_path, {".node-version": "24.19.0\n", "README.md": readme})
    hits = [h for ref in ta.NODE_REFS for h in ta.scan_refs(ta.Repo(repo), ref)]
    assert [h[2] for h in hits if h[0] == "README.md" and h[1] == 2] == ["history"], hits


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


DEVKIT_ZIP = b"fixture null driver zip"
DEVKIT_SHA = __import__("hashlib").sha256(DEVKIT_ZIP).hexdigest()
DEVKIT_COMMIT = "32ec30294a871683254b944b4129cbdec2591e6a"
DEVKIT_REPO = "DubiousDuo/VR-Emulator-Driver---SkyrimVR-Devkit"
DEVKIT_RAW = "https://raw.githubusercontent.com/%s/%s/null%%20driver.zip" % (DEVKIT_REPO, DEVKIT_COMMIT)
DEVKIT = {
    "tools/vr-emu-driver/fetch.py": ('DRIVER_TAG = "Release_v2.0.1"\n'
                                     'DRIVER_COMMIT = "%s"\n'
                                     'DRIVER_ZIP_URL = ("https://raw.githubusercontent.com/DubiousDuo/'
                                     'VR-Emulator-Driver---SkyrimVR-Devkit/"\n'
                                     '                  "%s/null%%20driver.zip")\n'
                                     'DRIVER_ZIP_SHA256 = "%s"\n' % (DEVKIT_COMMIT, DEVKIT_COMMIT, DEVKIT_SHA)),
    "tools/vr-emu-driver/README.md": "| Release | `Release_v2.0.1` = commit `%s` (2026-10-14) |\n" % DEVKIT_COMMIT,
}


def devkit_cache(tmp_path, tag_sha=DEVKIT_COMMIT, body=DEVKIT_ZIP, latest="Release_v2.0.1"):
    api = "https://api.github.com/repos/" + DEVKIT_REPO
    return make_cache(tmp_path, extra=[
        (api + "/releases/latest", 200, json.dumps({"tag_name": latest}).encode()),
        (api + "/git/ref/tags/Release_v2.0.1", 200, json.dumps({"object": {"type": "commit", "sha": tag_sha}}).encode()),
        (DEVKIT_RAW, 200, body)])


def test_the_devkit_driver_pin_tag_and_package_hash_are_ok(tmp_path):
    rows, code = audit(make_repo(tmp_path, DEVKIT), devkit_cache(tmp_path), ["SkyrimVR Devkit null driver"])
    assert code == 0, [r.as_dict() for r in rows]
    assert {"version", "tag -> commit", "pinned hash"} <= {r.check for r in rows}


def test_a_devkit_package_that_changed_upstream_is_drift(tmp_path):
    rows, code = audit(make_repo(tmp_path, DEVKIT), devkit_cache(tmp_path, body=b"a different zip"),
                       ["SkyrimVR Devkit null driver"])
    assert code == 1 and any(r.check == "pinned hash" for r in by(rows, ta.DRIFT))


def test_a_moved_devkit_release_tag_is_drift(tmp_path):
    rows, code = audit(make_repo(tmp_path, DEVKIT), devkit_cache(tmp_path, tag_sha="c" * 40),
                       ["SkyrimVR Devkit null driver"])
    assert code == 1 and any(r.check == "tag -> commit" for r in by(rows, ta.DRIFT))


def test_devkit_pin_sites_that_disagree_are_drift(tmp_path):
    files = dict(DEVKIT)
    files["tools/vr-emu-driver/README.md"] = "| Release | `Release_v2.0.1` = commit `%s` |\n" % ("b" * 40)
    rows, code = audit(make_repo(tmp_path, files), devkit_cache(tmp_path), ["SkyrimVR Devkit null driver"])
    assert code == 1 and any(r.check == "pin sites" for r in by(rows, ta.DRIFT))


def test_a_newer_devkit_release_is_reported_not_failed(tmp_path):
    rows, code = audit(make_repo(tmp_path, DEVKIT), devkit_cache(tmp_path, latest="Release_v2.1.0"),
                       ["SkyrimVR Devkit null driver"])
    assert code == 0 and any(r.check == "version" for r in by(rows, ta.NEWER))


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
        commit_sources = [args for check, args in spec.get("checks", []) if check == "commit_package"]
        if not spec.get("upstream"):
            assert len(commit_sources) == 1, spec["name"]
            source = commit_sources[0]
            assert __import__("re").fullmatch(r"[\w.-]+/[\w.-]+", source.get("repo", "")), spec["name"]
            assert source.get("package") and len(source.get("version_sites", [])) >= 2, spec["name"]
            assert spec.get("pins"), spec["name"]
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
    unread = []
    for spec in ta.REGISTRY:
        sites = spec.get("pins", []) + spec.get("tag_pins", [])
        for check, args in spec.get("checks", []):
            if check == "commit_package":
                sites += args["version_sites"]
        unread.extend((spec["name"], site[1]) for site in sites if ta.read_pin_site(repo, site)[1] is None)
    assert unread == [], unread


# ------------------------------------------------------------------ Plan 8 T6d: every delivery path exact + verified
# User ruling 2026-10-05: every user gets EXACTLY the versions we tested. delivery_findings() scans every shipped
# surface for an install instruction that is not one exact, verifiable version. One red/green pair per rule clause.

def findings(tmp_path, files, exempt=()):
    return ta.delivery_findings(ta.Repo(make_repo(tmp_path, files)), exempt=list(exempt))


def rules(tmp_path, files, exempt=()):
    return sorted({(f[0], f[2]) for f in findings(tmp_path, files, exempt)[0]})


def test_the_shipped_tree_has_no_unpinned_delivery_path():
    """The gate itself: on this tree every install instruction is exact and verifiable, or exempt with a reason."""
    found, exempted, _stale = ta.delivery_findings(ta.Repo(REPO))
    assert found == [], "\n".join("%s:%d [%s] %s" % f[:4] for f in found)
    assert all(e[5] for e in exempted)


def test_every_delivery_exemption_still_matches_the_real_tree():
    """An exemption whose line was fixed (or moved) must be removed, or it silently excuses the next such line."""
    stale = ta.delivery_findings(ta.Repo(REPO))[2]
    assert stale == [], stale


@pytest.mark.parametrize("line,red", [
    ("Run `pip install lz4` first.", True),
    ("Run `python -m pip install pyffi==2.2.3` first.", True),                                    # pinned, unverified
    ("`python -m pip install --require-hashes pyffi==2.2.3`", True),                            # hashes but no lock
    ("`python -m pip install -r tools/requirements-tools.lock`", True),                         # lock, hashes optional
    ("`python -m pip install --only-binary=:all: --require-hashes -r tools/requirements-tools.lock`", False),
    ("`python -m pip install --dry-run --report r.json -r requirements.txt`", False),          # resolves, installs nothing
])
def test_a_pip_install_must_be_hash_locked(tmp_path, line, red):
    assert (("README.md", "pip") in rules(tmp_path, {"README.md": line + "\n"})) is red


@pytest.mark.parametrize("line,red", [
    ("`npm install`", True),                                                  # bare: resolves ranges, use npm ci
    ("`npm install -g @devcontainers/cli`", True),
    ("`npm install github:WingedGuardian/xeditlib`", True),                   # default-branch HEAD
    ("`npm install github:WingedGuardian/xeditlib#v1.0.1`", True),            # a tag can move; a commit cannot
    ("`npm install -g @devcontainers/cli@0.89.0`", False),
    ("`npm install --no-save github:WingedGuardian/xeditlib#" + "1" * 40 + " koffi@2.15.1`", False),
    ("`npm ci --ignore-scripts`", False),
])
def test_an_npm_install_must_name_an_exact_version_or_commit(tmp_path, line, red):
    assert (("README.md", "npm") in rules(tmp_path, {"README.md": line + "\n"})) is red


@pytest.mark.parametrize("line,red", [
    ("`dotnet tool install -g Spriggit.CLI`", True),
    ("`dotnet tool update -g Spriggit.CLI`", True),
    ("`dotnet tool install -g Spriggit.CLI --version 0.41.0`", False),
    ("`dotnet tool install -g Spriggit.CLI`, then `spriggit --version 0.41.0`", True),   # another command's flag
    ("`dotnet tool restore`", False),
])
def test_a_dotnet_tool_install_must_name_a_version(tmp_path, line, red):
    assert (("README.md", "dotnet-tool") in rules(tmp_path, {"README.md": line + "\n"})) is red


AUTOMOD_URL = "https://github.com/SpookyPirate/spookys-automod-toolkit"


@pytest.mark.parametrize("line,proof,red", [
    ("`git clone %s tools/automod`" % AUTOMOD_URL, True, True),                        # no tag: default HEAD
    ("`git clone --branch v1.11.2 %s tools/automod`" % AUTOMOD_URL, False, True),      # tag, nothing checks the commit
    ("`git clone --branch v1.0 https://github.com/someone/else x`", True, True),       # a repo nobody verifies
    ("`git clone --branch v1.11.2 %s tools/automod`" % AUTOMOD_URL, True, False),
    ("A `git clone` of the repo does NOT contain the DLL.", True, False),              # prose, not a command
])
def test_a_git_clone_must_name_a_tag_and_be_commit_verified(tmp_path, line, proof, red):
    files = {"README.md": line + "\n"}
    if proof:
        files["tools/automod-cli.sh"] = 'PIN_COMMIT="%s"\n' % ("e" * 40)
    assert (("README.md", "git-clone") in rules(tmp_path, files)) is red


@pytest.mark.parametrize("line,red", [
    ("`winget install Python.Python.3.12`", True),
    ("`winget install --id Python.Python.3.12 --exact`", True),
    ("`winget install --id Python.Python.3.12 --version 3.12.10`", True),
    ("`winget install --id Python.Python.3.12 --exact --version 3.12.10`", False),
    ("Spriggit (`dotnet tool install X --version 0.41.0`) needs `winget install --id Y --exact`", True),

])
def test_a_winget_install_must_be_exact_with_a_version(tmp_path, line, red):
    assert (("README.md", "winget") in rules(tmp_path, {"README.md": line + "\n"})) is red


def test_a_download_piped_into_a_shell_is_never_pinned(tmp_path):
    assert ("README.md", "pipe-to-shell") in rules(tmp_path, {"README.md": "RUN curl -fsSL https://x.io/i.sh | bash\n"})
    assert rules(tmp_path, {"README.md": "Read it with `curl -s https://x.io/a.json | jq .`\n"}) == []


@pytest.mark.parametrize("line,red", [
    ("FROM mcr.microsoft.com/devcontainers/python:3.0.6-3.11-bookworm", True),
    ("FROM mcr.microsoft.com/devcontainers/python:3.0.6-3.11-bookworm@sha256:" + "c" * 64, False),
])
def test_a_base_image_must_be_pinned_by_digest(tmp_path, line, red):
    assert ((".devcontainer/Dockerfile", "docker-from") in rules(tmp_path, {".devcontainer/Dockerfile": line + "\n"})) is red


@pytest.mark.parametrize("line,red", [
    ("RUN apt-get update && apt-get install -y --no-install-recommends openjdk-17-jdk-headless", True),
    ("RUN apt-get install -y --no-install-recommends openjdk-17-jdk-headless=17.0.16+8-1~deb12u1", False),
    ("choco install jq -y", True),
])
def test_a_system_package_must_name_an_exact_version(tmp_path, line, red):
    assert ((".devcontainer/Dockerfile", "system-package") in rules(tmp_path, {".devcontainer/Dockerfile": line + "\n"})) is red


def test_a_container_download_must_be_checksummed_in_the_same_step(tmp_path):
    bad = "RUN curl -fsSL -o /tmp/n.tar.gz https://nodejs.org/dist/v1/n.tar.gz \\\n    && tar -xzf /tmp/n.tar.gz\n"
    good = ("RUN curl -fsSL -o /tmp/n.tar.gz https://nodejs.org/dist/v1/n.tar.gz \\\n"
            "    && echo \"$sum  /tmp/n.tar.gz\" | sha256sum -c - \\\n    && tar -xzf /tmp/n.tar.gz\n")
    assert (".devcontainer/Dockerfile", "download-checksum") in rules(tmp_path, {".devcontainer/Dockerfile": bad})
    assert rules(tmp_path, {".devcontainer/Dockerfile": good}) == []   # the continuation lines are one command


@pytest.mark.parametrize("line,red", [
    ("Download `io_scene_nifly.zip` from the latest release.", True),
    ("Acquire: download a release from github.com/Orvid/Champollion/releases.", True),
    ("Click the big green LTS button.", True),
    ("The newest release on Nexus is 1.24.0.", False),                      # a statement, not an install
])
def test_an_install_that_follows_the_newest_release_is_unpinned(tmp_path, line, red):
    assert (("docs/x.md", "follows-latest") in rules(tmp_path, {"docs/x.md": line + "\n"})) is red


@pytest.mark.parametrize("line,red", [
    ("Download v1.3.2 from https://github.com/Orvid/Champollion/releases/download/v1.3.2/x.zip", True),
    ("Download v1.3.2 from https://github.com/Orvid/Champollion/releases/download/v1.3.2/x.zip, SHA-256 `"
     + "d" * 64 + "`", False),
    ("Download ReSaver 6.0.643 from Nexus mod 5031; check it against Pinned downloads.", False),
    ("Download ReSaver from Nexus mod 5031.", True),
])
def test_a_download_needs_a_hash_or_the_pinned_downloads_table(tmp_path, line, red):
    assert (("docs/x.md", "download-unverified") in rules(tmp_path, {"docs/x.md": line + "\n"})) is red


@pytest.mark.parametrize("line", [
    "Before you start, run `pip install lz4`.",                     # review I1: these were all read as history
    "Before installing, run `npm install -g foo`.",
    "Instead of the old tool, run `winget install Git.Git`.",
    "Prior to setup: `pip install lz4`",
    "Older toolkits said `pip install lz4`.",                       # no wording excuses a line any more
])
def test_no_wording_silences_an_install_line(tmp_path, line):
    assert findings(tmp_path, {"README.md": line + "\n"})[0], line


def test_only_history_files_are_skipped(tmp_path):
    line = "Older toolkits said `pip install lz4`.\n"
    assert findings(tmp_path, {"CHANGELOG.md": line, "docs/superpowers/plans/x.md": line})[0] == []
    assert [f[0] for f in findings(tmp_path / "b", {"docs/x.md": line})[0]] == ["docs/x.md"]


def test_private_backups_and_optional_backend_state_are_not_delivery_surfaces(tmp_path):
    line = "Run `pip install lz4`.\n"
    files = {".claude/backups/old/README.md": line, ".skyguard/housecarl/upstream/README.md": line,
             "KNOWLEDGEBASE.local.md": line, "tools/actual.md": line}
    assert [f[0] for f in findings(tmp_path, files)[0]] == ["tools/actual.md"]
    assert (tmp_path / "repo/.claude/backups/old/README.md").read_text() == line


def test_an_exempt_line_is_reported_with_its_reason_and_an_unused_exemption_is_stale(tmp_path):
    files = {"README.md": "`npm install -g @anthropic-ai/claude-code`\n"}
    ex = [("README.md", "npm", r"@anthropic-ai/claude-code", "the agent updates itself"),
          ("README.md", "pip", r"pip install nothing-here", "unused")]
    found, exempted, stale = findings(tmp_path, files, ex)
    assert found == [] and [e[5] for e in exempted] == ["the agent updates itself"]
    assert stale == [ex[1]]
    assert ("README.md", "npm") in rules(tmp_path, files, ex[1:])   # the same line without its exemption is red
    wrong_rule = [("README.md", "pip", r"@anthropic-ai/claude-code", "an exemption for another rule")]
    assert ("README.md", "npm") in rules(tmp_path, files, wrong_rule)   # it excuses only its own rule


def test_an_unpinned_delivery_path_fails_the_audit(tmp_path):
    repo = make_repo(tmp_path, {"README.md": "`pip install lz4`\n"})
    rows, code = audit(repo, tmp_path / "c", ["delivery paths"])
    assert code == 1 and any(r.tool == "delivery paths" and "README.md:1" in r.detail for r in by(rows, ta.DRIFT))
    clean = make_repo(tmp_path / "clean", {"README.md": "`npm ci`\n"})
    rows, code = audit(clean, tmp_path / "c", ["delivery paths"])
    assert code == 0 and [r.status for r in rows] == [ta.OK]


# NOT DELIVERED: a registry entry the toolkit never installs says why, and is never UNPINNED.

def test_an_integration_the_toolkit_never_delivers_is_reported_with_its_reason(tmp_path, monkeypatch):
    spec = {"name": "Some Manager", "upstream": ("npm", "@openai/codex"), "not_delivered": "the user's own tool"}
    monkeypatch.setattr(ta, "REGISTRY", [spec])
    rows, code = audit(make_repo(tmp_path, {}), make_cache(tmp_path), ["Some Manager"])
    assert [(r.status, r.detail, r.upstream) for r in rows] == [(ta.OUTSIDE, "the user's own tool", "0.161.0")]
    assert code == 0
    del spec["not_delivered"]
    rows, _code = audit(make_repo(tmp_path, {}), make_cache(tmp_path), ["Some Manager"])
    assert [r.status for r in rows] == [ta.UNPINNED]   # the control: without the reason it is UNPINNED


def test_every_registry_entry_reads_a_pin_or_says_why_it_is_not_delivered():
    """UNPINNED cannot come back silently: a new entry needs a pin site or a not_delivered reason."""
    bare = [s["name"] for s in ta.REGISTRY if not s.get("pins") and not s.get("not_delivered")]
    assert bare == [], bare


# Pinned hashes are re-checked against upstream: a published checksum, or the file itself.

def pynifly_table_repo(tmp_path, sha_a):
    return make_repo(tmp_path, {"docs/installing-tools.md": PYNIFLY_DOC.format(path="io_scene_nifly/NiflyDLL.dll")
                                + PYNIFLY_TABLE % (sha_a, SHA_B), "agent/skills/skyrim-nif/SKILL.md": PYNIFLY_SKILL})


def test_a_pinned_hash_upstream_publishes_is_ok_and_a_different_one_is_drift(tmp_path):
    rows, code = audit(pynifly_table_repo(tmp_path, SHA_A), make_cache(tmp_path), ["PyNifly"])
    assert code == 0 and len([r for r in rows if r.check == "pinned hash" and r.status == ta.OK]) == 2
    rows, code = audit(pynifly_table_repo(tmp_path / "bad", "f" * 64), make_cache(tmp_path / "bad"), ["PyNifly"])
    bad = [r for r in by(rows, ta.DRIFT) if r.check == "pinned hash"]
    assert code == 1 and [r.upstream for r in bad] == ["PyNifly"] and "NOT what upstream has" in bad[0].detail


def test_a_pinned_hash_is_checked_by_downloading_when_upstream_publishes_none(tmp_path):
    url = "https://github.com/Orvid/Champollion/releases/download/v1.3.2/Champollion.v1.3.2.zip"
    body = b"champollion bytes"
    row = "| Champollion | v1.3.2 | `Champollion.v1.3.2.zip` | `%s` | <%s> |\n"
    for sha, want in ((__import__("hashlib").sha256(body).hexdigest(), ta.OK), ("0" * 64, ta.DRIFT)):
        repo = make_repo(tmp_path / want.replace(" ", "_"), {"docs/installing-tools.md": row % (sha, url)})
        cache = make_cache(tmp_path / want.replace(" ", "_"), extra=[(url, 200, body)])
        rows, _code = audit(repo, cache, ["Champollion"])
        assert [r.status for r in rows if r.check == "pinned hash"] == [want], [r.as_dict() for r in rows]


@pytest.mark.parametrize("family,index", [
    ("Node.js", 0), ("Node.js", 1), (".NET SDK", 0), (".NET SDK", 1),
    ("Microsoft OpenJDK", 0), ("Microsoft OpenJDK", 1),
    ("Blender (4.2 LTS)", 0), ("PyNifly", 0), ("PyNifly", 1), ("House Carl", 0), ("7-Zip", 0), ("7-Zip", 1),
])
@pytest.mark.parametrize("swapped", [False, True])
def test_published_hash_belongs_to_the_exact_artifact(tmp_path, family, index, swapped):
    spec = next(row for row in ta.REGISTRY if row["name"] == family)
    items = [item for kind, args in spec["checks"] if kind == "hashes" for item in args["items"]]
    item = items[index]
    repo = ta.Repo(REPO)
    import re
    match = re.search(item["pattern"], repo.read(item["file"]))
    fields = match.groupdict()
    want, version = fields["sha"], fields["v"]
    other = ("e" if want[0] != "e" else "f") * len(want)
    correct, alternate = (other, want) if swapped else (want, other)
    if family == "Node.js":
        arch = "x64" if index == 0 else "arm64"
        artifact = f"node-v{version}-linux-{arch}.tar.gz"
        body = f"{correct}  {artifact}\n{alternate}  unrelated-platform.tar.gz\n"
    elif family == ".NET SDK":
        arch = "x64" if index == 0 else "arm64"
        artifact = f"dotnet-sdk-{version}-linux-{arch}.tar.gz"
        body = json.dumps({"releases": [{"sdk": {"version": version, "files": [
            {"url": "https://builds.dotnet.microsoft.com/dotnet/Sdk/" + version + "/" + artifact, "hash": correct},
            {"url": "https://example.invalid/unrelated-platform.tar.gz", "hash": alternate}]}}]})
    elif family == "Microsoft OpenJDK":
        arch = "x64" if index == 0 else "aarch64"
        artifact = f"microsoft-jdk-{version}-linux-{arch}.tar.gz"
        body = f"{correct}  {artifact}\n{alternate}  unrelated-platform.tar.gz\n"
    else:
        artifact = fields["url"].rsplit("/", 1)[-1]
        if family == "Blender (4.2 LTS)":
            body = f"{correct}  {artifact}\n{alternate}  unrelated-platform.tar.gz\n"
        else:
            body = json.dumps({"assets": [{"name": artifact, "digest": "sha256:" + correct},
                                           {"name": "unrelated-platform.zip", "digest": "sha256:" + alternate}]})
    url = item["published"].format(**fields)
    cache = make_cache(tmp_path, extra=[(url, 200, body.encode())])
    rows = []
    ta.check_hashes(ta.Fetcher(cache, offline=True, env={}), repo, family, version, None,
                    {"items": [item]}, rows, {}, [])
    assert [row.status for row in rows] == [ta.DRIFT if swapped else ta.OK], [row.as_dict() for row in rows]


@pytest.mark.parametrize("fmt,body", [
    ("github-release", '{"assets":[{"name":"selected.zip"}]}'),
    ("github-release", '{"assets":{}}'),
    ("github-release", '{"assets":[false]}'),
    ("github-release", '{"assets":[{"name":"selected.zip","digest":"sha256:no"}]}'),
    ("github-release", json.dumps({"assets": [
        {"name": "selected.zip", "digest": "sha256:" + SHA_A},
        {"name": "selected.zip", "digest": "sha256:" + SHA_B}]})),
    ("dotnet", '{"releases":[{"sdk":{"files":[{"url":"https://example.invalid/selected.zip"}]}}]}'),
    ("dotnet", '{"releases":false}'),
    ("checksums", SHA_A),  # A bare digest cannot validate an artifact in a multi-file manifest.
    ("checksums", SHA_A + "  selected.zip\n" + SHA_B + "  selected.zip\n"),
    ("unknown", SHA_A + "  selected.zip\n"),
])
def test_ambiguous_or_unreadable_published_hash_is_unchecked(fmt, body):
    item = {"label": "selected", "artifact": "selected.zip", "published_format": fmt}
    with pytest.raises(ta.CouldNotCheck):
        ta._published_checksum(ta.Response(200, body.encode()), "https://example.invalid/SUMS", item, {}, 64)


@pytest.mark.parametrize("fmt,body", [
    ("github-release", '{"assets":[]}'),
    ("dotnet", '{"releases":[]}'),
    ("checksums", SHA_A + "  other.zip\n"),
])
def test_an_absent_artifact_does_not_borrow_another_artifacts_hash(fmt, body):
    item = {"label": "selected", "artifact": "selected.zip", "published_format": fmt}
    assert ta._published_checksum(ta.Response(200, body.encode()), "https://example.invalid/SUMS", item, {}, 64) is None


@pytest.mark.parametrize("filename", ["selected.zip", "*selected.zip", "./selected.zip"])
def test_checksum_manifest_supported_filename_forms(filename):
    item = {"label": "selected", "artifact": "selected.zip", "published_format": "checksums"}
    body = (SHA_A.upper() + "  " + filename + "\n").encode()
    assert ta._published_checksum(ta.Response(200, body), "https://example.invalid/SUMS", item, {}, 64) == SHA_A


def test_a_bare_sidecar_hash_is_bound_to_its_artifact_specific_url():
    item = {"label": "selected", "artifact": "selected.zip", "published_format": "checksums", "single_file": True}
    response = ta.Response(200, (SHA_A + "\n").encode())
    assert ta._published_checksum(response, "https://example.invalid/selected.zip.sha256sum.txt", item, {}, 64) == SHA_A
    with pytest.raises(ta.CouldNotCheck):
        ta._published_checksum(response, "https://example.invalid/other.zip.sha256sum.txt", item, {}, 64)


def test_the_newest_main_nexus_file_is_the_upstream_version(tmp_path):
    url = "https://api.nexusmods.com/v1/games/g/mods/1/files.json"
    files = {"files": [{"category_name": "MAIN", "version": "6.0.636"}, {"category_name": "MAIN", "version": "6.0.643"},
                       {"category_name": "OLD_VERSION", "version": "9.9.9"}]}
    f = ta.Fetcher(make_cache(tmp_path, extra=[(url, 200, json.dumps(files).encode())]), offline=True, env={})
    assert ta.nexus_file_latest(f, "g", 1) == "6.0.643"


# Fix round 1 (review I1): the installer families the first version missed, each red and green.

@pytest.mark.parametrize("line,rule,red", [
    ("`npx some-tool --help`", "package-runner", True),
    ("`npx some-tool@1.2.3 --help`", "package-runner", False),
    ("`npm exec create-thing`", "package-runner", True),
    ("`uvx ruff check`", "package-runner", True),
    ("`uvx ruff==0.16.9 check`", "package-runner", False),
    ("`pipx install black`", "tool-installer", True),
    ("`pipx install black==24.1.0`", "tool-installer", False),
    ("`uv tool install ruff`", "tool-installer", True),
    ("`uv tool install ruff==0.16.9`", "tool-installer", False),
    ("`scoop install jq`", "tool-installer", True),
    ("`scoop install jq@1.7.1`", "tool-installer", False),
    ("`cargo install xwin`", "tool-installer", True),
    ("`cargo install xwin --version 0.6.5`", "tool-installer", True),          # no --locked: deps float
    ("`cargo install xwin --version 0.6.5 --locked`", "tool-installer", False),
    ("`go install example.com/x@latest`", "tool-installer", True),
    ("`go install example.com/x@v1.2.3`", "tool-installer", False),
    ("`uv pip install lz4`", "pip", True),
    ("`uv pip install --require-hashes -r tools/requirements-tools.lock`", "pip", False),
    ("`gh release download -R a/b`", "gh-release", True),                     # no tag: newest
    ("`gh release download v1 -R a/b`", "gh-release", True),                  # tag, nothing checks it
    ("`gh release download v1 -R a/b && sha256sum -c SUMS`", "gh-release", False),
    ("`irm https://get.x.io/install.ps1 | iex`", "pipe-to-shell", True),
    ("`iwr -useb https://get.x.io | iex`", "pipe-to-shell", True),
    ("`iex (iwr https://get.x.io).Content`", "pipe-to-shell", True),
    ("`curl -fsSL https://get.x.io | sh`", "pipe-to-shell", True),
    ("`wget -qO- https://get.x.io | sh`", "pipe-to-shell", True),
    ("`choco install jq`", "system-package", True),
    ("`choco install jq --version 1.7.1`", "system-package", False),
])
def test_every_installer_family_needs_an_exact_version(tmp_path, line, rule, red):
    assert (("README.md", rule) in rules(tmp_path, {"README.md": line + "\n"})) is red


@pytest.mark.parametrize("line,red", [
    ("`pip install lz4; pip install --only-binary=:all: --require-hashes -r t.lock`", True),   # review M1
    ("`pip install --dry-run x` then `pip install lz4`", True),
    ("`pip install --dry-run --report r.json -r requirements.txt`", False),
])
def test_pip_is_judged_per_command(tmp_path, line, red):
    assert (("README.md", "pip") in rules(tmp_path, {"README.md": line + "\n"})) is red


@pytest.mark.parametrize("name", ["tools/x.py", "tools/x.sh", "tools/x.ps1", "tools/x.cmd", "agent/x.json",
                                  "agent/x.yml", "docs/x.md", "x.txt"])
def test_an_unverified_download_is_found_in_every_shipped_text_type(tmp_path, name):
    bad = '    "Download Python from https://www.python.org/downloads/ and install it."\n'
    assert (name, "download-unverified") in rules(tmp_path, {name: bad})


def test_a_download_hash_may_wrap_onto_the_next_two_lines(tmp_path):
    text = ('"Download python-3.12.10-amd64.exe from https://www.python.org/ftp/python/3.12.10/ -- "\n'
            '"check its SHA-256 equals "\n"' + "e" * 64 + '"\n')
    assert rules(tmp_path, {"tools/x.py": text}) == []
    far = text.replace('"check its SHA-256 equals "\n', '"one"\n"two"\n"three"\n')
    assert ("tools/x.py", "download-unverified") in rules(tmp_path / "far", {"tools/x.py": far})


def test_the_pre_fix_manual_steps_are_seen(tmp_path):
    """Review I2: the toolkit-setup skill's manual steps (tools/inventory-runtimes.py) sent users to whatever
    python.org / nodejs.org / git-scm served; the scan did not read .py strings."""
    old = ('    "python": "Download Python 3.8 or newer from https://www.python.org/downloads/ and install it, "\n'
           '    "git": "Download Git for Windows from https://git-scm.com/download/win and install it. "\n'
           '    "node": "Download the LTS release from https://nodejs.org/ and install it. Then open a new terminal.",\n')
    found = findings(tmp_path, {"tools/inventory-runtimes.py": old})[0]
    assert sorted({(f[1], f[2]) for f in found}) == [(1, "download-unverified"), (2, "download-unverified"),
                                                    (3, "download-unverified"), (3, "follows-latest")]


def test_the_real_tree_install_refs_agree_with_their_pins(tmp_path):
    """Every version a shipped file states for a pinned tool equals its pin (offline: install refs are local), setup.sh
    included: its exemption (owed to T7) outlived the drift it excused and hid every later one (review L4b L3)."""
    rows = ta.run(REPO, ta.Fetcher(tmp_path / "empty", offline=True, env={}))
    bad = [r.detail for r in rows if r.check == "install refs" and r.status == ta.DRIFT]
    assert bad == [], bad


def test_a_doc_stating_another_jdk_than_the_image_is_drift(tmp_path):
    files = {".devcontainer/Dockerfile": "ARG JDK_VERSION=21.0.12\n",
             "docs/container-vs-windows.md": "| Parse a save | Container | ReSaver CLI (JDK 17 is in the image) |\n"}
    rows, code = audit(make_repo(tmp_path, files), tmp_path / "c", ["Microsoft OpenJDK"])
    assert any(r.check == "install refs" and "container-vs-windows.md:1 says 17" in r.detail
               for r in by(rows, ta.DRIFT)), [r.as_dict() for r in rows]
    files["docs/container-vs-windows.md"] = files["docs/container-vs-windows.md"].replace("JDK 17", "JDK 21.0.12")
    rows, _ = audit(make_repo(tmp_path / "ok", files), tmp_path / "c", ["Microsoft OpenJDK"])
    assert not [r for r in by(rows, ta.DRIFT) if r.check == "install refs"]


# ------------------------------------------------------------------ Git for Windows: one tested version, every path
# The Git for Windows the suites run under (MEASURED 2026-10-13 on the dev machine: `git --version` prints
# 2.55.0.windows.5), and its installer's SHA-256: downloaded and hashed, equal to the digest GitHub publishes for the
# release asset and to the InstallerSha256 in winget's Git.Git 2.55.0.5 manifest; Authenticode signature valid.
TESTED_GIT, TESTED_GIT_TAG = "2.55.0.5", "v2.55.0.windows.5"
TESTED_GIT_SHA = "d065a4e23c3d9a6b5073d609b5be0830227ec3ca053c083ba385061ddfaf94c6"
GIT_SPEC = next(s for s in ta.REGISTRY if s["name"] == "Git for Windows")


def _inventory():
    spec = importlib.util.spec_from_file_location("inventory_runtimes", REPO / "tools" / "inventory-runtimes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_git_for_windows_pin_names_the_tested_version():
    """Each delivery path names the one tested Git: the manual steps the toolkit-setup skill prints (version,
    release tag and hash), the version setup.sh reports as tested, and the docs' install line. Red while any of
    them still says an older Git (2.48.1 until 2026-10-13)."""
    repo = ta.Repo(REPO)
    sites = [ta.read_pin_site(repo, s) for s in GIT_SPEC["pins"]]
    assert sites and all(v == TESTED_GIT for _label, v in sites), sites
    live = [h for ref in GIT_SPEC["refs"] for h in ta.scan_refs(repo, ref) if h[2] != "history"]
    assert {h[0] for h in live} >= {"tools/inventory-runtimes.py", "setup.sh", "docs/installing-tools.md",
                                    "README.md"}, live
    assert [h for h in live if h[2] != TESTED_GIT] == [], live
    steps = _inventory().MANUAL_STEPS["git"]
    assert "Git for Windows %s " % TESTED_GIT in steps and "Git-%s-64-bit.exe" % TESTED_GIT in steps, steps
    assert "/releases/download/%s/" % TESTED_GIT_TAG in steps and TESTED_GIT_SHA in steps, steps


GIT_STEPS = ('    "git": "Install Git for Windows 2.55.0.5 (it includes Git Bash): download Git-2.55.0.5-64-bit.exe from "\n'
             '           "https://github.com/git-for-windows/git/releases/download/v2.55.0.windows.5/ -- " + _VERIFY\n'
             '           + "%s -- and run it. Then open a new terminal.",\n')
GIT_RELEASE_URL = "https://api.github.com/repos/git-for-windows/git/releases/tags/v2.55.0.windows.5"
GIT_LATEST_URL = "https://api.github.com/repos/git-for-windows/git/releases/latest"


@pytest.mark.parametrize("sha,want", [(TESTED_GIT_SHA, ta.OK), ("f" * 64, ta.DRIFT)])
def test_the_pinned_git_installer_hash_is_the_one_github_publishes(tmp_path, sha, want):
    release = json.dumps({"tag_name": TESTED_GIT_TAG, "assets": [
        {"name": "Git-2.55.0.5-64-bit.exe", "digest": "sha256:" + TESTED_GIT_SHA},
        {"name": "unrelated-installer.exe", "digest": "sha256:" + "f" * 64}]}).encode()
    repo = make_repo(tmp_path, {"tools/inventory-runtimes.py": GIT_STEPS % sha})
    cache = make_cache(tmp_path, extra=[(GIT_RELEASE_URL, 200, release), (GIT_LATEST_URL, 200, release)])
    rows, _code = audit(repo, cache, ["Git for Windows"])
    assert [r.status for r in rows if r.check == "pinned hash"] == [want], [r.as_dict() for r in rows]
    assert [r.status for r in rows if r.check == "version"] == [ta.OK], [r.as_dict() for r in rows]


# Commit-published xeditlib has a package version, not necessarily a matching release/tag.
XEDIT_COMMIT = "1" * 40
XEDIT_URL = "https://raw.githubusercontent.com/WingedGuardian/xeditlib/" + XEDIT_COMMIT + "/package.json"


def xedit_repo(tmp_path, *, version="1.0.2", documentation=True, extra_doc="", hint=None):
    url = "https://codeload.github.com/WingedGuardian/xeditlib/tar.gz/" + XEDIT_COMMIT
    files = {
        "package.json": json.dumps({"optionalDependencies": {"xeditlib": url}}),
        "package-lock.json": json.dumps({"packages": {"node_modules/xeditlib": {
            "resolved": url, "version": version}}}),
    }
    if documentation:
        files["docs/installing-tools.md"] = "xeditlib v1.0.2 = commit `" + XEDIT_COMMIT + "`\n" + extra_doc
    if hint:
        files["setup.sh"] = hint
    return make_repo(tmp_path, files)


def xedit_cache(tmp_path, *, status=200, body=None):
    if body is None:
        body = b'{"name":"xeditlib","version":"1.0.2"}'
    # No tag/release response is supplied: a regression must not silently need one.
    cache = tmp_path / "cache"
    ta.Fetcher(cache, offline=True, env={}).put(XEDIT_URL, status, body)
    return cache


def test_xeditlib_commit_package_does_not_require_a_matching_git_tag(tmp_path):
    rows, code = audit(xedit_repo(tmp_path), xedit_cache(tmp_path), ["xeditlib"])
    assert code == 0, [r.as_dict() for r in rows]
    assert {r.check for r in rows} == {"install refs", "commit package"}
    assert any(r.upstream == "1.0.2" and r.status == ta.OK for r in rows)


def test_xeditlib_missing_commit_package_is_drift(tmp_path):
    rows, code = audit(xedit_repo(tmp_path), xedit_cache(tmp_path, status=404, body=b"missing"), ["xeditlib"])
    assert code == 1 and any("absent at the pinned" in r.detail for r in by(rows, ta.DRIFT))


@pytest.mark.parametrize("body", [b"not JSON", b"[]", b'{"name":"xeditlib"}'])
def test_xeditlib_unreadable_commit_metadata_is_unchecked(tmp_path, body):
    rows, code = audit(xedit_repo(tmp_path), xedit_cache(tmp_path, body=body), ["xeditlib"])
    assert code == 2 and by(rows, ta.UNCHECKED), [r.as_dict() for r in rows]


def test_xeditlib_wrong_upstream_package_is_drift(tmp_path):
    rows, code = audit(xedit_repo(tmp_path), xedit_cache(tmp_path, body=b'{"name":"other","version":"1.0.2"}'), ["xeditlib"])
    assert code == 1 and any("package name differs" in r.detail for r in by(rows, ta.DRIFT))


def test_xeditlib_locked_package_version_must_match_the_commit(tmp_path):
    rows, code = audit(xedit_repo(tmp_path, version="1.0.1"), xedit_cache(tmp_path), ["xeditlib"])
    assert code == 1 and any("package-lock.json" in r.detail for r in by(rows, ta.DRIFT))


def test_xeditlib_missing_documented_package_version_is_unchecked(tmp_path):
    rows, code = audit(xedit_repo(tmp_path, documentation=False), xedit_cache(tmp_path), ["xeditlib"])
    assert code == 2 and any("docs/installing-tools.md" in r.detail for r in by(rows, ta.UNCHECKED))


def test_xeditlib_conflicting_documented_versions_are_drift(tmp_path):
    rows, code = audit(xedit_repo(tmp_path, extra_doc="xeditlib v1.0.1 = commit `" + XEDIT_COMMIT + "`"),
                       xedit_cache(tmp_path), ["xeditlib"])
    assert code == 1 and any("disagrees with itself" in r.detail for r in by(rows, ta.DRIFT))


def test_xeditlib_setup_commit_hint_without_backticks_is_checked(tmp_path):
    hint = 'echo "xeditlib v1.0.1 = commit 2222222 + koffi 2.15.1"\n'
    rows, code = audit(xedit_repo(tmp_path, hint=hint), xedit_cache(tmp_path), ["xeditlib"])
    assert code == 1 and any("setup.sh:1 says 2222222" in r.detail for r in by(rows, ta.DRIFT))


def test_xeditlib_audit_does_not_attribute_another_tools_commit_to_xeditlib(tmp_path):
    hint = 'echo "Ask which tools: xeditlib, AutoMod; AutoMod tag v1.11.2 (commit 2222222)"\n'
    rows, code = audit(xedit_repo(tmp_path, hint=hint), xedit_cache(tmp_path), ["xeditlib"])
    assert code == 0, [r.as_dict() for r in rows]


# ------------------------------------------------------------------ v4.0.0: tools pinned by commit, with no upstream tags

PIN = "0d48f60cb8a69b367abfbd78d58418e64c507da5"
RESAVERPP = {
    "tools/resaver-plus/build.py": 'UPSTREAM_COMMIT = "%s"\n' % PIN,
    "tools/resaver_native.py": 'UPSTREAM_COMMIT = "%s"\n' % PIN,
    "tools/resaver-plus/README.md": "| Upstream | [Tonycubed2/ResaverPP](x), version **19.6**, commit `%s` |\n" % PIN,
}
HEAD_URL = "https://api.github.com/repos/Tonycubed2/ResaverPP/commits/HEAD"


def _head(sha):
    return [(HEAD_URL, 200, json.dumps({"sha": sha}).encode())]


def test_a_commit_pin_at_the_upstream_head_is_ok(tmp_path):
    rows, code = audit(make_repo(tmp_path, RESAVERPP), make_cache(tmp_path, extra=_head(PIN)), ["ReSaver Plus (ReSaverPP)"])
    assert code == 0 and {r.status for r in rows} == {ta.OK}, [r.as_dict() for r in rows]
    assert {"version", "install refs"} <= {r.check for r in rows}


def test_an_upstream_head_past_the_commit_pin_is_newer_with_the_note(tmp_path):
    rows, code = audit(make_repo(tmp_path, RESAVERPP), make_cache(tmp_path, extra=_head("3" * 40)),
                       ["ReSaver Plus (ReSaverPP)"])
    newer = by(rows, ta.NEWER)
    assert code == 0 and len(newer) == 1 and "NOT adopted" in newer[0].detail, [r.as_dict() for r in rows]


def test_commit_pin_sites_that_disagree_are_drift(tmp_path):
    files = dict(RESAVERPP, **{"tools/resaver_native.py": 'UPSTREAM_COMMIT = "%s"\n' % ("1" * 40)})
    rows, code = audit(make_repo(tmp_path, files), make_cache(tmp_path, extra=_head(PIN)), ["ReSaver Plus (ReSaverPP)"])
    assert code == 1 and any(r.check == "pin sites" for r in by(rows, ta.DRIFT))


def test_a_doc_naming_another_commit_is_drift(tmp_path):
    files = dict(RESAVERPP, **{"tools/resaver-plus/README.md": "Tonycubed2/ResaverPP at commit `3958350`\n"})
    rows, code = audit(make_repo(tmp_path, files), make_cache(tmp_path, extra=_head(PIN)), ["ReSaver Plus (ReSaverPP)"])
    assert code == 1 and any(r.check == "install refs" for r in by(rows, ta.DRIFT))


def test_a_commit_pin_offline_without_a_cached_head_is_could_not_check(tmp_path):
    rows, code = audit(make_repo(tmp_path, RESAVERPP), make_cache(tmp_path), ["ReSaver Plus (ReSaverPP)"])
    assert code == 2 and by(rows, ta.UNCHECKED)
