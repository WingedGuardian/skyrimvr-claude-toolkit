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
import sys
from pathlib import Path

import pytest

from harness_isolation import active as _harness_active

REPO = Path(__file__).resolve().parent.parent
# No test, and no hook a test runs (most copy os.environ), may launch the real session-start temp sweep or reach the
# real Recycle Bin (tools/skyguard/run.py, tools/housekeep.py). A test that means to exercise either sets it back.
os.environ["SKYGUARD_HOUSEKEEP_SWEEP"] = "off"
BIN = Path(__file__).resolve().parent / "bin"
BS = chr(92)  # backslash, spelled this way to keep escapes out of this file
# THE install path of the guard corpus and of every test that replays or extends its rows: tests/guard_corpus/
# game-root.txt is the one place it is defined. It is SYNTHETIC (no machine this suite runs on has it, which
# tests/test_no_real_install_path.py asserts), so no verdict can depend on what is on the host's disk. It keeps a space
# in a folder name and an 8.3-shaped parent, the shapes the delete rules parse. Never spell it as a literal in a test.
GAME_ROOT = (REPO / "tests" / "guard_corpus" / "game-root.txt").read_text(encoding="utf-8").strip()
GAME_PARENT = GAME_ROOT.rsplit("/", 1)[0]


def short_name(component: str) -> str:
    """The first 8.3 short name Windows would give a long folder name (`Synthetic Games` -> `SYNTHE~1`)."""
    return "".join(c for c in component if c.isalnum())[:6].upper() + "~1"


# The install's 8.3 spelling, drive kept: C:/SYNTHE~1/THEELD~1.
GAME_SHORT = GAME_ROOT[:3] + "/".join(short_name(c) for c in GAME_ROOT[3:].split("/"))


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


def harness_active() -> bool:
    """True when the contained launcher (tests/run_harness_tests.py) installed the barriers."""
    return _harness_active()


def pytest_configure(config):
    """TOOLKIT_REQUIRE_HARNESS=1 turns "the barriers are not installed" into an error.

    CI sets it. Without it a plain pytest would skip the barrier-dependent modules and
    still go green, which is a gate that cannot fail.
    """
    if os.environ.get("TOOLKIT_REQUIRE_HARNESS") == "1" and not harness_active():
        raise pytest.UsageError(
            "TOOLKIT_REQUIRE_HARNESS=1 but the package-manager barriers are not active; "
            "run through: python -B tests/run_harness_tests.py tests/")

# setup.sh no longer installs anything, but it still shells out to which/python/powershell
# probes. Every invocation is capped so a stuck probe fails the test instead of hanging the job.
SETUP_TIMEOUT = 240

IGNORE = shutil.ignore_patterns(
    ".git", "tests", "__pycache__", "node_modules", ".pytest_cache", ".venv",
    # CI tees the running suite's own logs into the checkout (ci.yml: collect.log, behavioral.log); a copy that
    # took them shipped the runner's home path into every verify-release payload (Linux CI run 38187755448).
    "collect.log", "behavioral.log",
)


# --------------------------------------------------------------------------
# running setup.sh
# --------------------------------------------------------------------------

def login_name() -> str:
    """The account this process runs as, read from the OS rather than the environment (the harness launcher
    scrubs USERNAME/USER and fakes HOME): GetUserNameW on Windows, the passwd entry elsewhere. "" when unreadable.
    windows-latest CI needs it: its checkout is D:/a/..., under no user folder, so nothing else names the user."""
    try:
        if os.name == "nt":
            return os.getlogin()
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except (OSError, KeyError, ImportError):
        return ""


def copy_repo(dest: Path) -> Path:
    """A pristine copy of the toolkit, minus the test suite itself."""
    shutil.copytree(REPO, dest, ignore=IGNORE)
    return dest


def scratch_dir(prefix: str) -> str:
    """A module-level temp dir that is removed when the process exits.

    Bare tempfile.mkdtemp at import leaked one directory per pytest process: 14,018 of them had built up
    in %TEMP% by 2026-10-18, because the mutation gate starts a pytest per mutation.
    """
    import atexit
    import tempfile
    root = tempfile.mkdtemp(prefix=prefix)
    atexit.register(shutil.rmtree, root, True)
    return root


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


def run_setup(cwd: Path, env_overrides: dict | None = None, shim_path: bool = False, args=()):
    """Run the real setup.sh against `cwd`. Returns a CompletedProcess.

    `shim_path` puts tests/bin first on PATH, which is how a test takes control
    of an external command setup.sh depends on (`which`, `powershell`) without
    adding a test-only seam to the shipped script. `args` go to setup.sh itself
    (`--agent codex`, `--lock`).
    """
    env = dict(os.environ)
    env.setdefault("USERNAME", "testuser")
    if shim_path:
        env["PATH"] = f"{BIN}{os.pathsep}{env['PATH']}"
    if env_overrides:
        env.update({k: str(v) for k, v in env_overrides.items()})
    # The Git for Windows bin/bash launcher prepends its own directories. Bind
    # fixture commands inside the launched shell, retaining any containment
    # tripwires ahead of them, instead of accidentally probing the host tools.
    start = ('testpath="$1"; shift; '
             'if command -v cygpath >/dev/null 2>&1; then testpath=$(cygpath -p -u "$testpath"); fi; '
             'export PATH="${HARNESS_TRIPWIRE_BIN:+$HARNESS_TRIPWIRE_BIN:}$testpath:$PATH"; '
             'script="$1"; shift; source "$script" "$@"')
    return subprocess.run(
        [BASH, "-c", start, "--", env["PATH"], "setup.sh", *args],
        cwd=str(cwd), env=env,
        stdin=subprocess.DEVNULL,
        # errors="replace": a path setup echoes may hold bytes the locale codec cannot decode (a cp437 probe);
        # without it the reader thread dies and the test sees stdout=None instead of the message it asserts on.
        capture_output=True, text=True, errors="replace", timeout=SETUP_TIMEOUT,
    )


