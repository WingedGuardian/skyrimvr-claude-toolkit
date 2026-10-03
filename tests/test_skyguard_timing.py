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
SIZES = (20000, 80000)
BOUND_MS = 250          # at 20 KB; the bound scales with size, so a LINEAR rule passes at 80 KB and a quadratic fails


def _shapes(N):
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
        "java-subst-repeat": "resaver " + "$(java a " * (N // 9),
        "find-subst-repeat": "resaver " + "$(find a " * (N // 9),
        "rm-slash-run": "rm " + "/" * N,
        "rm-backslash-run": "rm " + BS * N,
        "rm-r-data-slash-run": "rm -r Data" + "/" * N,
        "rm-r-data-backslash-run": "rm -rf C:" + BS * N,
        "rm-relative-data": "rm " + "a/" * (N // 2) + "x",
        "sed-slash-run": "sed -i " + "/" * N,
        "sed-backslash-run": "sed -i " + BS * N,
        "mv-slash-run": "mv a " + "/" * N,
        "cp-backslash-run": "cp a " + BS * N,
        "redirect-slash-run": "echo x > " + "/" * N,
        "redirect-backslash-run": "echo x > " + BS * N,
        "external-o-slash-run": "Caprica -o " + "/" * N,
    }
    return s


SHAPES = {}
for _n in SIZES:
    for _k, _v in _shapes(_n).items():
        SHAPES[_k if _n == SIZES[0] else _k + "@" + str(_n // 1000) + "k"] = _v


def _bound(text):
    return BOUND_MS * max(1.0, len(text) / 20000.0) * 1.2
SHELL_RUN_MS = {}


def _decide_shell(cmd, tmp_path):
    r = paths.roots("/x/install", "", {"SKYRIM_GAME_ROOT": GAME})
    start = time.perf_counter()
    shell_rules.decide(cmd, r, audit.Audit(str(tmp_path)))
    return (time.perf_counter() - start) * 1000


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_shell_rules_stay_fast_on_adversarial_input(tmp_path, name):
    ms = _decide_shell(SHAPES[name], tmp_path)
    assert ms < _bound(SHAPES[name]), (
        f"{name}: decide() took {ms:.0f} ms on {len(SHAPES[name])} chars (bound {_bound(SHAPES[name]):.0f} ms)")


# Plan 5 Task 5: the parser sees every command too, so it gets every shell shape above PLUS shapes aimed at its
# own structure: deep nesting of ( $( { ` (must fail closed at depth 9, never recurse), long and unterminated
# quotes and heredocs, 10k `;`, long \-newline chains, PowerShell backtick runs and cmd ^ runs.
def _parse_shapes(N):
    return {
        "nest-paren": "( " * (N // 2),
        "nest-paren-closed": "( " * (N // 4) + ") " * (N // 4),
        "nest-subst": "$(" * (N // 2),
        "nest-dq-subst": '"$(' * (N // 3),
        "nest-brace": "{ " * (N // 2),
        "nest-backtick-escaped": "`" + (BS + "`") * (N // 2),
        "backtick-pairs": "``" * (N // 2),
        "backtick-words": "`a` " * (N // 4),
        "long-dq": '"' + "a" * N + '"',
        "long-sq": "'" + "a" * N + "'",
        "unterminated-dq-vars": '"' + "a $x " * (N // 5),
        "unterminated-heredoc": "cat <<EOF\n" + "line\n" * (N // 5),
        "heredoc-many-lines": "cat <<EOF\n" + "line\n" * (N // 5) + "EOF\n",
        "heredoc-many-on-a-line": "cat" + " <<E" * (N // 4) + "\n" + "E\n" * (N // 4),
        "heredoc-body-substs": "cat <<EOF\n" + "$(a) " * (N // 5) + "\nEOF",
        "heredoc-shell-body": "bash <<EOF\n" + "a; " * (N // 3) + "\nEOF",
        "semis-10k": ";" * 10000,
        "semis-spaced": "; " * (N // 2),
        "backslash-newline-chain": (BS + "\n") * (N // 2),
        "dollar-dparen": "$((" * (N // 3),
        "arith-closed": "$((1)) " * (N // 7),
        "dparen-cmd": "(( " * (N // 3),
        "brace-param-open": "${" * (N // 2),
        "brace-param-closed": "${a}" * (N // 4),
        "ansi-open": "$'" + (BS + "n") * (N // 2),
        "assign-array": "a=(" + "x " * (N // 2) + ")",
        "extglob-run": "@(a)" * (N // 4),
        "func-def-run": "f() " * (N // 4),
        "long-func-name": "a" * N + "()",
        "long-assign-paren": "a" * N + "=(",
        "long-fd-digits": "1" * N + ">x",
        "case-patterns": "case x in " + "a) b;; " * (N // 8) + "esac",
        "dbracket-ops": "[[ " + "a && b || ( c ) < d " * (N // 20) + "]]",
        "pipe-to-shell": "echo a | " * (N // 9) + "bash",
        "bash-c-long": "bash -c '" + "a " * (N // 2) + "'",
        "bash-c-nested": "bash -c " * (N // 8) + "x",
        "find-exec-chain": "find . -exec " * (N // 13),
        "xargs-chain": "xargs " * (N // 6),
        "sudo-chain": "sudo " * (N // 5),
        "cd-chain": "cd a; " * (N // 6),
        "redirect-run": ">" * N,
        "proc-subst-run": "<(" * (N // 2),
        "ps-backtick-run": "`" * N,
        "ps-backtick-pairs": "`a" * (N // 2),
        "ps-here-open": "@'\n" + "x\n" * (N // 2),
        "ps-io-nested": "[IO.File]::Delete(" * (N // 18),
        "ps-io-run": "[IO.File]::Delete('a') " * (N // 23),
        "ps-block-comment-open": "<#" + "x" * N,
        "ps-hash-run": "@{a=1} " * (N // 7),
        "ps-sq-doubled": "'" + "''" * (N // 2),
        "ps-smart-quotes": "‘" * N,
        "ps-commas": "ri " + "a," * (N // 2),
        "ps-params": "ri " + "-Path a " * (N // 8),
        "pwsh-command-long": "pwsh -Command " + "ri a; " * (N // 6),
        "cmd-caret-run": "^" * N,
        "cmd-caret-pairs": "^a" * (N // 2),
        "cmd-percent-run": "%" * N,
        "cmd-c-long": "cmd /c " + "del a & " * (N // 8),
        "cmd-for-parens": "for %f in (" + "a " * (N // 2) + ") do del %f",
        # fix round 1 branches
        "time-brace-run": "time { " * (N // 7),
        "time-p-run": "time -p " * (N // 8),
        "coproc-run": "coproc a { " * (N // 11),
        "array-substs": "a=(" + "$(x) " * (N // 5) + ")",
        "array-run": "a=( $(x) ) " * (N // 11),
        "brace-subst-run": "${x:-$(a)}" * (N // 10),
        "dparen-cmdlike": "((a b)) " * (N // 8),
        "dparen-long-word": "((" + "a" * N + " b))",
        "arith-subst-run": "$(( $(a) ))" * (N // 11),
        "cmd-rem-amp": "rem x & " * (N // 8),
        "cmd-eq-run": "if " + "a==" * (N // 3),
        "cmd-slash-word": "rd" + "/s" * (N // 2),
        "cmd-for-do-body": "for %i in (a) do (" * (N // 18),
        "cmd-for-f-command": "for /f %i in ('" + "rd x & " * (N // 7) + "') do echo",
        "ps-delete-run": "$x.Delete() " * (N // 12),
        "ps-method-long": "a" * N + ".Delete()",
        "ps-herestring-subst": '@"\n' + "$(a) " * (N // 5) + '\n"@',
        "ps-saps-args": "Start-Process cmd -Args " + "'a'," * (N // 4),
        "ps-iex-dynamic": "iex $c; " * (N // 8),
        "ps-endash-run": "ri " + "–Path a " * (N // 9),
        "ps-stop-parsing": "cmd --% " * (N // 8),
        "env-split-string": "env -S '" + "env -S " * (N // 8) + "'",
        "wrapper-chain": "ionice strace unbuffer setsid busybox " * (N // 38),
        "flock-chain": "flock f " * (N // 8),
        "alias-run": "alias a='b'; " * (N // 13),
        "trap-run": "trap 'a' EXIT; " * (N // 15),
        "parallel-seps": "parallel a " + "::: b " * (N // 6),
        "pipe-into-shell": "cat x | " * (N // 8) + "bash",
        "shell-reads-file": "sh < x; " * (N // 8),
        "comspec-run": "%COMSPEC% /c " * (N // 13),
    }


PARSE_SHAPES = dict(SHAPES)
for _n in SIZES:
    for _k, _v in _parse_shapes(_n).items():
        PARSE_SHAPES[_k if _n == SIZES[0] else _k + "@" + str(_n // 1000) + "k"] = _v
PARSE_MS = {}


@pytest.mark.parametrize("shell", ["", "cmd"])          # "" is bash AND PowerShell (R5)
@pytest.mark.parametrize("name", sorted(PARSE_SHAPES))
def test_shellparse_stays_fast_on_adversarial_input(name, shell):
    from skyguard import shellparse
    text = PARSE_SHAPES[name]
    start = time.perf_counter()
    p = shellparse.parse(text, shell)
    ms = (time.perf_counter() - start) * 1000
    PARSE_MS[(name, shell)] = ms
    assert not p.why.startswith("internal error"), p.why
    assert ms < _bound(text), f"{name} [{shell or 'bash+pwsh'}]: parse() took {ms:.0f} ms on {len(text)} chars"


def _write_shapes(N):
    return {
        "separator-run": "/" * N + "x",
        "long-name": GAME + "/Data/" + "x" * N,
        "ini-dots": GAME + "/Data/SKSE/Plugins/" + "a.ini" * (N // 5),
        "ini-slashes": GAME + "/Data/SKSE/Plugins/" + "/" * N,
        "backslashes": GAME.replace("/", BS) + BS + "Data" + BS * N,
        "repeated-dirs": (GAME + "/") * (N // 50),
        "plugin-ext": "a" * N + ".esp",
        "unicode": "\u00a0" * N,
    }


WRITE_SHAPES = {}
for _n in SIZES:
    for _k, _v in _write_shapes(_n).items():
        WRITE_SHAPES[_k if _n == SIZES[0] else _k + "@" + str(_n // 1000) + "k"] = _v


@pytest.mark.parametrize("name", sorted(WRITE_SHAPES))
def test_write_rules_stay_fast_on_adversarial_paths(tmp_path, name):
    start = time.perf_counter()
    write_rules.decide(WRITE_SHAPES[name], audit.Audit(str(tmp_path)))
    ms = (time.perf_counter() - start) * 1000
    assert ms < _bound(WRITE_SHAPES[name]), f"{name}: decide() took {ms:.0f} ms"


def test_the_timing_shapes_can_fail():
    """A guard that cannot go red is decoration: the old `A.*B` scan really was quadratic on these shapes."""
    import re
    t = time.perf_counter()
    re.search(r"(^|[;&|(`]|\s)(rm|rmdir|del|erase)\s.*(" + GAME + ")", "rm " * 20000, re.I | re.A)
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

@pytest.mark.parametrize("pattern,text", [
    (r"a+.*a", "aa"),                  # A's first end is not its earliest useful end
    (r"(abc|a).*b", "abc"),            # an alternation whose first alternative ends too late
    (r"(?i)foo.*bar", "FOOxBAR"),      # an inline flag
    (r"a.*?b", "aXb"),                 # a lazy quantifier
    (r"(a).*\1", "aXa"),               # a backreference
    (r"x(a.*b)", "xaXb"),              # the .* inside a group
    (r"(?=a).*b", "aXb"),              # a lookahead
    (r"a.*b|c", "c"),                  # a top-level |
    (r"a.*b.*c", "aXbYc"),             # two of them
    (r"rm\s.*x", "rm x"),              # a plausible first part that is not in the list
])
def test_split_dotstar_refuses_what_it_cannot_prove_equivalent(pattern, text):
    """Each of these splits WRONG if it is split (the first three really do), so the split must refuse them and
    grep must give the full regex's answer."""
    import re
    from skyguard import grep as g
    assert g.split_dotstar(pattern) is None
    assert g.grep(pattern, text) == bool(re.compile(pattern, re.ASCII | re.IGNORECASE).search(text))
    assert g.grep(pattern, text) is True


def test_a_naive_split_really_is_wrong_for_the_counterexamples():
    """The refusals above are not hypothetical: split by hand, `a+.*a` and `(abc|a).*b` give the wrong answer."""
    import re
    assert re.search(r"a+.*a", "aa")
    m = re.search(r"a+", "aa")
    assert re.search(r"a", "aa"[m.end():]) is None
    m = re.search(r"(abc|a)", "abc")
    assert re.search(r"b", "abc"[m.end():]) is None and re.search(r"(abc|a).*b", "abc")


def test_the_safe_prefix_list_is_what_the_rules_use():
    """If a rule's `A` changes, the split silently stops applying (and the timing shapes go red), so pin them."""
    from skyguard import grep as g
    assert len(g.SPLIT_SAFE_FIRST) == 6


def test_split_dotstar_refuses_a_possessive_quantifier():
    from skyguard import grep as g
    assert g.split_dotstar(r"a.*+b") is None     # (Python 3.10 cannot compile it, 3.11+ can; either way: no split)


def test_resaver_prefilter_skips_unrelated_commands():
    """Without the `resaver` early return every command pays for the ReSaver regexes (measured ~0.5 s at 80 KB of
    parentheses); with it an unrelated command costs a substring search."""
    text = "(" * 80000
    start = time.perf_counter()
    assert shell_rules.resaver_apply(text) is None
    assert (time.perf_counter() - start) * 1000 < 50


def test_the_registry_rule_is_linear():
    """The registry property rule, `.*` inside an alternation, was not split and was quadratic (0.5 s at 80 KB)."""
    import tempfile
    text = "Remove-ItemProperty " * 4000
    r = paths.roots("/x/install", "", {"SKYRIM_GAME_ROOT": GAME})
    start = time.perf_counter()
    shell_rules.decide(text, r, audit.Audit(tempfile.mkdtemp()))
    assert (time.perf_counter() - start) * 1000 < 250
