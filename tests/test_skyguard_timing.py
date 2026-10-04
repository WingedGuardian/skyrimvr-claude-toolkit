"""No input may make the guard slow.

Claude Code DROPS the answer of a PreToolUse hook that runs past its timeout, so a command that makes one of
our regexes backtrack badly does not just cost time: it makes the guard fail open. Each shape below is an
adversarial command (repeated wrapper words, repeated `A=1 `, repeated quotes and backslash-newlines, an
unterminated quote, a long single token) fired at EVERY shell rule and at the parser, and a long file path at
every write rule.

WHAT IS MEASURED (Plan 5 Task 5 fix round 2): wall-clock bounds failed whenever the machine was busy, so a red
meant "the machine was loaded" as often as "a rule went quadratic", and would have been ignored. Now:
  - CPU time of THIS process (time.process_time), so other processes' load does not count;
  - repeated until at least MIN_SAMPLE_MS of CPU has accumulated, because the Windows CPU clock ticks in ~15.6 ms
    steps and a single fast call reads as 0 or 15.6;
  - LINEARITY: each shape runs at 20 KB and 80 KB; cpu(80 KB) / cpu(20 KB) must stay under RATIO_MAX (linear is
    ~4, quadratic ~16) whenever the 80 KB time is large enough to measure (>= RATIO_FLOOR_MS). This is independent
    of machine speed;
  - an absolute CEILING_MS of CPU at 80 KB, generous: it catches the catastrophic case, it does not tune;
  - EARLY_MS at 20 KB: a shape already that slow at 20 KB fails at once instead of spending minutes at 80 KB.
The mutations in test_mutations.py put each known cause back and require the named shape to go red.
"""
import gc
import sys
import time

import pytest

from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
from skyguard import audit, paths, shell_rules, write_rules  # noqa: E402

GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
BS = chr(92)
SMALL, LARGE = 20000, 80000
RATIO_MAX = 8.0          # cpu(80 KB) / cpu(20 KB): linear ~4, quadratic ~16
RATIO_FLOOR_MS = 10.0    # below this at 80 KB the ratio is clock noise, and the shape is fast anyway
CEILING_MS = 2000.0      # CPU per call at 80 KB
EARLY_MS = 1000.0        # CPU per call at 20 KB: already catastrophic
MIN_SAMPLE_MS = 80.0     # accumulate at least this much CPU per measurement (clock granularity ~15.6 ms)
PRECISE_SAMPLE_MS = 400.0  # the re-measurement when a ratio reads >= RATIO_MAX
MAX_REPS = 20000
WALL_CAP_S = 5.0         # stop repeating after this much wall time (a call that waits rather than computes)


class _QuietAudit(audit.Audit):
    """An Audit that writes nothing: the measurement is the rules' CPU, not log-file I/O."""

    def __init__(self):
        super().__init__("")

    def raw(self, line):
        pass

    def log(self, hook, level, msg):
        pass

    def rule(self, hook, rule_id, decision, msg, level=None):
        pass


def cpu_ms(fn, min_sample=MIN_SAMPLE_MS):
    """CPU milliseconds of THIS process per call of fn(), averaged over enough calls to beat the clock tick."""
    gc.collect()
    reps = 0
    t0, w0 = time.process_time(), time.perf_counter()
    while True:
        fn()
        reps += 1
        spent = (time.process_time() - t0) * 1000
        if spent >= min_sample or reps >= MAX_REPS or spent >= EARLY_MS or time.perf_counter() - w0 > WALL_CAP_S:
            return spent / reps


def assert_linear(label, make):
    """make(N) -> a zero-argument callable that runs the shape at size N."""
    small = cpu_ms(make(SMALL))
    assert small < EARLY_MS, f"{label}: {small:.0f} ms CPU per call at 20 KB (catastrophic; 80 KB not tried)"
    large = cpu_ms(make(LARGE))
    assert large < CEILING_MS, f"{label}: {large:.0f} ms CPU per call at 80 KB (ceiling {CEILING_MS:.0f})"
    if large >= RATIO_FLOOR_MS:
        ratio = large / max(small, 0.25)
        if ratio >= RATIO_MAX:
            # An 80 ms sample of a ~30 ms call is 3 calls on a 15.6 ms clock (+-17%). Before calling it
            # superlinear, measure both sizes once more with a 5x sample (+-3%): a quadratic (~16) stays red.
            small = cpu_ms(make(SMALL), PRECISE_SAMPLE_MS)
            large = cpu_ms(make(LARGE), PRECISE_SAMPLE_MS)
            ratio = large / max(small, 0.25)
        assert ratio < RATIO_MAX, (
            f"{label}: superlinear -- {small:.1f} ms CPU at 20 KB, {large:.1f} ms at 80 KB, ratio {ratio:.1f} "
            f"(linear ~4, quadratic ~16, limit {RATIO_MAX:.0f})")
    return small, large


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


