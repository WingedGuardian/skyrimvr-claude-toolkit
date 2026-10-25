"""Test-only barriers. Python writes are constrained; shell effects require reviewed fixtures."""
import os
from pathlib import Path
import re
import shlex
import sys

MANAGERS = set("winget choco scoop apt apt-get dnf pacman brew npm npx corepack pnpm yarn pip pip3 pipx uv conda "
               "gem cargo dotnet".split())
_ACTIVE = False
# Events that write a file whose path is not an `open` (review L7 MEDIUM): Windows Python 3.12+ copies through
# CopyFile2 (shutil.copy/copy2), and os.truncate empties a file by name on every version. Value: the argument
# index that names the destination.
WRITE_EVENTS = {"_winapi.CopyFile2": 1, "shutil.copyfile": 1, "os.truncate": 0}
_SHELL_STRING_FLAGS = ("-c", "/c", "/C", "-Command", "-command")


def active():
    return _ACTIVE


def pip_module(tokens):
    """True for every spelling of `python -m pip`: `-m pip`, `-mpip`, `-Im pip`, `-Impip`, `-m pip3`, `-m pip.x`."""
    tokens = [str(t) for t in tokens]
    for i, t in enumerate(tokens):
        m = re.fullmatch(r"-[A-Za-z]*m(\S*)", t)
        if m:
            module = m.group(1) or (tokens[i + 1] if i + 1 < len(tokens) else "")
            if module in ("pip", "pip3") or module.startswith(("pip.", "pip3.")):
                return True
    return False


def package_manager(executable, argv):
    """The native tripwire's verdict for one Popen. A shell string (`bash -c "..."`, `cmd /c`, `-Command`) is read
    for a `-m pip` whose interpreter flags (-I, -E, -S) skip sitecustomize: this is the only check that sees it. A
    plain `python -m pip` in the string is left to sitecustomize, so that check stays exercised."""
    tokens = argv if isinstance(argv, (tuple, list)) else [a or b for a, b in re.findall(r'"([^"]*)"|(\S+)', argv)]
    tokens = [os.fsdecode(t) if isinstance(t, bytes) else str(t) for t in tokens]
    first = str(executable or (tokens[0] if tokens else ""))
    name = Path(first).name.lower().removesuffix(".exe").removesuffix(".cmd").removesuffix(".ps1")
    node_cli = any(t.replace("\\", "/").endswith(("/npm-cli.js", "/npx-cli.js")) for t in tokens)
    inner = []
    for i, t in enumerate(tokens[:-1]):
        if t in _SHELL_STRING_FLAGS:
            try:
                inner += shlex.split(tokens[i + 1], posix=True)
            except ValueError:
                inner += tokens[i + 1].split()
    no_site = any(re.fullmatch(r"-[A-Za-z]*[IES][A-Za-z]*", t) for t in inner)
    return name in MANAGERS or pip_module(tokens) or (no_site and pip_module(inner)) or node_cli


def contained_in(roots, value):
    if isinstance(value, int) or value is None:
        return True
    value = os.fsdecode(value)
    if value.lower() in ("nul", "/dev/null", r"\\.\nul"):
        return True
    if value.startswith("\\\\?\\"):
        value = "\\\\" + value[8:] if value[4:].startswith("UNC\\") else value[4:]
    return any(Path(value).resolve().is_relative_to(root) for root in roots)


def check_write(roots, event, args):
    """Raise PermissionError when `event` writes outside `roots`. The copy and truncate events only; `open` and the
    os.* path events are judged in install() (they need the dir_fd anchoring there)."""
    if event in WRITE_EVENTS and not contained_in(roots, args[WRITE_EVENTS[event]]):
        raise PermissionError("outside test fixture: " + event + " " + repr(args[WRITE_EVENTS[event]]))


def install():
    global _ACTIVE
    roots = [Path(r).resolve() for r in os.environ["HARNESS_FIXTURE_ROOT"].split(os.pathsep) if r]

    def contained(value):
        return contained_in(roots, value)

    def dir_fds(event, args):
        # POSIX shutil.rmtree is fd-based: it removes RELATIVE names with dir_fd=<open dir>.
        # Resolving those against the cwd (the repository) wrongly refuses fixture cleanup.
        if event in ("os.rename", "os.link"):
            return [args[2], args[3]]
        if event == "os.symlink":
            return [None, args[2]]
        if event == "os.mkdir":
            return [args[2]]
        return [args[1]]

    def anchored(value, fd):
        if fd is None or isinstance(value, int) or os.path.isabs(os.fsdecode(value)):
            return value
        try:
            base = os.readlink("/proc/self/fd/" + str(fd))
        except OSError:
            return value  # unresolvable: judged against the cwd, i.e. refused unless contained
        return os.path.join(base, os.fsdecode(value))

    def audit(event, args):
        if event == "open":
            filename, mode, flags = args
            writing = any(c in (mode or "") for c in "wax+") or flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            if writing and not contained(filename):
                raise PermissionError("outside test fixture: " + str(filename))
        elif event in WRITE_EVENTS:
            check_write(roots, event, args)
        elif event in ("os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.symlink", "os.link"):
            count = 2 if event in ("os.rename", "os.symlink", "os.link") else 1
            paths = [anchored(p, fd) for p, fd in zip(args[:count], dir_fds(event, args))]
            if event == "os.symlink" and not isinstance(paths[0], int) and not os.path.isabs(os.fsdecode(paths[0])):
                # A relative symlink TARGET is relative to the link's own folder, not the cwd (Linux CI run
                # 38187755448: venv's `lib64 -> lib` inside the fixture was refused as the repository's ./lib).
                paths[0] = os.path.join(os.path.dirname(os.fsdecode(paths[1])), os.fsdecode(paths[0]))
            if any(not contained(p) for p in paths):
                raise PermissionError("outside test fixture: " + repr(args[:count]))
        elif event == "subprocess.Popen":
            executable, argv, _, env = args
            child_env = env if env is not None else os.environ
            child_env["HARNESS_NATIVE_PATH"] = child_env.get("PATH", "")
            if package_manager(executable, argv):
                with open(os.environ["HARNESS_TRIPWIRE_LOG"], "a", encoding="utf-8") as log:
                    log.write("native " + repr(argv) + "\n")
                raise PermissionError("PACKAGE-MANAGER TRIPWIRE: " + repr(argv))
    sys.addaudithook(audit)
    _ACTIVE = True
