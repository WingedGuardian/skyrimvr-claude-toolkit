"""One apply_patch grammar for every caller. Anything it does not know is refused, never skipped."""
import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard.patch_paths import PatchParseError, parse_patch, shell_patch  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "payloads" / "codex"


def _captured(verb):
    for f in sorted(FIX.glob("*apply_patch*.json")):     # the 0.159.3 captures (notes/a.txt); not rglob
        cmd = json.loads(f.read_text(encoding="utf-8"))["tool_input"]["command"]
        if verb in cmd:
            return cmd
    pytest.fail("no captured fixture with " + verb)


@pytest.mark.parametrize("verb,op", [("*** Add File:", "add"), ("*** Update File:", "update"),
                                     ("*** Delete File:", "delete")])
def test_captured_shapes_parse(verb, op):
    assert parse_patch(_captured(verb)) == [(op, "notes/a.txt")]


def test_move_relabels_the_source_and_adds_the_destination():
    p = "*** Begin Patch\n*** Update File: a.txt\n*** Move to: b.esp\n@@\n-x\n+y\n*** End Patch"
    assert parse_patch(p) == [("move_from", "a.txt"), ("move_to", "b.esp")]


def test_multi_file_patch_lists_every_file():
    p = ("*** Begin Patch\n*** Add File: Data/x.esp\n+a\n*** Update File: Data/y.ini\n@@\n-a\n+b\n"
         "*** Delete File: Data/z.bsa\n*** End Patch")
    assert [o for o, _ in parse_patch(p)] == ["add", "update", "delete"]


def test_a_header_inside_a_hunk_is_content():
    p = "*** Begin Patch\n*** Add File: a.txt\n+*** Delete File: Data/Skyrim.esm\n*** End Patch"
    assert parse_patch(p) == [("add", "a.txt")]


def test_crlf_and_end_of_file_marker():
    p = "*** Begin Patch\r\n*** Update File: a.txt\r\n@@\r\n-a\r\n+b\r\n*** End of File\r\n*** End Patch\r\n"
    assert parse_patch(p) == [("update", "a.txt")]


@pytest.mark.parametrize("bad", [
    "*** Add File: a.txt\n+x\n*** End Patch",                          # no Begin
    "*** Begin Patch\n*** Add File: a.txt\n+x",                        # no End
    "*** Begin Patch\n*** Frobnicate File: a.txt\n*** End Patch",      # unknown header
    "*** Begin Patch\n*** Add File:   \n*** End Patch",                # empty path
    "*** Begin Patch\n*** Add File: a\n*** Move to: b\n*** End Patch",  # Move after Add
    "*** Begin Patch\n*** Update File: a\n@@\n-x\n+y\n*** Move to: b\n*** End Patch",  # Move not directly after Update
    "*** Begin Patch\n*** End Patch",                                  # touches nothing
])
def test_unknown_grammar_is_refused(bad):
    with pytest.raises(PatchParseError):
        parse_patch(bad)


def test_shell_heredoc_form():
    cmd = "apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: Data/x.esp\n+a\n*** End Patch\nPATCH"
    body, cd = shell_patch(cmd)
    assert parse_patch(body) == [("add", "Data/x.esp")] and cd is None


def test_shell_cd_prefix_is_returned():
    cmd = 'cd "Data/Scripts" && apply_patch <<EOF\n*** Begin Patch\n*** Delete File: a.psc\n*** End Patch\nEOF'
    body, cd = shell_patch(cmd)
    assert cd == "Data/Scripts" and parse_patch(body) == [("delete", "a.psc")]


def test_shell_quoted_argument_form():
    cmd = "apply_patch '*** Begin Patch\n*** Add File: x.esp\n+a\n*** End Patch'"
    assert parse_patch(shell_patch(cmd)[0]) == [("add", "x.esp")]


def test_an_ordinary_command_is_not_a_patch():
    assert shell_patch("Get-Date") is None
    assert shell_patch("rg -n apply_patch tools/") is None


