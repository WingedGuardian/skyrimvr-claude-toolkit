"""tools/skse-load-status.py: what SKSE did with each plugin DLL, read from SKSE's own log.

The logs here are hand-authored in the two shapes SKSE writes (see the tool's docstring for the sources):
VR / SKSE64 2.0.x print each verdict right after its "checking plugin" line, with a full path; SKSE64
2.1+ prints a bare DLL name and collects every "loaded correctly" line at the END, so a parser that
pairs a verdict with the line above it misattributes every SE/AE plugin. Plugin names are made up.

Every count assertion is paired with the RESULT line, because the tool's promise is that its rows sum to
the plugins it checked -- and test_two_verdicts_for_one_plugin_is_an_accounting_mismatch proves that
promise can go red.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from conftest import REPO

TOOL = REPO / "tools" / "skse-load-status.py"
VR_DIR = "C:\\Games\\Skyrim VR\\Data\\SKSE\\Plugins\\"

VR_LOG = "\n".join([
    "SKSEVR runtime: initialize (version = 2.0.12 01040F00 01D00000000000, os = 6.2 (9200))",
    "plugin directory = " + VR_DIR,
    "checking plugin " + VR_DIR + "\\AlphaFix.dll",
    "registering plugin listener for SKSE at 1 of 2",
    "plugin 1 allocated 1024 bytes from branch pool",
    "Plugin 1 registered for poses with sort 11",
    "plugin " + VR_DIR + "\\AlphaFix.dll (00000001 Alpha Fix 00000102) loaded correctly (handle 1)",
    "checking plugin " + VR_DIR + "\\BetaTool.dll",
    "plugin " + VR_DIR + "\\BetaTool.dll (00000001 Beta Tool 00000003) reported as incompatible during query (handle 2)",
    "checking plugin " + VR_DIR + "\\helper140.dll",
    "plugin " + VR_DIR + "\\helper140.dll does not appear to be an SKSE plugin",
    "checking plugin " + VR_DIR + "\\GammaHooks.dll",
    "plugin " + VR_DIR + "\\GammaHooks.dll (00000001 Gamma Hooks 00010510) reported as incompatible during load (handle 4)",
    "checking plugin " + VR_DIR + "\\DeltaSilent.dll",
    "dispatch message (5) to plugin listeners",
    "sending message type 5 to plugin 1",
    "",
])

SE_LOG = "\n".join([
    "SKSE64 runtime: initialize (version = 2.2.6 01060470 01D00000000000, os = 6.2 (9200))",
    "plugin directory = C:\\Games\\SkyrimSE\\Data\\SKSE\\Plugins\\",
    "scanning plugin directory C:\\Games\\SkyrimSE\\Data\\SKSE\\Plugins\\",
    "checking plugin AlphaFix.dll",
    "checking plugin BetaTool.dll",
    "plugin BetaTool.dll (00000001 Beta Tool 00010000) disabled, address library needs to be updated 0 (handle 0)",
    "checking plugin OldLE.dll",
    "plugin OldLE.dll (00000000  00000000) LE plugin cannot be used with SE 0 (handle 0)",
    "checking plugin NoVersion.dll",
    "plugin NoVersion.dll (00000000  00000000) no version data 0 (handle 0)",
    "checking plugin Missing.dll",
    "plugin Missing.dll (00000000  00000000) couldn't load plugin 126 (handle 0)",
    "loading plugin \"Alpha Fix\"",
    "plugin AlphaFix.dll (00000001 Alpha Fix 00000102) loaded correctly (handle 1)",
    "",
])


def run(*args, env=None):
    e = dict(os.environ)
    e.pop("SKSE_LOG_DIR", None)
    e.update(env or {})
    return subprocess.run([sys.executable, "-B", str(TOOL), *args], capture_output=True, text=True,
                          timeout=120, env=e)


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def count(out, label):
    line = next((ln for ln in out.splitlines() if ln.strip().startswith(label)), "")
    assert line, "no '{}' row:\n{}".format(label, out)
    return int(line.split(":")[1].split()[0])


def test_vr_log_every_verdict_and_the_rows_sum(tmp_path):
    r = run("--log", str(write(tmp_path, "sksevr.log", VR_LOG)))
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "5 shown of 5 checked" in out, out
    assert (count(out, "loaded"), count(out, "incompatible"), count(out, "not an SKSE plugin"),
            count(out, "other error"), count(out, "no result line"), count(out, "unmatched results")) == (1, 2, 1, 0, 1, 0)
    assert "RESULT: OK (rows sum to 5 checked)" in out, out


def test_vr_rows_carry_the_raw_version_and_the_reported_name(tmp_path):
    out = run("--log", str(write(tmp_path, "sksevr.log", VR_LOG))).stdout
    alpha = next(ln for ln in out.splitlines() if "AlphaFix.dll" in ln)
    assert "00000102" in alpha and "loaded correctly" in alpha and "Alpha Fix" in alpha, alpha
    # The path is reduced to the DLL name: rows are about plugins, not about where the game is.
    assert "C:\\Games" not in alpha, alpha


def test_a_plugin_with_no_verdict_is_shown_not_dropped(tmp_path):
    out = run("--log", str(write(tmp_path, "sksevr.log", VR_LOG))).stdout
    assert any("DeltaSilent.dll" in ln and "NO RESULT LINE" in ln for ln in out.splitlines()), out


def test_an_incompatible_plugin_names_the_phase_it_failed_in(tmp_path):
    out = run("--log", str(write(tmp_path, "sksevr.log", VR_LOG))).stdout
    assert any("BetaTool.dll" in ln and "reported as incompatible during query" in ln for ln in out.splitlines()), out
    assert any("GammaHooks.dll" in ln and "reported as incompatible during load" in ln for ln in out.splitlines()), out


def test_se_verdicts_printed_after_all_checks_are_matched_by_name(tmp_path):
    """SKSE64 2.1+: the "loaded correctly" line comes after every other plugin's check."""
    r = run("--log", str(write(tmp_path, "skse64.log", SE_LOG)))
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    lines = out.splitlines()
    assert any("AlphaFix.dll" in ln and "loaded correctly" in ln for ln in lines), out
    assert not any("Missing.dll" in ln and "loaded correctly" in ln for ln in lines), out
    assert (count(out, "loaded"), count(out, "other error"), count(out, "no result line")) == (1, 4, 0)
    assert "RESULT: OK (rows sum to 5 checked)" in out, out


