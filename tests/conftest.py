"""Shared fixtures for the toolkit's behavioral tests.

Every test gets its own copy of the repo in a temp dir and runs the *real*
`setup.sh` against it, then asserts on what setup.sh **wrote** -- never on what
it printed. Log text is cosmetic; the written paths are the contract. That
distinction is exactly what the `setup-smoke` CI job got wrong: it ran setup.sh
and then only checked that settings.json parsed, so every path bug this toolkit
has ever shipped would have passed it.

Fixtures are *generated*, not committed as literal directories: MO2 detection
compares absolute paths against the game root, so the layout has to be built
fresh under each test's tmp_path.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BIN = Path(__file__).resolve().parent / "bin"
BS = chr(92)  # backslash, spelled this way to keep escapes out of this file


def _resolve_bash() -> str:
    """Find a bash that actually works.

    `bash` on PATH is not necessarily a usable one. On a GitHub Windows runner
    it resolves to C:\\Windows\\System32\\bash.exe -- the WSL launcher -- which
    exits 1 when no distro is installed. A workflow's `shell: bash` fixes the
    step's own shell but not what subprocess finds, so every test that spawned
    bash failed there with a bare non-zero exit and no useful message.

    Candidates are probed rather than assumed: whichever first answers a
    trivial command wins.
    """
    candidates = [
        os.environ.get("TOOLKIT_BASH"),
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        "/bin/bash",
        "/usr/bin/bash",
    ]
    tried = []
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        tried.append(candidate)
        try:
            probe = subprocess.run([candidate, "-c", "echo toolkit-ok"],
                                   capture_output=True, text=True, timeout=60)
        except OSError:
            continue
        if probe.returncode == 0 and "toolkit-ok" in probe.stdout:
            return candidate
    raise RuntimeError(f"no working bash found; tried: {tried}")


BASH = _resolve_bash()

# setup.sh shells out to `winget install` when it cannot find jq, which would be
# a slow, network-dependent step that can hang a CI run. Every invocation is
# capped so a stuck install fails the test instead of the job.
SETUP_TIMEOUT = 240

IGNORE = shutil.ignore_patterns(
    ".git", "tests", "__pycache__", "node_modules", ".pytest_cache", ".venv"
)


# --------------------------------------------------------------------------
# running setup.sh
# --------------------------------------------------------------------------

def copy_repo(dest: Path) -> Path:
    """A pristine copy of the toolkit, minus the test suite itself."""
    shutil.copytree(REPO, dest, ignore=IGNORE)
    return dest


def make_game(tmp_path: Path, name: str = "game", exe: str = "SkyrimVR.exe") -> Path:
    """A toolkit copy plus a stubbed executable.

    The stub matters: without an .exe, setup.sh asks `read -p "Continue
    anyway?"` and would block on stdin.
    """
    d = copy_repo(tmp_path / name)
    (d / exe).write_bytes(b"")
    return d


def game_root_win(game: Path) -> str:
    """The path setup.sh computes for itself -- `pwd -W` on Windows, `pwd`
    elsewhere. Fixtures must agree with it or MO2 detection can never match."""
    r = subprocess.run(
        [BASH, "-c", "pwd -W 2>/dev/null || pwd"],
        cwd=str(game), capture_output=True, text=True, check=True,
    )
    return r.stdout.strip()


def run_setup(cwd: Path, env_overrides: dict | None = None, shim_path: bool = False):
    """Run the real setup.sh against `cwd`. Returns a CompletedProcess.

    `shim_path` puts tests/bin first on PATH, which is how a test takes control
    of an external command setup.sh depends on (`which`, `powershell`) without
    adding a test-only seam to the shipped script.
    """
    env = dict(os.environ)
    env.setdefault("USERNAME", "testuser")
    if shim_path:
        env["PATH"] = f"{BIN}{os.pathsep}{env['PATH']}"
    if env_overrides:
        env.update({k: str(v) for k, v in env_overrides.items()})
    return subprocess.run(
        [BASH, "setup.sh"],
        cwd=str(cwd), env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=SETUP_TIMEOUT,
    )


# --------------------------------------------------------------------------
# reading what setup.sh wrote
# --------------------------------------------------------------------------

def claude_md(game: Path) -> str:
    return (game / "CLAUDE.md").read_text(encoding="utf-8")


def hook_jq_lines(game: Path) -> dict[str, str]:
    """The `JQ="..."` line from every hook setup.sh configures."""
    found: dict[str, str] = {}
    for hook in sorted((game / ".claude" / "hooks").glob("*.sh")):
        for line in hook.read_text(encoding="utf-8").splitlines():
            if line.startswith("JQ="):
                found[hook.name] = line.strip()
                break
    return found


def key_path(game: Path, label: str) -> str:
    """Pull one `- **Label**: `value`` line out of the emitted CLAUDE.md."""
    for line in claude_md(game).splitlines():
        if line.startswith(f"- **{label}**"):
            return line
    return ""


# --------------------------------------------------------------------------
# MO2 layouts
# --------------------------------------------------------------------------

def qsettings(value: str, byte_array: bool = False) -> str:
    """Encode a path the way MO2's QSettings writes it: backslash-separated
    with the backslashes doubled, and -- for values Qt considers binary, which
    routinely includes gamePath and selected_profile -- wrapped in @ByteArray()."""
    v = value.replace("/", BS).replace(BS, BS * 2)
    return f"@ByteArray({v})" if byte_array else v


def write_mo2_ini(path: Path, game_path: str, profile: str, base_dir: str,
                  byte_array: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "[General]\n"
        "gameName=Skyrim Special Edition\n"
        f"gamePath={qsettings(game_path, byte_array)}\n"
        f"selected_profile={qsettings(profile, byte_array) if byte_array else profile}\n"
        "version=2.5.2\n"
        "\n"
        "[Settings]\n"
        f"base_directory={qsettings(base_dir)}\n"
    )
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def make_mo2_tree(base: Path, profile: str) -> Path:
    """mods/ + profiles/<profile>/ + overwrite/, with profile-local INIs and a
    load order (setup.sh only redirects CONFIG_DIR when those files exist)."""
    (base / "mods" / "SomeMod").mkdir(parents=True, exist_ok=True)
    (base / "overwrite").mkdir(parents=True, exist_ok=True)
    prof = base / "profiles" / profile
    prof.mkdir(parents=True, exist_ok=True)
    (prof / "Skyrim.ini").write_text("[General]\n", encoding="utf-8")
    (prof / "SkyrimPrefs.ini").write_text("[Display]\n", encoding="utf-8")
    (prof / "loadorder.txt").write_text("Skyrim.esm\n", encoding="utf-8")
    (prof / "plugins.txt").write_text("*Skyrim.esm\n", encoding="utf-8")
    return base


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def game_dir(tmp_path: Path) -> Path:
    """A stock (non-MO2) install."""
    return make_game(tmp_path)


@pytest.fixture
def portable_mo2(tmp_path: Path):
    """Nolvus / Wabbajack shape: a portable instance parked *beside* the game
    folder rather than under %LOCALAPPDATA%, with @ByteArray-wrapped values.

        <inst>/MO2/ModOrganizer.ini
        <inst>/STOCK GAME/          <- the game
        <inst>/MODS/{mods,profiles,overwrite}
    """
    inst = tmp_path / "Instances" / "Nolvus Awakening"
    game = make_game(inst, name="STOCK GAME", exe="SkyrimSE.exe")
    mods = make_mo2_tree(inst / "MODS", "Nolvus Awakening")
    root = game_root_win(game)
    base = root.rsplit("/", 1)[0] + "/MODS"
    write_mo2_ini(inst / "MO2" / "ModOrganizer.ini", root, "Nolvus Awakening",
                  base, byte_array=True)
    return game, inst, mods


@pytest.fixture
def global_mo2(tmp_path: Path):
    """A global instance under %LOCALAPPDATA%, with plain (non-@ByteArray)
    values -- the older format, which must keep working."""
    game = make_game(tmp_path, exe="SkyrimSE.exe")
    localapp = tmp_path / "fakelocal"
    mods = make_mo2_tree(tmp_path / "mo2base", "Default")
    root = game_root_win(game)
    base = root.rsplit("/", 1)[0] + "/mo2base"
    write_mo2_ini(localapp / "ModOrganizer" / "MyInstance" / "ModOrganizer.ini",
                  root, "Default", base, byte_array=False)
    return game, localapp, mods