def test_a_patch_it_cannot_attribute_is_refused():
    with pytest.raises(PatchParseError):
        shell_patch("Get-Date; apply_patch < p.txt")


# ---- fix round 1: cases measured against Codex 0.160.0's real apply_patch (plan errata: the brief's
# column-0-only grammar let real file ops through). Codex TRIMS header lines.

def _p(*body):
    return "\n".join(("*** Begin Patch",) + body + ("*** End Patch",))


@pytest.mark.parametrize("patch,expected", [
    (_p("*** Add File: a.txt", "+a", " *** Delete File: victim.esp"),
     [("add", "a.txt"), ("delete", "victim.esp")]),
    (_p("*** Add File: a.txt", "+a", "\t*** Add File: evil.esp", "+x"),
     [("add", "a.txt"), ("add", "evil.esp")]),
    (_p("  *** Update File: victim.esp", "@@", "-v", "+X"), [("update", "victim.esp")]),
    (_p("*** Update File: victim.esp", "  *** Move to: moved.esp", "@@", "-v", "+X"),
     [("move_from", "victim.esp"), ("move_to", "moved.esp")]),
    (_p("*** Update File: v.esp", "@@", "-v", "+X", " *** Delete File: other.esp"),
     [("update", "v.esp"), ("delete", "other.esp")]),     # over-reports a context line: safe direction
    (_p("*** Add File:evil.esp", "+a"), [("add", "evil.esp")]),
    (_p("\u00a0*** Add File: Data/x.esp", "+a"), [("add", "Data/x.esp")]),
])
def test_indented_headers_are_headers_like_codex_reads_them(patch, expected):
    assert parse_patch(patch) == expected


@pytest.mark.parametrize("bad", [
    _p("*** Add File: a.txt", "+a", "***Add File: Data/x.esp", "+b"),      # no space after ***
    _p("*** add file: Data/x.esp", "+a"),                                  # lowercase
    _p("*** Add File: a.txt", "+a", "*** End Patch", "*** Add File: Data/x.esp", "+b"),   # End mid-patch
    _p("*** Begin Patch", "*** Add File: a.txt", "+a"),                    # second Begin
    _p("*** Update File: a", "@@", "-a", "+b", "*** End of File", "  *** Frob: x"),
])
def test_any_other_star_line_is_refused(bad):
    with pytest.raises(PatchParseError):
        parse_patch(bad)


def test_plus_and_minus_lines_stay_content():
    p = _p("*** Update File: a.txt", "@@", "-*** Delete File: x.esp", "+*** Add File: y.esp")
    assert parse_patch(p) == [("update", "a.txt")]


CAP = REPO / "tests" / "fixtures" / "payloads" / "codex" / "0.160.0"


def _cmd(name):
    return json.loads((CAP / name).read_text(encoding="utf-8"))["tool_input"]["command"]


def test_0160_captured_move_and_multi_file_and_shell_heredoc():
    assert parse_patch(_cmd("codex-PreToolUse-apply_patch-3.json")) == [
        ("move_from", "shapes/a.txt"), ("move_to", "shapes/b.txt")]
    assert parse_patch(_cmd("codex-PreToolUse-apply_patch-4.json")) == [
        ("add", "shapes/c.txt"), ("add", "shapes/d.txt")]
    body, cd = shell_patch(_cmd("codex-PreToolUse-Bash-2.json"))
    assert parse_patch(body) == [("add", "viashell.txt")] and cd is None


_ONE = "<<A\n" + _p("*** Add File: a.txt", "+a") + "\nA"
_TWO = _ONE + "\napply_patch <<B\n" + _p("*** Add File: Data/x.esp", "+b") + "\nB\n"


