"""One apply_patch grammar for every caller. Anything it does not know is refused, never skipped."""
import json
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
