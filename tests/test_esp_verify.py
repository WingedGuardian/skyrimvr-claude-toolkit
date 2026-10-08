"""esp-verify-wrapper.sh must not report PASS for an inventory it never actually built.

P13: a serializer that emitted nothing (a missing .NET runtime, a crash that still exits 0, a
wrong package version) produced an empty snapshot and an empty "after", and an empty file diffed
against an empty file is "no cross-reference changes" -- a PASS from a check that looked at
nothing. A fake SPRIGGIT stands in for the real one so no .NET and no plugin are needed.
"""
import subprocess

from conftest import BASH, REPO

WRAPPER = (REPO / "tools" / "esp-verify-wrapper.sh").as_posix()

# Modes (FAKE_SPRIGGIT_MODE): none = writes nothing; noref = a record with no cross-reference;
# refs = a record that references Skyrim.esm.
FAKE = """#!/bin/bash
while [ "$#" -gt 0 ]; do
  case "$1" in -o) out="$2"; shift 2;; *) shift;; esac
done
case "${FAKE_SPRIGGIT_MODE:-none}" in
  none) ;;
  noref) printf 'EditorID: Lonely\\n' > "$out/Lonely - 000800_Mod.esp.yaml" ;;
  refs) printf 'Keyword: 013794:Skyrim.esm\\n' > "$out/Thing - 000800_Mod.esp.yaml" ;;
esac
exit 0
"""


def _run(tmp_path, mode, *args, after_plugin=()):
    fake = tmp_path / "fake-spriggit"
    if not fake.exists():
        fake.write_text(FAKE, newline="\n")
        fake.chmod(0o755)
    plugin = tmp_path / "Mod.esp"
    plugin.write_bytes(b"TES4")
    import os
    env = dict(os.environ, SPRIGGIT=fake.as_posix(), ESP_VERIFY_SNAPDIR=(tmp_path / "snaps").as_posix(),
               FAKE_SPRIGGIT_MODE=mode)
    return subprocess.run([BASH, WRAPPER, args[0], *args[1:], plugin.as_posix(), *after_plugin],
                          capture_output=True, text=True, env=env, timeout=120)


def test_snapshot_dies_when_the_serializer_yields_no_yaml(tmp_path):
    r = _run(tmp_path, "none", "snapshot")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "no YAML" in r.stderr


def test_verify_dies_when_the_serializer_yields_no_yaml(tmp_path):
    assert _run(tmp_path, "refs", "snapshot").returncode == 0
    r = _run(tmp_path, "none", "verify")
    assert r.returncode == 2, r.stdout + r.stderr


def test_two_empty_inventories_are_not_a_pass(tmp_path):
    """The false PASS: YAML exists but holds no cross-references, before and after."""
    assert _run(tmp_path, "noref", "snapshot").returncode == 0
    r = _run(tmp_path, "noref", "verify")
    assert r.returncode != 0, "empty vs empty must not PASS:\n" + r.stdout
    assert "PASS" not in r.stdout.replace("REFUSING PASS", "")
    assert "--allow-empty" in r.stdout + r.stderr


def test_allow_empty_accepts_a_plugin_with_genuinely_no_references(tmp_path):
    assert _run(tmp_path, "noref", "snapshot").returncode == 0
    r = _run(tmp_path, "noref", "verify", "--allow-empty")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout


def test_a_populated_inventory_still_passes_without_the_flag(tmp_path):
    # The twin that keeps the refusals above honest: a real, unchanged inventory is a PASS.
    assert _run(tmp_path, "refs", "snapshot").returncode == 0
    r = _run(tmp_path, "refs", "verify")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout


def test_allow_empty_still_lists_the_references_that_appeared(tmp_path):
    """Empty snapshot, populated after, under --allow-empty: the additions must be PRINTED. The
    branch for this once carried a literal newline inside an awk string, an awk syntax error that
    swallowed the list and still said PASS."""
    assert _run(tmp_path, "noref", "snapshot").returncode == 0
    r = _run(tmp_path, "refs", "verify", "--allow-empty")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "reference added" in r.stdout and "013794:Skyrim.esm" in r.stdout, r.stdout + r.stderr
    assert "syntax error" not in r.stderr


