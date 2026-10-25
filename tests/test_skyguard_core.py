"""skyguard core: grep semantics, path roots, audit log format, Python floor."""
import ast
import sys

import pytest

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


def test_grep_is_ascii_only():
    # GNU grep's [[:space:]], \b and case folding are ASCII; Python's Unicode defaults are not.
    assert not grep.grep(r"rm\s+x", "rm x")
    assert not grep.grep(r"rm\s+x", "rmx") and not grep.grep(r"rm\s+x", "rm　x")
    assert grep.grep(r"rm\s+x", "rm	x")
    assert not grep.grep("kelvin", "Kelvin")        # Kelvin sign does not fold to k
    assert not grep.grep("sed", "ſed")              # long s does not fold to s
    assert grep.grep("KELVIN", "kelvin")


def test_path_regex_windows_dialects_all_match():
    rx = paths.path_to_regex("C:/Synthetic Games/Skyrim VR")
    for s in ["C:/Synthetic Games/Skyrim VR", "C:" + BS + "Synthetic Games" + BS + "Skyrim VR", "/c/Synthetic Games/Skyrim VR",
              "C://Synthetic Games//Skyrim VR", "C:" + BS * 2 + "Synthetic Games" + BS * 2 + "Skyrim VR", "c:/synthetic games/skyrim vr"]:
        assert grep.grep(rx, s), s


def test_path_regex_escapes_metacharacters():
    rx = paths.path_to_regex("C:/Games/Skyrim (VR) [x]$")
    assert grep.grep(rx, "rm -rf C:/Games/Skyrim (VR) [x]$/Data")
    assert not grep.grep(rx, "C:/Games/Skyrim VR x/Data")


def test_path_regex_rejects_relative_and_bare_drive():
    assert paths.path_to_regex("Data/x") is None
    assert paths.path_to_regex("C:/") is None
    assert paths.path_to_regex("/home/u") == paths.LEAD + "home" + SEP + "u"


def test_env_and_file_are_a_union_and_both_quote_styles_parse(tmp_path):
    """R3: a guard never loses a root because another source shadows it. The file used to override the
    environment (bash's `. env`), so an exported SKYRIM_GAME_ROOT was dropped whenever the file also set one."""
    f = tmp_path / "skyrim-paths.env"
    f.write_text("SKYRIM_GAME_ROOT='C:/A $B'\nSKYRIM_CONFIG_DIR=\"D:/P\"\n# comment\nbad line\n", encoding="utf-8")
    cfg = paths.read_config(f, {"SKYRIM_GAME_ROOT": "E:/Env/Skyrim", "SKYRIM_LOADORDER_DIR": "L"})
    assert cfg.values("SKYRIM_GAME_ROOT") == ["E:/Env/Skyrim", "C:/A $B"]
    assert cfg.values("SKYRIM_CONFIG_DIR") == ["D:/P"] and cfg.malformed == ()
    r = paths.roots("", "", cfg)
    assert r.install_dirs == ("e:/Env/Skyrim", "c:/A $B")
    assert grep.grep(r.game, "rm -rf E:/Env/Skyrim/Data") and grep.grep(r.game, "rm -rf C:/A $B/Data")
    # ...while TOOL lookup keeps the Codex precedence: the environment wins.
    assert cfg.first("SKYRIM_GAME_ROOT") == "E:/Env/Skyrim" and cfg.first("SKYRIM_LOADORDER_DIR") == "L"


def test_mo2_keys_become_mo2_dirs(tmp_path):
    f = tmp_path / "skyrim-paths.env"
    f.write_text("SKYRIM_MO2_MODS='D:/MO2/mods'\nSKYRIM_MO2_OVERWRITE='D:/MO2/overwrite'\n", encoding="utf-8")
    r = paths.roots("C:/G", "", paths.read_config(f, {"SKYRIM_MO2_MODS": "E:/Other/mods"}))
    assert r.mo2_dirs == ("e:/Other/mods", "d:/MO2/mods", "d:/MO2/overwrite")


def test_empty_values_declare_nothing_and_are_not_malformed(tmp_path):
    """setup.sh writes SKYRIM_MO2_MODS='' on a non-MO2 install."""
    f = tmp_path / "skyrim-paths.env"
    f.write_text("SKYRIM_MO2_MODS=''\nSKYRIM_MO2_OVERWRITE=\n", encoding="utf-8")
    cfg = paths.read_config(f, {})
    assert cfg.malformed == () and cfg.values("SKYRIM_MO2_MODS") == []


