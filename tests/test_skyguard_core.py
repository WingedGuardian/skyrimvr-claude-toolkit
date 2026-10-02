"""skyguard core: grep semantics, path roots, audit log format, Python floor."""
import ast
import sys

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, grep, paths, verdict  # noqa: E402

BS = chr(92)
SEP = r"[/\\]+"


def test_grep_is_line_wise():
    # [[:space:]] cannot cross a newline in grep; Python's \s can. The port evaluates per line.
    assert not grep.grep(r"rm\s+/x", "rm\n/x")
    assert grep.grep(r"rm\s+/x", "echo a\nrm /x")


def test_grep_case_flag():
    assert grep.grep("REMOVE-ITEM", "remove-item x")
    assert not grep.grep(r"^\s*ls\s", "LS x", icase=False)


def test_path_regex_windows_dialects_all_match():
    rx = paths.path_to_regex("C:/GOG Games/Skyrim VR")
    for s in ["C:/GOG Games/Skyrim VR", "C:" + BS + "GOG Games" + BS + "Skyrim VR", "/c/GOG Games/Skyrim VR",
              "C://GOG Games//Skyrim VR", "C:" + BS * 2 + "GOG Games" + BS * 2 + "Skyrim VR", "c:/gog games/skyrim vr"]:
        assert grep.grep(rx, s), s


def test_path_regex_escapes_metacharacters():
    rx = paths.path_to_regex("C:/Games/Skyrim (VR) [x]$")
    assert grep.grep(rx, "rm -rf C:/Games/Skyrim (VR) [x]$/Data")
    assert not grep.grep(rx, "C:/Games/Skyrim VR x/Data")


def test_path_regex_rejects_relative_and_bare_drive():
    assert paths.path_to_regex("Data/x") is None
    assert paths.path_to_regex("C:/") is None
    assert paths.path_to_regex("/home/u") == SEP + "home" + SEP + "u"


def test_env_file_overrides_environment_and_parses_both_quote_styles(tmp_path):
    f = tmp_path / "skyrim-paths.env"
    f.write_text("SKYRIM_GAME_ROOT='C:/A $B'\nSKYRIM_CONFIG_DIR=\"D:/P\"\n# comment\nbad line\n", encoding="utf-8")
    env = paths.read_env(f, {"SKYRIM_GAME_ROOT": "X", "SKYRIM_LOADORDER_DIR": "L"})
    assert env["SKYRIM_GAME_ROOT"] == "C:/A $B" and env["SKYRIM_CONFIG_DIR"] == "D:/P"
    assert env["SKYRIM_LOADORDER_DIR"] == "L"


def test_roots_dedupe_identical_spellings_only_like_bash():
    # bash's add_root dedupes by exact ERE text: the same spelling twice is one root, but /c/ and C:/
    # render different text (drive-letter case), so both are kept -- harmless, and what bash does.
    one = paths.path_to_regex("C:/G/S")
    assert paths.roots("C:/G/S", "C:/G/S", {}).game == one
    assert paths.roots("/c/G/S", "C:/G/S", {}).game == paths.path_to_regex("/c/G/S") + "|" + one
    assert paths.roots("", "", {}).config == r"Documents[/\\]+My Games[/\\]+Skyrim"


def test_audit_line_format(tmp_path):
    a = audit.Audit(str(tmp_path))
    a.log("protect-bash", "DENY", "why")
    line = (tmp_path / ".claude" / "backups" / "AUDIT_LOG.txt").read_text(encoding="utf-8")
    assert line.startswith("[")
    stamp, rest = line[1:].split("] ", 1)
    assert len(stamp) == 15 and stamp[8] == "_" and rest == "protect-bash DENY: why\n"


def test_advice_joins_with_pipes():
    adv = verdict.Advice()
    adv.add("a")
    adv.add("b")
    assert adv.joined() == "a | b"


def test_engine_parses_on_python_3_8():
    for f in sorted((REPO / "tools" / "skyguard").rglob("*.py")):
        ast.parse(f.read_text(encoding="utf-8"), filename=str(f), feature_version=(3, 8))
