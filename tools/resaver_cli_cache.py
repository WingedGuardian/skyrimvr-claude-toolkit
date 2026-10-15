"""Content-keyed isolated ReSaver compilation; stdout contains only the classpath."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from modding_harness import durable
from modding_harness.locking import exclusive


def inputs(tool, jdk, compiler, readonly):
    paths = [tool / "ResaverCLI.java", tool / "ReSaver.jar"]
    paths += sorted(p for p in (tool / "lib").glob("*") if p.is_file() and p.suffix.lower() == ".jar")
    paths += sorted((tool / "analysis-overlay").rglob("*.java"))
    records = {p.relative_to(tool).as_posix(): durable.digest(p) for p in paths}
    records.update(jdk=jdk, compiler=compiler, flags="--release 17 -encoding UTF-8", readonly=readonly,
                   cache_engine=durable.digest(Path(__file__)))
    return records


def valid(cache, expected):
    try:
        meta = json.loads((cache / "complete.json").read_text())
        if meta["inputs"] != expected or not meta["classes"]:
            return None
        actual = {p.relative_to(cache).as_posix() for p in cache.rglob("*.class")}
        if actual != set(meta["classes"]) or "driver/ResaverCLI.class" not in actual:
            return None
        for rel, digest in meta["classes"].items():
            path = cache / rel
            if path.is_symlink() or durable.digest(path) != digest:
                return None
        return meta
    except (OSError, KeyError, ValueError):
        return None


def compile_cache(tool, classpath, bash, jdk, compiler, readonly):
    expected = inputs(tool, jdk, compiler, readonly)
    key = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
    base = tool / ".compiled"
    if base.is_symlink():
        raise OSError("refusing linked compilation cache")
    base.mkdir(exist_ok=True)
    cache = base / key
    with exclusive(base / ".lock", wait=30):
        meta = valid(cache, expected)
        if meta is None:
            stage = Path(tempfile.mkdtemp(prefix="owned-compile-", dir=base))
            (stage / "driver").mkdir()

            def javac(output, sources):
                # Arguments are passed as arguments, never interpolated into shell code.
                relative_cp = classpath.replace(tool.as_posix(), ".").replace(str(tool), ".")
                command = [bash, "-c", 'exec javac "$@"', "--", "--release", "17", "-encoding", "UTF-8", "-cp", relative_cp,
                           "-d", output.relative_to(tool).as_posix(),
                           *(source.relative_to(tool).as_posix() for source in sources)]
                result = subprocess.run(command, cwd=tool, timeout=120)
                if result.returncode:
                    raise OSError("javac failed with exit {}".format(result.returncode))

            javac(stage / "driver", [tool / "ResaverCLI.java"])
            if not (stage / "driver/ResaverCLI.class").is_file():
                raise OSError("javac reported success without producing ResaverCLI.class")
            overlay = False
            overlay_sources = sorted((tool / "analysis-overlay").rglob("*.java"))
            if readonly and overlay_sources:
                (stage / "overlay").mkdir()
                try:
                    javac(stage / "overlay", overlay_sources)
                    overlay = all((stage / "overlay" / source.relative_to(tool / "analysis-overlay")
                                   .with_suffix(".class")).is_file() for source in overlay_sources)
                except OSError:
                    print("[resaver-cli] WARN: overlay compilation failed; read coverage is STOCK only", file=sys.stderr)
            if inputs(tool, jdk, compiler, readonly) != expected:
                raise OSError("compilation inputs changed while javac ran; refusing activation")
            classes = {p.relative_to(stage).as_posix(): durable.digest(p) for p in stage.rglob("*.class")}
            for rel in classes:
                with open(stage / rel, "r+b") as stream:
                    os.fsync(stream.fileno())
            meta = {"v": 1, "inputs": expected, "classes": classes, "overlay": overlay}
            durable.atomic_json(stage / "complete.json", meta)
            if cache.exists():
                # Never delete an old/corrupt cache; quarantine only this owned key
                # after resolved containment checks, for diagnosis and rollback.
                retired = base / ("invalid-" + key + "-" + uuid.uuid4().hex)
                if cache.resolve().parent != base.resolve() or retired.resolve().parent != base.resolve():
                    raise OSError("cache quarantine resolved outside the owned cache root")
                os.rename(cache, retired)
            os.rename(stage, cache)
            durable.sync_dir(base)
            if valid(cache, expected) is None:
                raise OSError("activated cache failed verification")
    return cache, meta


def launch(tool, cache, meta, classpath, separator, bash, flags, arguments):
    """Use relative ASCII launcher paths and a UTF-8 data channel for user args.

    Keep the original caller directory as data so changing the Java process cwd
    to the tool cache never redirects relative input/output paths.
    """
    relative_cp = classpath.replace(tool.as_posix(), ".").replace(str(tool), ".")
    entries = [(cache / "driver").relative_to(tool).as_posix(), relative_cp]
    if meta["overlay"]:
        entries.insert(0, (cache / "overlay").relative_to(tool).as_posix())
    runtime = tool / ".compiled" / ("arguments-" + uuid.uuid4().hex)
    runtime.mkdir()
    path = runtime / "call.args"
    values = [str(Path.cwd()), *arguments]
    data = "\n".join(base64.b64encode(value.encode("utf-8")).decode("ascii") for value in values) + "\n"
    path.write_text(data, encoding="ascii", newline="\n")
    mine = path.stat()
    try:
        if os.environ.get("RESAVER_CLI_DEBUG") == "1":
            print("[resaver-cli] verified cache={} caller={}".format(cache, Path.cwd()), file=sys.stderr)
        command = [bash, "-c", 'exec java "$@"', "--", *shlex.split(flags), "-cp", separator.join(entries),
                   "ResaverCLI", "--arguments-file", path.relative_to(tool).as_posix()]
        return subprocess.call(command, cwd=tool)
    finally:
        # Only remove this operation's unchanged argument data and empty directory.
        try:
            current = path.stat()
            if ((current.st_dev, current.st_ino) == (mine.st_dev, mine.st_ino)
                    and path.read_text(encoding="ascii") == data):
                path.unlink()
            runtime.rmdir()
        except OSError:
            pass  # do not delete changed/unowned diagnostic data


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError, ValueError):
            pass  # in-memory/test streams have no console encoding to configure
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", required=True, type=Path)
    parser.add_argument("--classpath", required=True)
    parser.add_argument("--bash", required=True)
    parser.add_argument("--jdk", required=True)
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--separator", required=True)
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--jvm-flags", default="")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        cache, meta = compile_cache(args.tool, args.classpath, args.bash, args.jdk, args.compiler, args.read_only)
        entries = [str(cache / "driver"), args.classpath]
        if args.read_only and meta["overlay"]:
            entries.insert(0, str(cache / "overlay"))
        elif args.read_only:
            print("[resaver-cli] WARN: analysis overlay unavailable; read coverage is STOCK only", file=sys.stderr)
        if args.run:
            arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
            return launch(args.tool, cache, meta, args.classpath, args.separator, args.bash, args.jvm_flags, arguments)
        print(args.separator.join(entries))
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print("[resaver-cli] REFUSED: {} (no cached-class fallback)".format(exc), file=sys.stderr)
        if args.run:
            print(json.dumps({"ok": False, "error": "verified compilation/launch failed; no cached-class fallback"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