SHAPE_NAMES = sorted(_shapes(SMALL))
ROOTS = paths.roots("/x/install", "", {"SKYRIM_GAME_ROOT": GAME})


@pytest.mark.parametrize("name", SHAPE_NAMES)
def test_shell_rules_stay_fast_on_adversarial_input(name):
    def make(n):
        cmd = _shapes(n)[name]
        return lambda: shell_rules.decide(cmd, ROOTS, _QuietAudit())
    assert_linear(name, make)


# Plan 5 Task 7: the delete rules resolve EVERY operand of every delete primitive against the cwd, glob- and
# short-name-match unresolved words against the roots, scan interpreter code and each opaque exec's words. Shapes
# aimed at each of those, fired with a cwd inside the install (so relative words really resolve) in bash and
# PowerShell.
def _delete_shapes(N):
    # The parser gives up past 2,000 tokens (MAX_TOKENS: the legacy text rules decide then), so a shape of 40,000
    # one-letter words would measure only that fallback. Repeated units are therefore LONG words (F = 60, W = 200,
    # X = 400 characters), keeping every shape at ~1,100-1,400 tokens at 80 KB so the per-operand code really runs
    # on every word (test_the_delete_shapes_reach_the_delete_code checks it).
    F, W, X = "f" * 60, "w" * 200, "x" * 400
    return {
        "rm-many-operands": "rm " + (F + " ") * (N // 61),
        "rm-many-data-operands": "rm -rf " + ("Data/" + F + " ") * (N // 66),
        "rm-many-glob-operands": "rm -rf " + ("/c/*/" + F + " ") * (N // 66),
        "rm-many-short-operands": "rm " + ("C:/GOGGAM~1/" + F + " ") * (N // 73),
        "rm-var-operands": 'ls "' + GAME + '"; rm ' + ('"$X' + F + '" ') * (N // 65),
        "rm-option-run": "rm " + ("-" + F + " ") * (N // 62) + "x",
        "rm-deep-relative": "rm " + "a/" * (N // 2) + "x",
        "rm-dotdot-run": "rm " + "../" * (N // 3) + "x",
        "rm-glob-middle-run": "rm /c/" + "*/" * (N // 2) + "x",
        "rm-short-name-run": "rm C:/" + "AB~1/" * (N // 5) + "x",
        "git-clean-specs": "git clean -fd -- " + (F + " ") * (N // 61),
        "git-global-opts": "git " + ("-c a=" + W + " ") * (N // 206) + "clean -f",
        "git-rm-specs": "git rm -r " + (F + " ") * (N // 61),
        "robocopy-flags": "robocopy a b " + ("/MIR:" + F + " ") * (N // 66),
        "npx-flags": "npx " + ("-" + F + " ") * (N // 62) + "rimraf x",
        "python-code-long": 'python -c "' + "os.remove(x); " * (N // 14) + '" "' + GAME + '"',
        "interp-unlink-run": 'python -c "' + "unlink " * (N // 7) + '"',
        "node-rmsync-run": 'node -e "' + ".rmSync(" * (N // 8) + '"',
        "remove-item-params": "Remove-Item " + ("-Include " + W + " ") * (N // 210),
        "remove-item-many": "Remove-Item " + (W + ", ") * (N // 202) + "b",
        "printf-sh-opaque": "printf '" + "rm -rf x " * (N // 9) + "' | sh",
        "opaque-many": ("$R " + W + "; ") * (N // 206) + 'ls "' + GAME + '"',
        "xargs-rm-many": ("ls " + X + " | xargs rm; ") * (N // 417) + 'ls "' + GAME + '"',
        "find-delete-starts": "find " + (F + " ") * (N // 61) + "-delete",
        "parse-fail-destroyer": 'rm -rf "' + "x" * N,
    }


PARSE_FAIL_SHAPES = {"parse-fail-destroyer"}


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_the_delete_shapes_reach_the_delete_code(shell):
    """A shape the parser refuses measures only the fallback regex. Every delete shape but the deliberate parse
    failure must parse at 80 KB and yield execs, and the operand shapes must yield operands."""
    from skyguard import deletes, shellparse
    for name, cmd in _delete_shapes(LARGE).items():
        p = shellparse.parse(cmd, shell)
        if name in PARSE_FAIL_SHAPES:
            assert not p.ok, name
            continue
        assert p.ok and p.execs, (name, shell, p.why)
        if name.startswith(("rm-", "git-clean", "git-rm", "remove-item-many", "robocopy", "npx")) or (
                name.startswith("find-") and shell == "bash"):           # PowerShell's `find` is find.exe
            assert any(deletes.operands(e) for e in p.execs), (name, shell)


DELETE_SHAPES = sorted(_delete_shapes(SMALL))


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("name", DELETE_SHAPES)
def test_delete_rules_stay_fast(name, shell):
    def make(n):
        cmd = _delete_shapes(n)[name]
        return lambda: shell_rules.decide(cmd, ROOTS, _QuietAudit(), cwd="/x/install", shell=shell)
    assert_linear(name + "/" + shell, make)


# Plan 5 Task 8: the write rules classify every redirect / tee / Set-Content / sed / dd target and every copy
# destination (resolving it against the cwd), and on a parse failure or an opaque program scan the text per segment.
# Long repeated words keep each shape under the parser's token cap so the per-target code really runs.
def _shell_write_shapes(N_):
    F, W = "f" * 60, "w" * 200
    return {
        "redirect-many": "echo x" + (" > " + W) * (N_ // 203),
        "redirect-many-esp": "echo x" + (" >> " + W + ".txt") * (N_ // 208),
        "tee-many": "tee " + (F + " ") * (N_ // 61),
        "set-content-params": "Set-Content " + ("-Encoding " + W + " ") * (N_ // 211) + "x",
        "sed-i-many": "sed -i s/a/b/ " + (F + " ") * (N_ // 61),
        "dd-many": "dd " + ("of=" + F + " ") * (N_ // 64),
        "cp-many-esp": "cp " + (F + ".esp ") * (N_ // 65) + "Data/",
        "cp-t-many": "cp -t Data " + (F + ".esp ") * (N_ // 65),
        "robocopy-files": "robocopy a Data " + (F + ".esp ") * (N_ // 65),
        "copy-item-many": "Copy-Item " + (W + ", ") * (N_ // 202) + "b",
        "skse-path-run": "echo x > " + "Data/SKSE/Plugins/" * (N_ // 18) + "x.ini",
        "dots-run": "echo x > " + "." * N_ + "esp",
        "fallback-parse-fail": 'echo "' + ("> " + F + ".esp ") * (N_ // 67),
        "fallback-write-words": 'echo "' + "> " * (N_ // 2),
        "opaque-pipe-text": "printf '" + ("echo > " + F + ".esp ") * (N_ // 72) + "' | sh",
        "opaque-many-pipes": ("printf " + W + " | $X; ") * (N_ // 211),
        # Task 9 item 0: the fallback's redirect target (one word, quoted or not) and tee's operand list.
        "opaque-gt-run": '"$X" -c "' + "a>b " * (N_ // 4) + '"',
        "opaque-gt-quote-run": "\"$X\" -c '" + '>"' * (N_ // 2) + "'",
        "opaque-tee-options": '"$X" -c "tee ' + ("-" + F + " ") * (N_ // 62) + 'x.esp"',
        "fallback-gt-quote-run": 'echo "' + "> '" * (N_ // 3),
    }


BASH_OPAQUE = {"opaque-gt-run", "opaque-gt-quote-run", "opaque-tee-options"}


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_the_write_shapes_reach_the_write_code(shell):
    """Every shape but the deliberate parse failures must parse at 80 KB and yield execs."""
    from skyguard import shellparse
    for name, cmd in _shell_write_shapes(LARGE).items():
        p = shellparse.parse(cmd, shell)
        if name.startswith("fallback-"):
            assert not p.ok, (name, shell)
            continue
        if shell == "powershell" and name in BASH_OPAQUE:
            assert p.ok, (name, shell, p.why)       # `"$X" -c ...` is a string expression in PowerShell, not an exec
            continue
        assert p.ok and p.execs, (name, shell, p.why)


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("name", sorted(_shell_write_shapes(SMALL)))
def test_write_shell_rules_stay_fast(name, shell):
    def make(n):
        cmd = _shell_write_shapes(n)[name]
        return lambda: shell_rules.decide(cmd, ROOTS, _QuietAudit(), cwd="/x/install", shell=shell)
    assert_linear(name + "/" + shell, make)


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
        # fix round 3 branches
        "ps-cast-run": "[void](ri a); " * (N // 14),
        "ps-method-tail-long": "$x." + "a" * N + "(1)",
        "saps-array-run": "Start-Process cmd -ArgumentList @(" + "'a'," * (N // 4) + "'b')",
        "printf-into-shell": "printf 'a' | " * (N // 13) + "bash",
        "echo-into-python": "echo 'print(1)' | python; " * (N // 27),
        "xargs-shell-run": "echo a | xargs bash; " * (N // 21),
        "env-cluster-run": "env " + "-iuX " * (N // 5),
        "comspec-switches": "%COMSPEC% " + "/s " * (N // 3) + "/c rd x",
    }


def all_parse_shapes(N):
    s = dict(_shapes(N))
    s.update(_parse_shapes(N))
    return s


PARSE_NAMES = sorted(all_parse_shapes(SMALL))


@pytest.mark.parametrize("shell", ["", "cmd"])          # "" is bash AND PowerShell (R5)
@pytest.mark.parametrize("name", PARSE_NAMES)
def test_shellparse_stays_fast_on_adversarial_input(name, shell):
    from skyguard import shellparse

    def make(n):
        text = all_parse_shapes(n)[name]
        p = shellparse.parse(text, shell)
        assert not p.why.startswith("internal error"), p.why
        return lambda: shellparse.parse(text, shell)
    assert_linear(f"{name} [{shell or 'bash+pwsh'}]", make)


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


@pytest.mark.parametrize("name", sorted(_write_shapes(SMALL)))
def test_write_rules_stay_fast_on_adversarial_paths(name):
    def make(n):
        path = _write_shapes(n)[name]
        return lambda: write_rules.decide(path, _QuietAudit())
    assert_linear(name, make)


def test_the_timing_shapes_can_fail():
    """A guard that cannot go red is decoration: the old `A.*B` scan really was quadratic on these shapes."""
    import re
    rx = re.compile(r"(^|[;&|(`]|\s)(rm|rmdir|del|erase)\s.*(" + GAME + ")", re.I | re.A)
    with pytest.raises(AssertionError, match="catastrophic|ceiling|superlinear"):
        assert_linear("unsplit rm .* rule", lambda n: (lambda: rx.search("rm " * (n // 3))))


def test_the_ratio_clause_alone_catches_quadratic_work_under_the_ceilings():
    """Quadratic but fast enough to pass EARLY_MS and CEILING_MS: only the 80/20 ratio can see it. Its linear twin
    must pass, or the ratio clause is just noise."""
    def quadratic(n):
        k = n // 100                                   # 200 at 20 KB, 800 at 80 KB: k*k grows 16x
        return lambda: sum(i ^ j for i in range(k) for j in range(k))

    def linear(n):
        k = n * 40                                     # grows 4x
        return lambda: sum(range(k))
    with pytest.raises(AssertionError, match="superlinear"):
        assert_linear("synthetic quadratic", quadratic)
    small, large = assert_linear("synthetic linear", linear)
    assert large >= RATIO_FLOOR_MS, "the linear twin is too fast to reach the ratio check; it proves nothing"


def test_cpu_time_ignores_time_spent_waiting():
    """The point of process_time: time the process is not on the CPU (another process's load, a sleep) does not
    count, so a busy machine cannot turn a timing test red."""
    assert cpu_ms(lambda: time.sleep(0.05)) < 10


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
    assert shell_rules.resaver_apply(text) is None
    assert cpu_ms(lambda: shell_rules.resaver_apply(text)) < 50     # CPU: ~0.01 ms with it, ~500 ms without


def test_the_registry_rule_is_linear():
    """The registry property rule, `.*` inside an alternation, was not split and was quadratic (0.5 s at 80 KB)."""
    assert_linear("Remove-ItemProperty run",
                  lambda n: (lambda: shell_rules.decide("Remove-ItemProperty " * (n // 20), ROOTS, _QuietAudit())))


# Plan 5 Task 6: targets.resolve / zone and the paths-file value grammar run on every operand and every hook call.
def _target_shapes(N):
    return {
        "dotdot-run": ("../" * (N // 3), "bash"),
        "slash-run": ("/" * N, "bash"),
        "backslash-run": (BS * N, "powershell"),
        "component-run": ("a/" * (N // 2), "bash"),
        "dot-space-run": ("a. /" * (N // 4), "cmd"),
        "home-var-run": ("$HOME" * (N // 5), "bash"),
        "ps-env-run": ("$env:USERPROFILE" * (N // 16), "powershell"),
        "cmd-var-run": ("%USERPROFILE%" * (N // 13), "cmd"),
        "dollar-run": ("$" * N, "bash"),
        "percent-run": ("%" * N, "cmd"),
        "glob-run": ("*/" * (N // 2), "bash"),
        "unc-run": ("//s/h" + "/x" * (N // 2), "bash"),
        "msys-run": ("/c" * (N // 2), "bash"),
        "provider-run": ("FileSystem::" * (N // 12), "powershell"),
        "brace-sep-run": ("{a,/" * (N // 4), "bash"),
        "brace-open-run": ("{" * N, "bash"),
        "brace-group-run": ("{a,b/c}" * (N // 7), "bash"),
    }


@pytest.mark.parametrize("name", sorted(_target_shapes(SMALL)))
def test_targets_stay_fast_on_adversarial_words(name):
    from skyguard import targets
    env = {"HOME": "/c/Users/me", "USERPROFILE": "C:/Users/me"}

    def make(n):
        word, dialect = _target_shapes(n)[name]

        def run():
            p = targets.resolve(word, GAME, dialect, env)
            targets.zone(p or ("c:/" + word), ROOTS)
        return run
    assert_linear(name, make)


def _value_shapes(N):
    return {
        "sq-escape-run": "'a'" + (BS + "''") * (N // 4),
        "dq-long": '"' + "a" * N + '"',
        "dq-escape-run": '"' + (BS + '"') * (N // 2) + '"',
        "bare-escape-run": (BS + " ") * (N // 2),
        "space-run-then-text": "a" + " " * N + "b",
        "space-run-then-comment": "'a'" + " " * N + "# c",
        "unterminated-sq": "'" + "a" * N,
        "quote-alternation": "'a'\"b\"" * (N // 6),
    }


@pytest.mark.parametrize("name", sorted(_value_shapes(SMALL)))
def test_paths_value_grammar_is_linear(name):
    assert_linear(name, lambda n: (lambda: paths.parse_value(_value_shapes(n)[name])))


def test_paths_file_read_is_linear(tmp_path):
    def make(n):
        f = tmp_path / ("p%d.env" % n)
        f.write_text("SKYRIM_GAME_ROOT=" + "'a'" * (n // 3) + "\n" + " " * n + "!\n" + "export " * (n // 7) + "\n",
                     encoding="utf-8")
        return lambda: paths.read_config(f, {})
    assert_linear("paths-file", make)


# Fix round 1: an adversarial payload cwd (normalised on every operand) and covers_protected over long globs.
def _cwd_shapes(N):
    return {
        "cwd-dotdot": "C:/" + "../" * (N // 3),
        "cwd-slashes": "/c" + "/" * N,
        "cwd-components": "C:/" + "a/" * (N // 2),
        "cwd-tilde-digit": "C:/" + "a~1/" * (N // 4),
        "cwd-streams": "C:/" + "a:b/" * (N // 4),
        "cwd-unc": "//localhost/c$" + "/x" * (N // 2),
    }


@pytest.mark.parametrize("name", sorted(_cwd_shapes(SMALL)))
def test_targets_stay_fast_on_an_adversarial_cwd(name):
    from skyguard import targets

    def make(n):
        cwd = _cwd_shapes(n)[name]
        return lambda: targets.resolve("Data/x", cwd, "bash", {})
    assert_linear(name, make)


def _covers_shapes(N):
    return {
        "star-run": "c:/" + "*" * N,
        "star-a-run": "c:/" + "*a" * (N // 2) + "b",
        "question-run": "c:/" + "?" * N,
        "class-run": "c:/" + "[a]" * (N // 3),
        "open-class-run": "c:/" + "[" * N,
        "brace-run": "c:/" + "{a,b}" * (N // 5),
        "brace-open-run": "c:/" + "{" * N,
        "deep-path": "c:/" + "*/" * (N // 2),
    }


@pytest.mark.parametrize("name", sorted(_covers_shapes(SMALL)))
def test_covers_protected_stays_fast(name):
    from skyguard import targets
    assert_linear(name, lambda n: (lambda: targets.covers_protected(_covers_shapes(n)[name], ROOTS)))


@pytest.mark.parametrize("shape", ["open", "open-a", "closed-no-comma"])
def test_brace_expansion_is_linear(shape):
    """covers_protected caps a component at MAX_GLOB_COMPONENT, but _braces itself must not be quadratic either."""
    from skyguard import targets
    make_text = {"open": lambda n: "{" * n, "open-a": lambda n: "{a" * (n // 2),
                 "closed-no-comma": lambda n: "{a}" * (n // 3)}[shape]
    assert_linear(shape, lambda n: (lambda: targets._braces(make_text(n))))