def test_allow_empty_is_accepted_after_the_plugin_too(tmp_path):
    assert _run(tmp_path, "noref", "snapshot").returncode == 0
    r = _run(tmp_path, "noref", "verify", after_plugin=("--allow-empty",))
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_unknown_option_is_a_usage_error_not_a_plugin_path(tmp_path):
    assert _run(tmp_path, "refs", "snapshot").returncode == 0
    r = _run(tmp_path, "refs", "verify", after_plugin=("--bogus",))
    assert r.returncode == 2 and "unknown option" in r.stderr, r.stdout + r.stderr


def test_the_refusal_message_is_grammatical(tmp_path):
    assert _run(tmp_path, "noref", "snapshot").returncode == 0
    r = _run(tmp_path, "noref", "verify")
    assert "both the snapshot and the new inventory hold 0" in r.stdout, r.stdout


def test_references_vanishing_to_empty_still_fails_with_the_flag(tmp_path):
    # --allow-empty excuses a plugin that never had references, not one that lost them all.
    assert _run(tmp_path, "refs", "snapshot").returncode == 0
    r = _run(tmp_path, "noref", "verify", "--allow-empty")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "DROPPED" in r.stdout


# --- Spriggit restored from the repo's tool manifest (review I3) and the pinned package version (M7) ---
# installing-tools documents `dotnet tool restore`: the pinned version, but no `spriggit` on PATH. The ESP
# guard died with "spriggit not found" on that healthy install. A fake `dotnet` (ESP_VERIFY_DOTNET seam: the
# harness shadows the real name) answers `tool run spriggit -- ...` only from a folder holding the manifest,
# like the real one, and records the --PackageVersion it was given.

def _path_without_spriggit():
    import os
    import shutil
    return os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep)
                           if d and not (shutil.which("spriggit", path=d) or shutil.which("spriggit.exe", path=d)))


LOCAL_DOTNET = """#!/bin/bash
[ "$1 $2 $3 $4" = "tool run spriggit --" ] || exit 1
[ -f .config/dotnet-tools.json ] || { echo "Cannot find a manifest file." >&2; exit 1; }
shift 4
# MEASURED 2026-10-05: the real `spriggit --version` prints three lines and EXITS 1.
[ "$1" = "--version" ] && { printf 'Spriggit version 0.41.0+x\n--version\nSpriggit.CLI 0.41.0+x\n'; exit 1; }
while [ "$#" -gt 0 ]; do
  case "$1" in -o) out="$2"; shift 2;; -v) echo "$2" > "$VERSION_LOG"; shift 2;; -i) [ -f "$2" ] || exit 3; shift 2;; *) shift;; esac
done
printf 'Keyword: 013794:Skyrim.esm\n' > "$out/Thing - 000800_Mod.esp.yaml"
exit 0
"""


def _run_local(tmp_path, *args):
    import os
    fake = tmp_path / "fake-dotnet"
    fake.write_text(LOCAL_DOTNET, newline="\n")
    fake.chmod(0o755)
    plugin = tmp_path / "Mod.esp"
    plugin.write_bytes(b"TES4")
    env = {k: v for k, v in os.environ.items() if k != "SPRIGGIT"}
    env.update(PATH=_path_without_spriggit(), ESP_VERIFY_DOTNET=fake.as_posix(),
               ESP_VERIFY_SNAPDIR=(tmp_path / "snaps").as_posix(), VERSION_LOG=(tmp_path / "ver.txt").as_posix())
    # cwd is NOT the toolkit: the wrapper has to find the manifest itself, with the plugin path still valid.
    return subprocess.run([BASH, WRAPPER, *args, "Mod.esp"], capture_output=True, text=True, env=env,
                          timeout=120, cwd=str(tmp_path))


def test_a_manifest_installed_spriggit_drives_the_guard(tmp_path):
    r = _run_local(tmp_path, "snapshot")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not found" not in r.stderr and "1 cross-references" in r.stderr, r.stderr


def test_the_package_version_defaults_to_the_pin(tmp_path):
    import json
    pin = json.loads((REPO / ".config" / "dotnet-tools.json").read_text(encoding="utf-8"))["tools"]["spriggit.cli"]["version"]
    assert _run_local(tmp_path, "snapshot").returncode == 0
    assert (tmp_path / "ver.txt").read_text().strip() == pin