# --------------------------------------------------------------------------
# reading what setup.sh wrote
# --------------------------------------------------------------------------

def claude_md(game: Path) -> str:
    return (game / "CLAUDE.md").read_text(encoding="utf-8")


def hook_python_lines(game: Path) -> dict[str, str]:
    """The `PY="..."` line from every hook setup.sh configures."""
    found: dict[str, str] = {}
    for hook in sorted((game / ".claude" / "hooks").glob("*.sh")):
        for line in hook.read_text(encoding="utf-8").splitlines():
            if line.startswith("PY="):
                found[hook.name] = line.strip()
                break
    return found


def install_hooks(root: Path, names=None, python: str | None = None, with_engine: bool = True) -> Path:
    """Put the shipped hook wrappers into `root/.claude/hooks`, configured the way setup.sh leaves them.

    `{{PYTHON_PATH}}` becomes this interpreter (forward slashes) unless `python` says otherwise, and
    `tools/skyguard` is copied beside them -- the wrappers are thin, so without the engine they can
    only report that they cannot decide. Returns `root`.
    """
    py = sys.executable.replace(BS, "/") if python is None else python
    hooks = root / ".claude" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    for f in (REPO / ".claude" / "hooks").glob("*.sh"):
        if names is not None and f.name not in names:
            continue
        text = f.read_text(encoding="utf-8").replace("{{PYTHON_PATH}}", py)
        (hooks / f.name).write_text(text, encoding="utf-8", newline="\n")
    if with_engine:
        shutil.copytree(REPO / "tools" / "skyguard", root / "tools" / "skyguard",
                        ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
        shutil.copytree(REPO / "tools/modding_harness", root / "tools/modding_harness",
                        ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    return root


#: The names the wrappers' fallback chain looks up on PATH (`py -3`, `python`, `python3`).
PYTHON_NAMES = ("python", "python3", "py")


def offers_python(directory: str) -> bool:
    d = Path(directory)
    return any((d / n).exists() or (d / (n + ".exe")).exists() for n in PYTHON_NAMES)


def _is_python_name(name: str) -> bool:
    low = name.lower()
    return low.startswith("python") or low in ("py", "py.exe")


def _mirror_without_python(directory: str, into: Path) -> None:
    """Link every entry of `directory` except the Pythons into `into` (symlink; a hard link or a copy where
    the platform refuses one)."""
    into.mkdir(parents=True, exist_ok=True)
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return
    for name in names:
        src = Path(directory) / name
        if _is_python_name(name) or not src.is_file() or (into / name).exists():
            continue
        try:
            os.symlink(src, into / name)
        except OSError:
            try:
                os.link(src, into / name)
            except OSError:
                shutil.copy2(src, into / name)


_MIRRORS: dict = {}


def python_free_path(path: str | None = None, *, mirror: bool | None = None, base: Path | None = None) -> str:
    """PATH with no python, py or python3 on it -- and EVERYTHING ELSE still on it.

    Dropping each directory that offers a python is enough on Windows, where the Pythons live in their own
    directories. On Linux the system python3 lives in /usr/bin beside cat, mkdir, tr and sleep: dropping
    /usr/bin made `INPUT=$(cat)` in every hook wrapper fail with "cat: command not found", so every hook read
    an EMPTY payload (GUARD INERT ... received no file path / receipt NO PAYLOAD) and mkdir could not make the
    heartbeat directory. The first native-Linux CI run (38178385999) failed 36 hook tests that way. So a
    dropped directory is replaced by a mirror of its non-Python entries (default: on every non-Windows
    platform). `mirror` and `base` are seams for tests/test_python_free_path.py."""
    path = os.environ.get("PATH", "") if path is None else path
    mirror = (os.name != "nt") if mirror is None else mirror
    keep = []
    for d in path.split(os.pathsep):
        if not d:
            continue
        if not offers_python(d):
            keep.append(d)
        elif mirror:
            key = (d, str(base))
            if key not in _MIRRORS:
                if base is None:
                    import atexit
                    import tempfile
                    root = Path(tempfile.mkdtemp(prefix="python-free-path-"))
                    atexit.register(shutil.rmtree, root, True)
                else:
                    root = base
                link_dir = root / str(len(_MIRRORS))
                _mirror_without_python(d, link_dir)
                _MIRRORS[key] = str(link_dir)
            keep.append(_MIRRORS[key])
    return os.pathsep.join(keep)


def no_python_env(**extra) -> dict:
    """An environment in which the hook wrappers' interpreter fallback chain finds NOTHING: PATH with no
    python on it (python_free_path), no LOCALAPPDATA / PROGRAMFILES to search, and the fixed system locations
    (/usr/bin/python3, /usr/local/bin/python3 -- present on every Linux runner) switched off with the
    wrappers' SKYGUARD_NO_SYSTEM_PYTHON knob. For tests that mean "the interpreter is unusable" (Plan 4
    Task 5: otherwise the chain would, correctly, rescue the call)."""
    return dict(os.environ, PATH=python_free_path(), LOCALAPPDATA="", PROGRAMFILES="",
                SKYGUARD_NO_SYSTEM_PYTHON="1", **extra)


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