SQ_FORMS = [
    # (file text after `KEY=`, the value bash assigns) -- setup.sh's sq() output and the hand-written spellings
    ("'C:/Synthetic Games/Skyrim VR'", "C:/Synthetic Games/Skyrim VR"),
    ("'C:/Bob'\\''s Games/Skyrim'", "C:/Bob's Games/Skyrim"),
    ("'C:/a'\\'''\\''b'", "C:/a''b"),
    ("'C:/My$Stuff/Sky`rim'", "C:/My$Stuff/Sky`rim"),
    ("\"C:/Synthetic Games/Skyrim VR\"", "C:/Synthetic Games/Skyrim VR"),
    ("\"C:\\Users\\me\\Skyrim\"", "C:\\Users\\me\\Skyrim"),
    ("\"C:/a\\\"b\"", "C:/a\"b"),
    ("C:/plain/path", "C:/plain/path"),
    ("C:/a\\ b", "C:/a b"),
    ("'C:/x'  # trailing comment", "C:/x"),
    ("/c/msys/path", "/c/msys/path"),
    ("'C:/x'#c", "C:/x#c"),                       # fix round 1: `#` mid-word is literal, not a comment
    ("C:/a#b", "C:/a#b"),
]


@pytest.mark.parametrize("line,want", [
    ("declare -x SKYRIM_GAME_ROOT='C:/x'", "C:/x"),
    ("declare SKYRIM_GAME_ROOT='C:/x'", "C:/x"),
    ("typeset -x SKYRIM_GAME_ROOT='C:/x'", "C:/x"),
    ("readonly SKYRIM_GAME_ROOT='C:/x'", "C:/x"),
    ("declare -rx SKYRIM_GAME_ROOT='C:/x'", "C:/x"),
    ("export\tSKYRIM_GAME_ROOT='C:/x'", "C:/x"),
])
def test_declare_forms_are_read_like_bash(line, want, tmp_path):
    """Fix round 1: `declare -x KEY=` used to be ignored silently. Oracle: bash sourcing the same bytes."""
    f = tmp_path / "skyrim-paths.env"
    f.write_text(line + "\n", encoding="utf-8", newline="\n")
    cfg = paths.read_config(f, {})
    assert cfg.malformed == () and cfg.values("SKYRIM_GAME_ROOT") == [want]
    from conftest import BASH
    import subprocess
    got = subprocess.run([BASH, "-c", '. "$1"; printf %s "$SKYRIM_GAME_ROOT"', "x", f.as_posix()],
                         capture_output=True, timeout=60)
    assert got.stdout.decode("utf-8") == want


@pytest.mark.parametrize("line", ["declare -i SKYRIM_GAME_ROOT='C:/x'", "declare -l SKYRIM_CONFIG_DIR='C:/X'",
                                  "local SKYRIM_GAME_ROOT='C:/x'"])
def test_a_declare_that_changes_the_value_is_malformed(line, tmp_path):
    f = tmp_path / "skyrim-paths.env"
    f.write_text(line + "\n", encoding="utf-8")
    assert len(paths.read_config(f, {}).malformed) == 1


def test_a_bom_does_not_hide_the_first_key(tmp_path):
    """Fix round 1: a UTF-8 BOM glued itself to the first key, which was then dropped silently -- even a
    MALFORMED protection key, so S-INERT-CONFIG never fired."""
    f = tmp_path / "skyrim-paths.env"
    f.write_bytes(b"\xef\xbb\xbfSKYRIM_GAME_ROOT='C:/G'\n")
    assert paths.read_config(f, {}).values("SKYRIM_GAME_ROOT") == ["C:/G"]
    f.write_bytes(b"\xef\xbb\xbfSKYRIM_GAME_ROOT=relative/x\n")
    assert paths.read_config(f, {}).malformed == (("skyrim-paths.env", "SKYRIM_GAME_ROOT"),)


def test_a_lone_cr_does_not_split_a_line(tmp_path):
    """bash does not split on CR, so `KEY='C:/x'<CR>OTHER=...` is one line and KEY is not assigned."""
    f = tmp_path / "skyrim-paths.env"
    f.write_bytes(b"SKYRIM_GAME_ROOT='C:/x'\rSKYRIM_CONFIG_DIR='C:/y'\r")
    assert paths.read_config(f, {}).malformed == (("skyrim-paths.env", "SKYRIM_GAME_ROOT"),)


def test_a_malformed_key_is_reported_once(tmp_path):
    f = tmp_path / "skyrim-paths.env"
    f.write_text("SKYRIM_GAME_ROOT\nSKYRIM_GAME_ROOT\n", encoding="utf-8")
    assert paths.read_config(f, {}).malformed == (("skyrim-paths.env", "SKYRIM_GAME_ROOT"),)


@pytest.mark.parametrize("raw,want", SQ_FORMS)
def test_value_grammar_reads_what_bash_reads(raw, want, tmp_path):
    assert paths.parse_value(raw) == want
    f = tmp_path / "skyrim-paths.env"
    f.write_text("SKYRIM_GAME_ROOT=" + raw + "\n", encoding="utf-8", newline="\n")
    cfg = paths.read_config(f, {})
    assert cfg.malformed == () and cfg.values("SKYRIM_GAME_ROOT") == [want]
    # The independent oracle: bash itself, sourcing the same bytes.
    from conftest import BASH
    import subprocess
    got = subprocess.run([BASH, "-c", '. "$1"; printf %s "$SKYRIM_GAME_ROOT"', "x", f.as_posix()],
                         capture_output=True, timeout=60)
    assert got.stdout.decode("utf-8") == want


