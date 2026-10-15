"""tools/nexus.sh and tools/nexus-render.py: the Nexus desk review, with NO network.

nexus.sh is run from a copy in a temporary toolkit folder (so no real tools/.nexus_api_key is ever read) with a
fake `curl` first on PATH that answers from the hand-authored responses in tests/fixtures/nexus/. The renderer is
also driven directly. Nothing here can reach Nexus.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import BASH, REPO

FIX = REPO / "tests" / "fixtures" / "nexus"
RENDER = REPO / "tools" / "nexus-render.py"
KEY = "zq-fake-key-0123456789"

_spec = importlib.util.spec_from_file_location("nexus_render", RENDER)
nr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nr)

FAKE_CURL = r"""#!/usr/bin/env bash
# Test stand-in for curl: answers from $FAKE_NEXUS_DIR, appends the -w status line, logs what it was asked.
url=""; data=""; key=""; prev=""
for a in "$@"; do
    case "$prev" in --data-binary) data="$a" ;; -H) case "$a" in apikey:*) key="${a#apikey: }" ;; esac ;; esac
    case "$a" in http*) url="$a" ;; esac
    prev="$a"
done
printf '%s\t%s\n' "$url" "$key" >> "$FAKE_CURL_LOG"
[ -n "${FAKE_CURL_FAIL:-}" ] && exit 6
case "$url" in
    */graphql) case "$data" in *"mods("*) f="${FAKE_FIND:-find.json}" ;; *) f="req.json" ;; esac ;;
    */files.json) f="files.json" ;;
    */changelogs.json) f="changelogs.json" ;;
    */users/validate.json) f="validate.json" ;;
    */mods/*.json) f="mod.json" ;;
    *) f="" ;;
esac
code="${FAKE_CURL_CODE:-200}"
case "$url" in *"${FAKE_CURL_404_ON:-@none@}"*) code=404 ;; esac
[ -n "$f" ] && [ "$code" = "200" ] && cat "$FAKE_NEXUS_DIR/$f"
printf '\n%s' "$code"
"""


@pytest.fixture
def kit(tmp_path):
    tools = tmp_path / "toolkit" / "tools"
    tools.mkdir(parents=True)
    for name in ("nexus.sh", "nexus-render.py"):
        shutil.copy2(REPO / "tools" / name, tools / name)
    shutil.copytree(REPO / "tools/modding_harness", tools / "modding_harness",
                    ignore=shutil.ignore_patterns("__pycache__"))
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "curl").write_bytes(FAKE_CURL.encode("utf-8"))
    (fake / "curl").chmod(0o755)
    return tools


def run(kit, *args, key=KEY, **env):
    e = dict(os.environ)
    e.pop("NEXUS_API_KEY", None)
    if key:
        e["NEXUS_API_KEY"] = key
    e["PATH"] = str(kit.parent.parent / "bin").replace("\\", "/") + os.pathsep + e.get("PATH", "")
    e["FAKE_NEXUS_DIR"] = str(FIX)
    e["FAKE_CURL_LOG"] = str(kit.parent.parent / "curl.log")
    e.update({k: str(v) for k, v in env.items()})
    # Git for Windows' bin/bash launcher prepends /mingw64/bin to inherited PATH,
    # ahead of a fake curl. Bind and verify the fake INSIDE the started shell so
    # a fixture can never fall through to the real network client.
    shell = ('testbin="$1"; shift; '
             'if command -v cygpath >/dev/null 2>&1; then testbin=$(cygpath -u "$testbin"); fi; '
             'export PATH="$testbin:$PATH"; '
             '[ "$(command -v curl)" = "$testbin/curl" ] || exit 97; source "$@"')
    r = subprocess.run([BASH, "-c", shell, "--", str(kit.parent.parent / "bin"),
                        str(kit / "nexus.sh"), *args], capture_output=True, timeout=120, env=e)
    return subprocess.CompletedProcess(r.args, r.returncode, r.stdout.decode("utf-8", "replace"),
                                       r.stderr.decode("utf-8", "replace"))


def calls(kit):
    log = kit.parent.parent / "curl.log"
    return [ln.split("\t") for ln in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


# --------------------------------------------------------------------------- vkey

@pytest.mark.parametrize("newer, older", [
    ("3.100.11", "3.6.2"), ("3.6.2", "3.0.50b"), ("3.0.50b", "3.0.50a"), ("3.3.10", "3.0.35"), ("1.10", "1.9"),
    ("v2.0", "1.99"),
])
def test_vkey_orders_versions_numerically(newer, older):
    assert nr.vkey(newer) > nr.vkey(older)


@pytest.mark.parametrize("a, b", [("6.4", "6.4.0"), ("v6.4", "6.4"), ("6.4.0.0", "6.4")])
def test_vkey_treats_trailing_zeros_and_a_leading_v_as_equal(a, b):
    assert nr.vkey(a) == nr.vkey(b)


# --------------------------------------------------------------------------- the renderer

def render(*args, **env):
    e = dict(os.environ)
    e.update(env)
    r = subprocess.run([sys.executable, "-B", str(RENDER), *args], capture_output=True, timeout=60, env=e)
    return subprocess.CompletedProcess(r.args, r.returncode, r.stdout.decode("utf-8", "replace"),
                                       r.stderr.decode("utf-8", "replace"))


def fx(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_changelogs_since_the_installed_version_are_all_shown_newest_first():
    r = render("changelog", "--since", "3.3.10", LOG=fx("changelogs.json"))
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "changelogs newer than 3.3.10: 2 version(s)" in out
    assert out.index("3.100.11:") < out.index("3.6.2:"), "sorted as strings, not versions"
    assert "  3.0.35:" not in out and "  3.3.10:" not in out


def test_without_since_the_newest_are_capped_and_the_cap_is_said():
    out = render("changelog", LOG=json.dumps({str(i) + ".0": ["x"] for i in range(1, 12)})).stdout
    assert "newest 8 of 11" in out and "11.0:" in out and "3.0:" not in out


def test_page_tolerates_explicit_nulls_and_reports_every_part():
    r = render("page", "skyrimspecialedition", "101", "--since", "3.3.10", INFO=fx("mod.json"), FILES=fx("files.json"),
               LOG=fx("changelogs.json"), REQ=fx("req.json"))
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "Example Framework  [101]  by Some Author" in out
    assert "newest MAIN file is 3.6.3 -- the header above is stale" in out
    assert "- Example Library  (needed for the scripts)" in out and "- Address Library" in out
    assert "Skyrim VR users: get the VR file." in out and "[size" not in out and "<br" not in out
    assert "keyword hits in description:" in out and "uninstalling" in out and "requires" in out
    assert "> VR version." in out
    assert "?            ?" in out, "a file with null category and version must still print"


def test_page_says_requirements_could_not_be_fetched_not_none():
    out = render("page", "skyrimspecialedition", "101", INFO=fx("mod.json"), FILES=fx("files.json"), REQ="").stdout
    assert "could NOT fetch requirements -- not the same as none" in out


def test_page_with_no_mod_record_is_exit_2():
    r = render("page", "skyrimspecialedition", "9", INFO="{}", FILES=fx("files.json"))
    assert r.returncode == 2 and "no mod record" in r.stderr


def test_unparseable_responses_are_exit_2_never_a_blank_answer():
    assert render("page", "skyrimspecialedition", "1", INFO="<html>", FILES="{}").returncode == 2
    assert render("find", R="not json").returncode == 2
    assert render("find", R=fx("find_errors.json")).returncode == 2


def test_find_tolerates_null_fields():
    r = render("find", R=fx("find.json"))
    assert r.returncode == 0, r.stderr
    assert "[   101] Example Framework  v3.6.2  by Some Author  updated 2026-09-01" in r.stdout
    assert "[   202] Example Framework NG  v?  by ?  updated ?" in r.stdout


def test_contents_lists_a_preview_and_names_a_refusal(monkeypatch, capsys):
    def fake_fetch(url):
        if url.endswith("/12.json"):
            return "LISTED", json.loads(fx("preview.json"))
        return "REFUSED THIS CLIENT (HTTP 403) -- open the link in a browser", None
    monkeypatch.setattr(nr, "fetch", fake_fetch)
    monkeypatch.setenv("FILES", fx("files.json"))
    assert nr.main(["contents"]) == 0
    out = capsys.readouterr().out
    assert "LISTED: 2 file(s)" in out and "/SKSE/Plugins/ExampleFramework.dll  512 kB" in out
    assert "REFUSED THIS CLIENT (HTTP 403)" in out


def test_fetch_never_sends_a_browser_user_agent(monkeypatch):
    seen = {}

    class Refuse(Exception):
        pass

    def fake_urlopen(req, timeout=0):
        seen["ua"] = req.get_header("User-agent")
        raise nr.urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(nr.urllib.request, "urlopen", fake_urlopen)
    status, tree = nr.fetch("https://file-metadata.example.invalid/a b.json")
    assert tree is None and status.startswith("REFUSED THIS CLIENT (HTTP 403)")
    assert "Mozilla" not in seen["ua"] and seen["ua"] == nr.UA
    assert nr.fetch("http://insecure.example.invalid/x.json")[0].startswith("NOT FETCHED")


def test_the_requirements_query_only_takes_a_numeric_id():
    assert render("gql-req", "123", "skyrimspecialedition").returncode == 0
    assert render("gql-req", "1 } evil {", "skyrimspecialedition").returncode == 2
    assert render("gql-req", "123", "otherdomain").returncode == 2


# --------------------------------------------------------------------------- nexus.sh, through the fake curl

def test_page_end_to_end_and_the_key_is_used_but_never_printed(kit):
    r = run(kit, "page", "101", "--since", "3.3.10")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT: OK" in r.stdout and "changelogs newer than 3.3.10: 2 version(s)" in r.stdout
    assert "Example Library" in r.stdout
    assert KEY not in r.stdout and KEY not in r.stderr
    urls = [u for u, _ in calls(kit)]
    assert any(u.endswith("/mods/101.json") for u in urls) and any(u.endswith("/graphql") for u in urls)
    assert all(k == KEY for _, k in calls(kit)), "the key was not sent as the apikey header"


def test_find_end_to_end(kit):
    r = run(kit, "find", "Example Framework")
    assert r.returncode == 0 and "[   101] Example Framework" in r.stdout and "RESULT: OK" in r.stdout, r.stdout + r.stderr


def test_an_http_error_is_could_not_check_exit_3(kit):
    r = run(kit, "page", "101", FAKE_CURL_CODE=401)
    assert r.returncode == 3, r.stdout + r.stderr
    assert "RESULT: COULD-NOT-CHECK" in r.stdout and "HTTP 401" in r.stderr
    assert "RESULT: OK" not in r.stdout


def test_a_network_failure_is_could_not_check_exit_3(kit):
    r = run(kit, "mod", "101", FAKE_CURL_FAIL=1)
    assert r.returncode == 3 and "RESULT: COULD-NOT-CHECK" in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("args", [["find", "x"], ["mod", "1"], ["validate"], ["raw", "/users/validate.json"],
                                  ["page", "1"], ["contents", "1"]])
def test_no_key_is_could_not_check_exit_3_and_calls_nothing(kit, args):
    """Every command, the older mod / validate / raw included (they used to exit 1)."""
    r = run(kit, *args, key="")
    assert r.returncode == 3 and "No Nexus API key" in r.stderr and "RESULT: COULD-NOT-CHECK" in r.stdout
    assert calls(kit) == []


def test_a_search_error_is_could_not_check(kit):
    r = run(kit, "find", "x", FAKE_FIND="find_errors.json")
    assert r.returncode == 3 and "RESULT: COULD-NOT-CHECK" in r.stdout, r.stdout + r.stderr


def test_a_missing_changelog_does_not_fail_the_page_but_is_not_called_none(kit):
    r = run(kit, "page", "101", FAKE_CURL_404_ON="changelogs.json")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "changelogs: could NOT fetch or read them -- not the same as none" in r.stdout, r.stdout
    assert "no changelog published" not in r.stdout


@pytest.mark.parametrize("args", [[], ["scan"], ["check", "1"], ["info", "1"], ["page"], ["page", "abc"],
                                  ["page", "1", "--bogus"], ["find"], ["mod", "1", "bad/domain"]])
def test_bad_usage_and_the_dropped_commands_are_exit_2(kit, args):
    r = run(kit, *args)
    assert r.returncode == 2, r.stdout + r.stderr
    assert calls(kit) == []
