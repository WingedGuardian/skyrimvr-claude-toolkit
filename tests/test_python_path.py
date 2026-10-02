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
        assert line == f'PY="{EXPECTED}"', (
            f"{hook}: the Python path was corrupted by sed escape handling.\n"
            f"  supplied: {WINDOWS_PYTHON}\n"
            f"  expected: PY=\"{EXPECTED}\"\n"
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
        path = line[len('PY="'):-1]
        assert "{{" not in path and "\\" not in path, f"{hook}: {line}"
        assert os.path.isabs(path) or path.startswith("/"), f"{hook}: not absolute: {line}"
        probe = subprocess.run([path, "-c", "print(1)"], capture_output=True, text=True, timeout=60)
        assert probe.stdout.strip() == "1", f"{hook}: {path} is not a runnable interpreter"


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
