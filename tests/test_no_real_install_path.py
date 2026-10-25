"""No test may name the maintainer's real install, or anything under its parent folder.

Until 2026-10-24 tests/guard_corpus/game-root.txt held the real install path and about 28 test files spelled it as a
literal, so on the maintainer's machine every corpus verdict ran against a folder that EXISTS, and nowhere else did:
a verdict that read the disk would have passed on one host for a reason the others never saw. The corpus root is now
synthetic and defined once (conftest.GAME_ROOT, read from game-root.txt); this file keeps it that way.

The one exemption is a byte-for-byte copy of a RELEASED document (tests/fixtures/migrate-3x/): migrate-3x recognises
those by their sha256, so they cannot be edited. A file is exempt only while its hash is one tools/migrate-3x-known.json
lists; an edited copy is scanned like any other file.
"""
import base64
import hashlib
import json
import os
import re
from pathlib import Path

from conftest import GAME_ROOT, REPO

# Spelled in pieces so this file does not match itself. Any case; any run of backslashes before the space (a bash-
# escaped `\ `, doubled again inside JSON); and the parent's 8.3 short name.
_PARENT = re.compile(r"(?i)\b" + "g" + r"og(\\*) games")
_SHORT = re.compile(r"(?i)\b" + "g" + r"oggam~[0-9]")
_B64 = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")


def _known_release_hashes():
    known = json.loads((REPO / "tools" / "migrate-3x-known.json").read_text(encoding="utf-8"))
    return {spec["sha256"] for specs in known["docs"].values() for spec in specs}


def _hits_in_text(text):
    found = [m.group(0) for m in _PARENT.finditer(text)] + [m.group(0) for m in _SHORT.finditer(text)]
    for tok in _B64.findall(text):                       # an encoded command (pwsh -EncodedCommand) hides a path
        try:
            raw = base64.b64decode(tok, validate=True)
        except ValueError:
            continue
        for enc in ("utf-16-le", "utf-8"):
            try:
                decoded = raw.decode(enc)
            except UnicodeDecodeError:
                continue
            found += ["base64:" + m.group(0) for m in _PARENT.finditer(decoded)]
    return found


def real_install_hits(root, exempt_hashes):
    """{relative path: [matched spellings]} for every file under root that names the real install's parent folder."""
    out = {}
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or ".pytest_cache" in path.parts:
            continue
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() in exempt_hashes:
            continue
        hits = _hits_in_text(data.decode("utf-8", errors="replace"))
        if hits:
            out[path.relative_to(root).as_posix()] = hits
    return out


def test_no_file_under_tests_names_the_real_install():
    hits = real_install_hits(REPO / "tests", _known_release_hashes())
    assert hits == {}, ("these files name the real install (use conftest.GAME_ROOT / GAME_PARENT / GAME_SHORT, or a "
                        "synthetic folder of the test's own):\n" + "\n".join(f"  {k}: {v[:3]}" for k, v in hits.items()))


def test_the_scan_sees_every_spelling_of_the_real_install(tmp_path):
    """Each spelling in its own file, so one that the scan misses cannot hide behind another that it catches."""
    parent = "G" + "OG Games"
    bs = chr(92)
    spellings = {
        "fwd.txt": "C:/" + parent + "/The Elder Scrolls V Skyrim VR",
        "back.txt": "C:" + bs + parent + bs + "x",
        "escaped.json": json.dumps("rm -rf /c/" + parent.replace(" ", bs + " ") + "/x"),
        "upper.txt": "C:/" + parent.upper() + "/X",
        "short.txt": "C:/" + "G" + "OGGAM~1/THEELD~1",
        "encoded.txt": "pwsh -EncodedCommand " + base64.b64encode(("rd 'C:/" + parent + "'").encode("utf-16-le")).decode(),
    }
    for name, text in spellings.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    assert sorted(real_install_hits(tmp_path, set())) == sorted(spellings)


def test_only_an_unedited_released_copy_is_exempt(tmp_path):
    data = ("released text naming C:/" + "G" + "OG Games/x\n").encode("utf-8")
    (tmp_path / "KNOWLEDGEBASE.fixture").write_bytes(data)
    assert real_install_hits(tmp_path, {hashlib.sha256(data).hexdigest()}) == {}
    (tmp_path / "KNOWLEDGEBASE.fixture").write_bytes(data + b"an edit\n")
    assert list(real_install_hits(tmp_path, {hashlib.sha256(data).hexdigest()})) == ["KNOWLEDGEBASE.fixture"]


def test_the_corpus_root_is_synthetic_spaced_and_absent_from_this_host():
    assert re.fullmatch(r"[A-Z]:/[^/]+ [^/]+/[^/]+ [^/]+", GAME_ROOT), GAME_ROOT     # spaced parent AND leaf
    assert not _hits_in_text(GAME_ROOT)
    assert not os.path.exists(GAME_ROOT) and not os.path.exists(GAME_ROOT.rsplit("/", 1)[0]), (
        GAME_ROOT + " exists on this machine: a corpus verdict could now depend on its contents. Pick another "
        "synthetic root in tests/guard_corpus/game-root.txt.")
