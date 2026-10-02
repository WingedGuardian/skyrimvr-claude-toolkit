"""Run tests with test-only package-manager tripwires; never provision dependencies."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def evidence_root(repo):
    """repo/.pytest_cache, or container-local scratch when the checkout is read-only.

    Inside the dev container /workspace is a read-only bind, so creating the evidence
    directory there raises EROFS. HARNESS_EVIDENCE_ROOT overrides both.
    """
    override = os.environ.get("HARNESS_EVIDENCE_ROOT")
    candidates = [Path(override)] if override else [Path(repo) / ".pytest_cache"]
    candidates.append(Path(tempfile.gettempdir()) / "skyrim-harness-evidence")
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = Path(tempfile.mkdtemp(prefix="probe-", dir=candidate))
            probe.rmdir()
            return candidate
        except OSError:
            continue
    raise SystemExit("Could not check: no writable evidence directory")


def main():
    repo = Path(__file__).resolve().parents[1]
    root = evidence_root(repo)
    if root != repo / ".pytest_cache":
        print("Repository not writable; evidence goes to", root, flush=True)
    work = Path(tempfile.mkdtemp(prefix="harness-", dir=root))
    binary = work / "bin"
    binary.mkdir()
    managers = "winget choco scoop apt apt-get dnf pacman brew npm pip pip3 dotnet".split()
    for name in managers:
        stub = binary / name
        stub.write_text('#!/bin/bash\n' +
                        'printf "%s %s\\n" "' + name + '" "$*" >> "$HARNESS_TRIPWIRE_LOG"\n' +
                        'echo "PACKAGE-MANAGER TRIPWIRE: ' + name + ' $*" >&2\nexit 97\n',
                        encoding="utf-8", newline="\n")
        stub.chmod(0o755)
    bash = shutil.which("bash")
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if candidate.exists():
            bash = str(candidate)
    if not bash:
        raise SystemExit("Could not check: no Bash runtime")
    runtime_dirs = [str(binary), str(Path(sys.executable).parent), str(Path(bash).parent)]
    for name in ("node", "jq", "git", "docker"):
        executable = shutil.which(name)
        if executable:
            runtime_dirs.append(str(Path(executable).parent))
    if os.name == "nt":
        git = Path(bash).parents[1]
        runtime_dirs += [str(git / "usr/bin"), str(git / "mingw64/bin"), str(git / "cmd")]
        packages = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
        runtime_dirs += [str(p.parent) for p in packages.glob("jqlang.jq_*/jq.exe")]
    else:
        runtime_dirs += ["/usr/bin", "/bin"]
    paths = os.pathsep.join(dict.fromkeys(runtime_dirs))
    node = shutil.which("node")
    if node:
        quoted = "'" + Path(node).as_posix().replace("'", "'\\''") + "'"
        (binary / "node").write_text(
            '#!/bin/bash\nfor a in "$@"; do\n'
            ' case "$a" in *npm-cli.js) echo "PACKAGE-MANAGER TRIPWIRE: node npm-cli.js" >&2; '
            'echo "node npm-cli.js" >> "$HARNESS_TRIPWIRE_LOG"; exit 97;; esac\ndone\n'
            'exec ' + quoted + ' "$@"\n', newline="\n")
        (binary / "node").chmod(0o755)
    # Git Bash rewrites native PATH on startup; restore each subprocess's intended PATH inside Bash.
    bootstrap = work / "bootstrap.sh"
    bootstrap.write_text('if [ -n "${HARNESS_NATIVE_PATH:-}" ]; then\n'
                         ' if command -v cygpath >/dev/null 2>&1; then PATH=$(cygpath -p "$HARNESS_NATIVE_PATH");'
                         ' else PATH="$HARNESS_NATIVE_PATH"; fi\nfi\n'
                         'export PATH="$HARNESS_TRIPWIRE_BIN:$PATH"\n', newline="\n")
    (work / "sitecustomize.py").write_text(
        "import os, sys\n"
        "if '-m' in sys.orig_argv and any(x in ('pip', 'pip3') for x in sys.orig_argv):\n"
        " with open(os.environ['HARNESS_TRIPWIRE_LOG'], 'a') as log: log.write('python -m pip\\n')\n"
        " os.write(2, b'PACKAGE-MANAGER TRIPWIRE: python -m pip\\n'); os._exit(97)\n"
        "from harness_isolation import install\ninstall()\n")
    env = {k: v for k, v in os.environ.items() if k.upper() in
           ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT")}
    for directory in ("home", "temp", "home/local", "home/roaming"):
        (work / directory).mkdir(parents=True, exist_ok=True)
    tripbin = binary.as_posix()
    if os.name == "nt":
        tripbin = "/" + tripbin[0].lower() + tripbin[2:]
    env.update(PATH=paths, TOOLKIT_BASH=bash, HOME=str(work / "home"),
               USERPROFILE=str(work / "home"), LOCALAPPDATA=str(work / "home/local"),
               APPDATA=str(work / "home/roaming"), TEMP=str(work / "temp"), TMP=str(work / "temp"),
               PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               PYTHONPATH=os.pathsep.join([str(work), str(repo / "tests")]),
               PYTEST_PLUGINS="harness_pytest", BASH_ENV=bootstrap.as_posix(),
               HARNESS_TRIPWIRE_BIN=tripbin, HARNESS_FIXTURE_ROOT=str(work),
               HARNESS_TRIPWIRE_LOG=str(work / "tripwire.log"),
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    args = sys.argv[1:] or ["tests/", "-q", "-rs"]
    command = [sys.executable, "-B", "-m", "pytest", *args, "-p", "no:cacheprovider",
               "--basetemp", str(work / "pytest")]
    (work / "command.json").write_text(json.dumps(command, indent=2))
    print("Evidence:", work, flush=True)
    return subprocess.call(command, cwd=repo, env=env)


if __name__ == "__main__":
    sys.exit(main())
