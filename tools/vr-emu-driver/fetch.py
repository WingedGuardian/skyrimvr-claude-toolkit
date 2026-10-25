#!/usr/bin/env python3
"""Fetch the official SkyrimVR Devkit v2.0.1 null driver onto THIS machine, pinned and hash-verified. Nothing is shipped.

    python tools/vr-emu-driver/fetch.py

Upstream publishes the driver as a prebuilt package, `null driver.zip`, committed at the Release_v2.0.1 tag
(DRIVER_COMMIT); its Devkit frontend downloads exactly that file. This script downloads the same file from that
commit, never a branch, and refuses it unless:
  1. the zip's SHA-256 is DRIVER_ZIP_SHA256;
  2. every member is a plain relative path under null/ (no absolute path, no `..`);
  3. null/bin/win64/driver_null.dll hashes to DRIVER_DLL_SHA256, and the upstream sidecar driver_null.dll.sha256
     says the same.
Only then is it extracted, under tools/vr-emu-driver/build/null/ (ignored by git, never in a release), with
BUILD-INFO.json beside it. `bash tools/vr-headless.sh emu on` re-verifies the DLL against the same pin before it
swaps anything in.

Why a pinned download and no longer a local build: README.md, "Delivery decision".

Exit: 0 fetched and verified | 2 the download, a hash or the archive layout did not verify (nothing is written).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
PACKAGE = BUILD / "null"
DRIVER_REPO = "https://github.com/DubiousDuo/VR-Emulator-Driver---SkyrimVR-Devkit"
DRIVER_TAG = "Release_v2.0.1"
DRIVER_COMMIT = "32ec30294a871683254b944b4129cbdec2591e6a"
DRIVER_ZIP_URL = ("https://raw.githubusercontent.com/DubiousDuo/VR-Emulator-Driver---SkyrimVR-Devkit/"
                  "32ec30294a871683254b944b4129cbdec2591e6a/null%20driver.zip")
DRIVER_ZIP_SHA256 = "391d4dfc30f1a9884e849ea2cc2299af6893e5bb54a76734f5d96f1408d1c434"
DRIVER_DLL_SHA256 = "fc689623914c6643c6e8629dc6bcb130193888b6835d902ed6411ab476121454"
DLL = "bin/win64/driver_null.dll"
# The members vr-headless swaps over SteamVR's own null driver. The package's other files
# (devkit_version.txt, the .sha256 sidecar, licence copies) serve upstream's frontend, which the toolkit does not use.
SWAPPED = (DLL, "driver.vrdrivermanifest", "resources/settings/default.vrsettings")


class FetchError(Exception):
    pass


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "skyrim-ai-assistant-toolkit-vr-emu-fetch/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def verify(data: bytes) -> dict:
    """{relative path under null/: bytes} for a package that passes every pin; FetchError otherwise."""
    got = hashlib.sha256(data).hexdigest()
    if got != DRIVER_ZIP_SHA256:
        raise FetchError("the driver zip hashes to {}, not the pinned {}: refusing it".format(got, DRIVER_ZIP_SHA256))
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for item in archive.infolist():
            rel = PurePosixPath(item.filename.replace("\\", "/"))
            if rel.is_absolute() or ".." in rel.parts or not rel.parts or rel.parts[0] != "null":
                raise FetchError("unexpected path in the driver zip: {!r}".format(item.filename))
            if not item.is_dir():
                files[str(PurePosixPath(*rel.parts[1:]))] = archive.read(item)
    missing = [m for m in SWAPPED if m not in files]
    if missing:
        raise FetchError("the driver zip lacks {}".format(", ".join(missing)))
    dll = hashlib.sha256(files[DLL]).hexdigest()
    if dll != DRIVER_DLL_SHA256:
        raise FetchError("driver_null.dll hashes to {}, not the pinned {}".format(dll, DRIVER_DLL_SHA256))
    sidecar = files.get(DLL + ".sha256", b"").decode("ascii", "replace").strip().lower()
    if sidecar != DRIVER_DLL_SHA256:
        raise FetchError("upstream's driver_null.dll.sha256 says {!r}, not the pinned DLL hash".format(sidecar))
    return files


def install(files: dict, dest: Path = PACKAGE) -> None:
    """Write the verified package into a fresh staging folder, then move it into place in one rename."""
    BUILD.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        same = all((dest / rel).is_file() and (dest / rel).read_bytes() == body for rel, body in files.items())
        if same:
            return                                  # already fetched and identical: nothing to do
        raise FetchError("{} already holds a different package; move it aside (it is yours to keep) and run "
                         "this again".format(dest))
    stage = Path(tempfile.mkdtemp(prefix="fetch-", dir=str(BUILD)))     # same-run scratch
    for rel, body in files.items():
        target = stage.joinpath(*PurePosixPath(rel).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    os.replace(str(stage), str(dest))


def main(argv) -> int:
    if argv:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    try:
        files = verify(download(DRIVER_ZIP_URL))
        install(files)
        info = {"repo": DRIVER_REPO, "tag": DRIVER_TAG, "commit": DRIVER_COMMIT, "url": DRIVER_ZIP_URL,
                "zip_sha256": DRIVER_ZIP_SHA256, "dll_sha256": DRIVER_DLL_SHA256, "swapped": list(SWAPPED)}
        (BUILD / "BUILD-INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    except (FetchError, OSError, zipfile.BadZipFile) as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        return 2
    print("fetched and verified {} (driver_null.dll sha256 {})".format(PACKAGE, DRIVER_DLL_SHA256))
    print("next: bash tools/vr-headless.sh on, then bash tools/vr-headless.sh emu on (Steam closed)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
