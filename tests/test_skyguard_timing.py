"""No input may make the guard slow.

Claude Code DROPS the answer of a PreToolUse hook that runs past its timeout, so a command that makes one of
our regexes backtrack badly does not just cost time: it makes the guard fail open. Each shape below is a 20 KB
adversarial command (repeated wrapper words, repeated `A=1 `, repeated quotes and backslash-newlines, an
unterminated quote, a long single token) fired at EVERY shell rule, and a long file path at every write rule.

The bound is generous (CI noise) and the REAL numbers are what matter: Task 4 fix round 2 measured the worst
shape at ~130 ms after the fix (was 10.5 s). The mutations in test_mutations.py put each cause back.
"""
import sys
import time

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, paths, shell_rules, write_rules  # noqa: E402

GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
BS = chr(92)
N = 20000
BOUND_MS = 250


def _shapes():
    s = {
        "env-assign": "env " + "A=1 " * (N // 4),
        "env-assign-resaver": "env " + "A=1 " * (N // 4) + "resaver",
        "assign-only": "A=1 " * (N // 4),
        "wrappers": "sudo nohup time exec command nice " * (N // 34),
        "env-words": "env " * (N // 4),
        "xargs-opts": "xargs -n 1 -I {} " * (N // 17),
        "timeout": "timeout -s 9 5 " * (N // 15),
        "find-exec": "find x " * (N // 7) + "-exec",
        "find-repeat": "resaver ; find x -exec " * (N // 23),
        "quotes-dq": '"' * N,
        "quotes-sq": "'" * N,
        "quote-then-text": '"' + "a " * (N // 2),
        "continuations": ("a" + BS + "\n") * (N // 3),
        "continuations-spaces": (" " + BS + "\n") * (N // 3),
        "unterminated-bash-c": 'resaver bash -c "' + "x " * (N // 2),
        "bash-c-escapes": 'resaver bash -c "' + (BS + '"') * (N // 2),
        "bash-c-repeat": "resaver " + '; bash -c "x ' * (N // 14),
        "eval-repeat": "resaver ; eval '" * (N // 16),
        "long-token": "a" * N,
        "long-path-resaver": "tools/" * (N // 6) + "resaver-cli.sh",
        "resaver-junk-tail": "bash tools/resaver-cli.sh reset-havok --apply " + "a" * N,
        "resaver-junk-ess": "bash tools/resaver-cli.sh reset-havok --apply " + "a" * N + ".ess",
        "resaver-quotes": "bash tools/resaver-cli.sh reset-havok --apply " + '"a ' * (N // 3),
        "resaver-repeat": "tools/resaver-cli.sh " * (N // 20),
        "java-quoted": "resaver java " + '"' + "x" * N,
        "java-repeat": "resaver java -x " * (N // 16),
        "semis": "; " * (N // 2),
        "parens": "resaver " + "(" * N,
        "dollar-paren": "resaver " + "$(" * (N // 2),
        "heredocs": "cat <<E\n" * (N // 8),
        "heredoc-open": "bash <<E\n" + "x\n" * (N // 2),
        "heredoc-lt": "<<" * (N // 2),
        "heredoc-pipe": "<< | " * (N // 5),
        "keywords": "do then else elif { ! " * (N // 22),
        "rm-repeat": "rm " * (N // 3),
        "cp-repeat": "cp a " * (N // 4),
        "mv-repeat": "mv " * (N // 3),
        "sed-repeat": "sed -i " * (N // 6),
        "redirect-repeat": "> " * (N // 2),
        "automod-repeat": "automod add-weapon " * (N // 19),
        "automod-nif": "automod " * (N // 8),
        "automod-archive": "automod archive " * (N // 16),
        "champollion": "Champollion " * (N // 12),
        "external-out": "Caprica " + "-o x " * (N // 5),
        "psc-output": "-o " + "x" * N,
        "registry-words": "Remove-ItemProperty " * (N // 20),
        "plugins": ".esp " * (N // 5),
        "game-path-repeat": (GAME + " ") * (N // 50),
        "win-path": "C:" + BS + "x" * N,
        "unicode-space": "\u00a0" * N,
    }
    return s


SHAPES = _shapes()
SHELL_RUN_MS = {}


def _decide_shell(cmd, tmp_path):
    r = paths.roots("/x/install", "", {"SKYRIM_GAME_ROOT": GAME})
    start = time.perf_counter()
    shell_rules.decide(cmd, r, audit.Audit(str(tmp_path)))
    return (time.perf_counter() - start) * 1000


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_shell_rules_stay_fast_on_adversarial_input(tmp_path, name):
    ms = _decide_shell(SHAPES[name], tmp_path)
    assert ms < BOUND_MS, f"{name}: decide() took {ms:.0f} ms on {len(SHAPES[name])} chars (bound {BOUND_MS} ms)"


WRITE_SHAPES = {
    "separator-run": "/" * N + "x",
    "long-name": GAME + "/Data/" + "x" * N,
    "ini-dots": GAME + "/Data/SKSE/Plugins/" + "a.ini" * (N // 5),
    "ini-slashes": GAME + "/Data/SKSE/Plugins/" + "/" * N,
    "backslashes": GAME.replace("/", BS) + BS + "Data" + BS * N,
    "repeated-dirs": (GAME + "/") * (N // 50),
    "plugin-ext": "a" * N + ".esp",
    "unicode": "\u00a0" * N,
}


@pytest.mark.parametrize("name", sorted(WRITE_SHAPES))
def test_write_rules_stay_fast_on_adversarial_paths(tmp_path, name):
    start = time.perf_counter()
    write_rules.decide(WRITE_SHAPES[name], audit.Audit(str(tmp_path)))
    ms = (time.perf_counter() - start) * 1000
    assert ms < BOUND_MS, f"{name}: decide() took {ms:.0f} ms (bound {BOUND_MS} ms)"


def test_the_timing_shapes_can_fail():
    """A guard that cannot go red is decoration: the old `A.*B` scan really was quadratic on these shapes."""
    import re
    t = time.perf_counter()
    re.search(r"(^|[;&|(`]|\s)(rm|rmdir|del|erase)\s.*(" + GAME + ")", "rm " * (N // 3) * 3, re.I | re.A)
    assert (time.perf_counter() - t) * 1000 > BOUND_MS, "the unsplit rule is no longer slow here; the shape is stale"


def test_split_dotstar_matches_the_unsplit_regex_on_random_text():
    """`A.*B` became "A, then B after A's first end"; the two must agree on every line."""
    import random
    import re
    from skyguard import grep as g
    patterns = [
        r"(^|[;&|(`]|\s)(rm|rmdir|del|erase)\s.*(" + GAME + ")",
        r"(^|[;&|(`]|\s)(mv|cp|move|copy)\s.*(" + GAME + ")",
        r"sed\s+-i.*(" + GAME + ")",
        r"(automod|SpookysAutomod).*\b(replace-textures|rename-strings|fix-eyes|scale)\b",
        r"(automod|SpookysAutomod).*\b(add-weapon|add-spell)\b",
        r"(?<![/\\])[/\\]+Data[/\\]+SKSE[/\\]+Plugins[/\\]+.*\.ini$",
    ]
    words = ["rm", "mv", "cp", "sed", "-i", "automod", "Automod", "SpookysAutomod", "scale", "add-spell", "x",
             GAME, "/Data/SKSE/Plugins/", "a.ini", ".ini", " ", "  ", ";", "|", "(", "scalex", "rmx"]
    rng = random.Random(4)
    for pat in patterns:
        assert g.split_dotstar(pat) is not None, pat
        rx = re.compile(pat, re.ASCII | re.IGNORECASE)
        for _ in range(3000):
            text = "".join(rng.choice(words) for _ in range(rng.randint(1, 9)))
            assert g.grep(pat, text) == bool(rx.search(text)), (pat, text)


def test_split_dotstar_leaves_other_patterns_alone():
    from skyguard import grep as g
    assert g.split_dotstar(r"(a|b.*c)") is None          # inside a group
    assert g.split_dotstar(r"a.*b|c") is None            # top-level |
    assert g.split_dotstar(r"a.*b.*c") is None           # two of them
    assert g.split_dotstar(r"[.*]x") is None             # in a class
    assert g.split_dotstar(r"a\.*b") is None             # escaped dot
