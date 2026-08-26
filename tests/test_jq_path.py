"""The jq path setup.sh bakes into the safety hooks must survive `sed`.

setup.sh substitutes JQ_PATH into every hook with
`sed -i "s|{{JQ_PATH}}|$JQ_PATH|g"`. GNU sed interprets backslash sequences in
replacement text -- `\\U` starts upper-casing, `\\t` becomes a tab, `\\L` starts
lower-casing -- so an un-normalized Windows path is silently mangled and every
safety hook ends up pointing at a jq that does not exist. Hooks then fail open.

This is the same defect fixed for LOCALAPPDATA and DOCUMENTS_DIR in v3.2.1
(`C:\\Users\\You\\AppData\\Local` came out as `C:SERSYOUAPPDATAocal`), in the one
variable that fix did not cover. v3.6's hook checker validates the paths inside
settings.json; it never looks at the jq path inside the hook scripts.
"""

from __future__ import annotations

from conftest import hook_jq_lines, run_setup

# Contains \U (Users), \t (testuser), \A, \L (Local), \M, \W, \L, \j -- several of
# which GNU sed treats as escapes in replacement text.
WINDOWS_JQ = r"C:\Users\testuser\AppData\Local\Microsoft\WinGet\Links\jq.exe"
EXPECTED = "C:/Users/testuser/AppData/Local/Microsoft/WinGet/Links/jq.exe"


def test_backslash_jq_path_reaches_hooks_intact(game_dir):
    result = run_setup(
        game_dir, env_overrides={"FAKE_WHICH_JQ": WINDOWS_JQ}, shim_path=True
    )
    assert result.returncode == 0, f"setup.sh failed:\n{result.stdout}\n{result.stderr}"

    lines = hook_jq_lines(game_dir)
    assert lines, "setup.sh configured no hooks at all"

    for hook, line in lines.items():
        assert "{{JQ_PATH}}" not in line, f"{hook}: placeholder was never substituted"
        assert line == f'JQ="{EXPECTED}"', (
            f"{hook}: jq path was corrupted by sed escape handling.\n"
            f"  supplied: {WINDOWS_JQ}\n"
            f"  expected: JQ=\"{EXPECTED}\"\n"
            f"  got:      {line}"
        )
