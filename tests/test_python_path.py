"""The Python path setup.sh bakes into the safety hooks must survive `sed`.

setup.sh substitutes PYTHON_PATH into every hook with
`sed -i "s|{{PYTHON_PATH}}|$PYTHON_PATH|g"`. GNU sed interprets backslash
sequences in replacement text -- `\\U` starts upper-casing, `\\t` becomes a tab, `\\L` starts
lower-casing -- so an un-normalized Windows path is silently mangled and every
safety hook ends up pointing at an interpreter that does not exist. The hooks then refuse every
command (they fail closed), which is safe but unusable.

`py -3 -c "import sys; print(sys.executable)"` prints exactly such a path
(`C:\\Users\\You\\AppData\\Local\\Programs\\Python\\Python312\\python.exe`), with a CR on the end under
some shells. It is the same defect fixed for LOCALAPPDATA and DOCUMENTS_DIR in v3.2.1 and for the jq
path afterwards, in the variable that replaced it. v3.6's hook checker validates the paths inside
settings.json; it never looks at the interpreter path inside the hook scripts.
"""

from __future__ import annotations

from conftest import hook_python_lines, run_setup

# Contains \U (Users), \t (testuser), \A, \L (Local), \P, \W, \j -- several of which GNU sed treats
# as escapes in replacement text.
WINDOWS_PYTHON = r"C:\Users\testuser\AppData\Local\Programs\Python\Python312\python.exe"
EXPECTED = "C:/Users/testuser/AppData/Local/Programs/Python/Python312/python.exe"


def test_backslash_python_path_reaches_hooks_intact(game_dir):
    result = run_setup(
        game_dir, env_overrides={"FAKE_PYTHON_EXE": WINDOWS_PYTHON}, shim_path=True
    )
    assert result.returncode == 0, f"setup.sh failed:\n{result.stdout}\n{result.stderr}"

    lines = hook_python_lines(game_dir)
    assert len(lines) == 5, f"setup.sh configured {sorted(lines)}, expected all five hooks"

    for hook, line in lines.items():
        assert "{{PYTHON_PATH}}" not in line, f"{hook}: placeholder was never substituted"
        assert line == f"PY='{EXPECTED}'", (
            f"{hook}: the Python path was corrupted by sed escape handling.\n"
            f"  supplied: {WINDOWS_PYTHON}\n"
            f"  expected: PY='{EXPECTED}'\n"
            f"  got:      {line}"
        )


def test_the_real_interpreter_is_what_gets_written(game_dir):
    """No shim: whatever Python setup.sh detects, the hooks must hold an absolute, runnable path --
    not a bare command name the hook environment's PATH might not resolve."""
    import os
    import subprocess

    result = run_setup(game_dir)
    assert result.returncode == 0, f"setup.sh failed:\n{result.stdout}\n{result.stderr}"
    lines = hook_python_lines(game_dir)
    assert len(lines) == 5
    for hook, line in lines.items():
        path = line[len("PY='"):-1]
        assert "{{" not in path and "\\" not in path, f"{hook}: {line}"
        assert os.path.isabs(path) or path.startswith("/"), f"{hook}: not absolute: {line}"
        probe = subprocess.run([path, "-c", "print(1)"], capture_output=True, text=True, timeout=60)
        assert probe.stdout.strip() == "1", f"{hook}: {path} is not a runnable interpreter"


def test_a_path_containing_an_ampersand_is_not_mangled_by_sed(game_dir):
    """`&` in sed's replacement means "the whole match" -- `C:/R&D/python.exe` would come out with the
    placeholder spliced into it. A folder name may legally contain `&`."""
    path = "C:/R&D/Python & Co/python.exe"
    result = run_setup(game_dir, env_overrides={"FAKE_PYTHON_EXE": path}, shim_path=True)
    assert result.returncode == 0, result.stderr
    lines = hook_python_lines(game_dir)
    assert len(lines) == 5
    for hook, line in lines.items():
        assert line == f"PY='{path}'", f"{hook}: {line}"