def test_se_error_code_is_split_from_the_reason(tmp_path):
    out = run("--log", str(write(tmp_path, "skse64.log", SE_LOG))).stdout
    missing = next(ln for ln in out.splitlines() if "Missing.dll" in ln)
    assert "couldn't load plugin (code 126)" in missing, missing
    beta = next(ln for ln in out.splitlines() if "BetaTool.dll" in ln)
    assert "disabled, address library needs to be updated (code 0)" in beta, beta


def test_the_filter_matches_the_reported_name_too(tmp_path):
    out = run("gamma hooks", "--log", str(write(tmp_path, "sksevr.log", VR_LOG))).stdout
    assert "1 shown of 5 checked" in out, out
    assert "GammaHooks.dll" in out


def test_two_verdicts_for_one_plugin_is_an_accounting_mismatch(tmp_path):
    """The check that the rows sum to the checked plugins must be able to go red."""
    doubled = VR_LOG + "plugin " + VR_DIR + "\\AlphaFix.dll (00000001 Alpha Fix 00000102) loaded correctly (handle 1)\n"
    r = run("--log", str(write(tmp_path, "sksevr.log", doubled)))
    assert r.returncode == 1, r.stdout
    assert "ACCOUNTING MISMATCH" in r.stdout and "RESULT: OK" not in r.stdout, r.stdout


@pytest.mark.parametrize("args", [["-x"], ["--all"], ["a", "b"]])
def test_bad_usage_is_exit_2(tmp_path, args):
    """With a readable log, so the refusal can only come from the argument check (an unknown flag taken as a
    filter would print an empty table and exit 0)."""
    r = run(*args, "--log", str(write(tmp_path, "sksevr.log", VR_LOG)))
    assert r.returncode == 2, r.stdout + r.stderr
    assert r.stderr.startswith("usage:"), r.stderr


def test_log_without_a_path_is_exit_2():
    r = run("--log")
    assert r.returncode == 2 and r.stderr.startswith("usage:"), r.stderr


def test_a_missing_log_is_exit_2(tmp_path):
    assert run("--log", str(tmp_path / "nope.log")).returncode == 2
    r = run(env={"SKSE_LOG_DIR": str(tmp_path)})
    assert r.returncode == 2 and "SKSE_LOG_DIR" in r.stderr, r.stderr


def test_the_newest_log_wins_and_the_other_is_named(tmp_path):
    old = write(tmp_path, "sksevr.log", VR_LOG)
    new = write(tmp_path, "skse64.log", SE_LOG)
    t = time.time()
    os.utime(old, (t - 7200, t - 7200))
    os.utime(new, (t, t))
    out = run(env={"SKSE_LOG_DIR": str(tmp_path)}).stdout
    assert "SKSE log: " + str(new) in out, out
    assert "also present, not read: " + str(old) in out, out


def test_discovery_finds_a_gog_se_folder(tmp_path):
    """GOG SE/AE writes under "Skyrim Special Edition GOG" (SKSE64's SAVE_FOLDER_NAME)."""
    skse = tmp_path / "Documents" / "My Games" / "Skyrim Special Edition GOG" / "SKSE"
    skse.mkdir(parents=True)
    log = write(skse, "skse64.log", SE_LOG)
    r = run(env={"USERPROFILE": str(tmp_path), "HOME": str(tmp_path)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SKSE log: " + str(log) in r.stdout, r.stdout