@pytest.mark.parametrize("cmd", [
    "apply_patch " + _TWO,                                  # a second patch after the terminator
    "apply_patch " + _ONE + "\nrm Data/x.esp",              # any command after it
    "apply_patch " + _ONE + " && rm Data/x.esp",            # same line as the terminator? (tag text follows)
    "apply_patch extra " + _ONE,                            # text between the word and the heredoc
    "apply_patch <<A && rm x\n" + _p("*** Add File: a.txt", "+a") + "\nA",
])
def test_text_the_parser_does_not_inspect_is_refused(cmd):
    with pytest.raises(PatchParseError):
        shell_patch(cmd)


def test_a_heredoc_with_only_trailing_whitespace_is_fine():
    body, _ = shell_patch("apply_patch " + _ONE + "\n \n")
    assert parse_patch(body) == [("add", "a.txt")]


@pytest.mark.parametrize("prefix", [
    "FOO=1 apply_patch", "./apply_patch", "/usr/bin/apply_patch", "C:\\bin\\apply_patch", "sudo apply_patch",
    "env X=1 apply_patch", "(apply_patch", "apply_patch.exe", "applypatch.exe", "echo hi && apply_patch",
    "cat p | apply_patch", "{ apply_patch", "x=$(apply_patch", "exec apply_patch",
    "bash -lc \"apply_patch", "sh -c 'apply_patch", "pwsh -Command \"apply_patch",
])
def test_every_way_to_run_it_is_attributed_or_refused(prefix):
    with pytest.raises(PatchParseError):
        shell_patch(prefix + " " + _ONE)


@pytest.mark.parametrize("cmd", [
    "rg -n apply_patch tools/", "echo apply_patch is a tool", "git commit -m \"fix apply_patch parsing\"",
    "grep -rn applypatch .", "cat docs/apply_patch.md", "Get-Content apply_patch.txt",
])
def test_a_mention_is_not_a_run(cmd):
    assert shell_patch(cmd) is None


# ---- optional differential: the REAL Codex apply_patch is the oracle. Opt in with SKYGUARD_CODEX_DIFF=1.
_ADV = [
    _p("*** Add File: a.txt", "+a", " *** Delete File: victim.esp"),
    _p("*** Add File: a.txt", "+a", "\t*** Add File: evil.esp", "+x"),
    _p("  *** Update File: victim.esp", "@@", "-v", "+X"),
    _p("*** Update File: victim.esp", "  *** Move to: moved.esp", "@@", "-v", "+X"),
    _p("*** Update File: victim.esp", "*** Move to: moved.esp", "@@", "-v", "+X"),
    _p("*** Add File: a.txt", "+a", "*** Delete File: victim.esp", "*** Add File: sub/dir/n.esp", "+n"),
    _p("*** Add File:evil.esp", "+a"),
    _p("*** Add File: a.txt", "+a", "+*** Delete File: victim.esp"),
    _p("*** Delete File: victim.esp"),
]


def _tree(d):
    return {str(p.relative_to(d)).replace("\\", "/"): p.read_bytes() for p in d.rglob("*") if p.is_file()}


@pytest.mark.skipif(not os.environ.get("SKYGUARD_CODEX_DIFF"),
                    reason="differential test against the real Codex apply_patch: set SKYGUARD_CODEX_DIFF=1")
@pytest.mark.skipif(shutil.which("codex") is None, reason="codex is not on PATH")
@pytest.mark.parametrize("patch", _ADV)
def test_every_file_codex_touches_is_in_the_parsers_output(patch, tmp_path):
    for f in ("victim.esp", "other.esp"):
        (tmp_path / f).write_text("v\n")
    before = _tree(tmp_path)
    subprocess.run([shutil.which("codex"), "--codex-run-as-apply-patch", patch], cwd=str(tmp_path),
                   capture_output=True, text=True, timeout=60)
    after = _tree(tmp_path)
    touched = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    try:
        claimed = {p.strip() for _, p in parse_patch(patch)}
    except PatchParseError:
        return                                   # refusing is always safe
    assert touched <= claimed, (touched, claimed)