def test_rerunning_setup_repairs_a_stale_interpreter_path(game_dir):
    """After a Python upgrade the stored path points at nothing and the hooks refuse every command.
    "Already configured" must not hide that: re-running setup has to put the current interpreter back."""
    import json
    import subprocess

    from conftest import BASH, no_python_env

    assert run_setup(game_dir).returncode == 0
    good = hook_python_lines(game_dir)["protect-bash.sh"]
    assert good.startswith("PY='") and "{{" not in good
    hooks = game_dir / ".claude" / "hooks"
    for h in hooks.glob("*.sh"):
        text = h.read_text(encoding="utf-8").replace(good, "PY='C:/Python312-gone/python.exe'")
        h.write_text(text, encoding="utf-8", newline="\n")

    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    stale = subprocess.run([BASH, (hooks / "protect-bash.sh").as_posix()], input=payload,
                           capture_output=True, text=True, timeout=60, env=no_python_env())
    assert "GUARD INERT" in stale.stdout, "the stale path should make the hook refuse"

    result = run_setup(game_dir)
    assert result.returncode == 0
    assert "Updated interpreter" in result.stdout, result.stdout[-1500:]
    assert hook_python_lines(game_dir)["protect-bash.sh"] == good
    fixed = subprocess.run([BASH, (hooks / "protect-bash.sh").as_posix()], input=payload,
                           capture_output=True, text=True, timeout=60)
    assert fixed.stdout.strip() in ("", "{}"), fixed.stdout
    # and a third run, with nothing changed, says so
    assert "Updated interpreter" not in run_setup(game_dir).stdout


def test_setup_warns_loudly_when_no_python_exists(game_dir, tmp_path):
    """No Python 3 anywhere on PATH: setup must say the hooks will refuse, and must not pretend it
    configured them. It must still finish (the rest of setup is independent of Python)."""
    import os
    import shutil

    bash_dir = os.path.dirname(shutil.which("bash") or "")
    # A PATH with the basics setup.sh needs and no interpreter. Python's own directory is not on it.
    keep = [d for d in os.environ["PATH"].split(os.pathsep)
            if d and not any(os.path.exists(os.path.join(d, n))
                             for n in ("python", "python.exe", "python3", "python3.exe", "py", "py.exe"))]
    result = run_setup(game_dir, env_overrides={"PATH": os.pathsep.join(keep + [bash_dir])})
    out = result.stdout
    assert "will REFUSE every command" in out, out[-1500:]
    assert "NOT CONFIGURED" in out
    assert all("{{PYTHON_PATH}}" in (game_dir / ".claude" / "hooks" / h).read_text(encoding="utf-8")
               for h in ("protect-bash.sh", "protect-files.sh"))


def _eval_py_line(line: str) -> str:
    """What bash makes of the written `PY=...` line: the value the wrapper will actually run."""
    import subprocess

    from conftest import BASH

    r = subprocess.run([BASH, "-c", line + chr(10) + 'printf %s "$PY"'], capture_output=True, text=True, timeout=60)
    return r.stdout


def test_a_path_with_shell_metacharacters_reaches_the_hook_verbatim(game_dir):
    """A double-quoted PY="..." expands `$` and backticks at every hook call; single quotes do not."""
    path = "C:/Users/a$HOME`id`b/python.exe"
    result = run_setup(game_dir, env_overrides={"FAKE_PYTHON_EXE": path}, shim_path=True)
    assert result.returncode == 0, result.stderr
    lines = hook_python_lines(game_dir)
    assert len(lines) == 5
    for hook, line in lines.items():
        assert line == f"PY='{path}'", f"{hook}: {line}"
        assert _eval_py_line(line) == path, f"{hook}: bash expanded the path"


def test_a_path_with_a_single_quote_survives_the_quoting(game_dir):
    path = "C:/Users/O'Brien/python.exe"
    result = run_setup(game_dir, env_overrides={"FAKE_PYTHON_EXE": path}, shim_path=True)
    assert result.returncode == 0, result.stderr
    lines = hook_python_lines(game_dir)
    assert len(lines) == 5
    for hook, line in lines.items():
        assert _eval_py_line(line) == path, f"{hook}: {line}"
    # re-running with the same interpreter changes nothing (the comparison knows the quoted form)
    again = run_setup(game_dir, env_overrides={"FAKE_PYTHON_EXE": path}, shim_path=True)
    assert "Updated interpreter" not in again.stdout and "Already configured" in again.stdout


def test_setup_refuses_a_python_older_than_3_8(game_dir):
    """The engine's floor is 3.8. A 3.6 used to pass setup's "is it Python 3?" probe and then every
    hook denied "engine failed" -- a full outage that nothing explained."""
    result = run_setup(game_dir, env_overrides={"FAKE_PYTHON_EXE": "C:/Py36/python.exe", "FAKE_PYTHON_OLD": "1"},
                       shim_path=True)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "3.8" in out and "too old" in out, out[-1500:]
    assert "NOT CONFIGURED" in out
    assert all("{{PYTHON_PATH}}" in (game_dir / ".claude" / "hooks" / h).read_text(encoding="utf-8")
               for h in ("protect-bash.sh", "protect-files.sh"))