@pytest.mark.parametrize("line,key", [
    ('SKYRIM_GAME_ROOT="C:/GOG $X/Skyrim"', "SKYRIM_GAME_ROOT"),       # bash expands $X: the guard cannot know it
    ("SKYRIM_GAME_ROOT=\"C:/`x`/Skyrim\"", "SKYRIM_GAME_ROOT"),
    ("SKYRIM_CONFIG_DIR=C:/My Games/Skyrim", "SKYRIM_CONFIG_DIR"),     # unquoted space: bash runs `Games/Skyrim`
    ("SKYRIM_MO2_MODS='D:/MO2/mods", "SKYRIM_MO2_MODS"),               # unterminated quote
    ("SKYRIM_MO2_OVERWRITE=$HOME/ow", "SKYRIM_MO2_OVERWRITE"),
    ("SKYRIM_GAME_ROOT='relative/dir'", "SKYRIM_GAME_ROOT"),           # a relative root protects nothing
    ("SKYRIM_GAME_ROOT=C:\\GOG\\Skyrim", "SKYRIM_GAME_ROOT"),           # bash reads C:GOGSkyrim (drive-relative)
    ("SKYRIM_GAME_ROOT ='C:/x'", "SKYRIM_GAME_ROOT"),                  # not an assignment in bash
    ("SKYRIM_CONFIG_DIR", "SKYRIM_CONFIG_DIR"),
    ("SKYRIM_GAME_ROOT=~/Skyrim", "SKYRIM_GAME_ROOT"),
])
def test_a_malformed_protection_value_is_reported_by_key_not_skipped(line, key, tmp_path):
    f = tmp_path / "skyrim-paths.env"
    f.write_text("SKYRIM_LOADORDER_DIR='C:/L'\n" + line + "\n", encoding="utf-8")
    cfg = paths.read_config(f, {})
    assert cfg.malformed == (("skyrim-paths.env", key),)
    assert cfg.values(key) == []
    assert paths.roots("C:/G", "", cfg).malformed == (("skyrim-paths.env", key),)


def test_a_malformed_value_denies_naming_the_key(tmp_path):
    from skyguard import shell_rules
    f = tmp_path / "skyrim-paths.env"
    f.write_text('SKYRIM_GAME_ROOT="C:/GOG $X/Skyrim"\n', encoding="utf-8")
    r = paths.roots("C:/G", "", paths.read_config(f, {}))
    a = audit.Audit(str(tmp_path))
    v = shell_rules.decide("ls", r, a)
    assert v.decision == "deny"
    assert v.reason == ("GUARD INERT: skyrim-paths.env has an unreadable value for SKYRIM_GAME_ROOT, so a "
                        "protected folder is unknown.\nRe-run setup.sh to rewrite it.")
    assert ("S-INERT-CONFIG", "deny") in a.fired


def test_a_malformed_non_protection_key_is_not_a_guard_failure(tmp_path):
    f = tmp_path / "skyrim-paths.env"
    f.write_text("SKYRIM_LOADORDER_DIR=\"C:/$X\"\nOTHER=a b c\n", encoding="utf-8")
    assert paths.read_config(f, {}).malformed == ()


def test_an_absent_file_declares_nothing_and_an_undecodable_one_raises(tmp_path):
    assert paths.read_config(tmp_path / "none.env", {"SKYRIM_GAME_ROOT": "C:/G"}).values("SKYRIM_GAME_ROOT") == ["C:/G"]
    f = tmp_path / "skyrim-paths.env"
    f.write_bytes(b"SKYRIM_GAME_ROOT='\xff\xfe'\n")
    with pytest.raises(ValueError):
        paths.read_config(f, {})


def test_crlf_file_parses(tmp_path):
    f = tmp_path / "skyrim-paths.env"
    f.write_bytes(b"SKYRIM_GAME_ROOT='C:/G'\r\nSKYRIM_MO2_MODS='D:/m'\r\n")
    cfg = paths.read_config(f, {})
    assert cfg.malformed == () and cfg.values("SKYRIM_MO2_MODS") == ["D:/m"]


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


def test_grep_drops_trailing_cr_per_line():
    # MEASURED on Windows Git Bash (the reference): `printf 'plugins.txt\r\n' | grep -qiE 'txt$'` and the
    # two- and three-CR forms all exit 0; a CR followed by more text does not. Linux GNU grep disagrees.
    assert grep.grep(r"txt$", "plugins.txt\r")
    assert grep.grep(r"txt$", "plugins.txt\r\r")
    assert grep.grep(r"txt$", "a\nplugins.txt\r\r\r\nb")
    assert not grep.grep(r"txt$", "plugins.txt\rx")
    assert not grep.grep(r"txt$", "plugins.txt\rx\ny")
