"""Read-only dependency/configuration gates also run under ordinary CI pytest."""
import hashlib
import json

import pytest
from conftest import REPO


@pytest.mark.parametrize("input_file,lock_file", [("requirements.txt", "requirements.lock"),
                                                   ("requirements-windows.txt", "requirements-windows.lock")])
def test_python_lock_tracks_input_and_hashes(input_file, lock_file):
    digest = hashlib.sha256((REPO / input_file).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    text = (REPO / lock_file).read_text()
    assert "# input-sha256: " + digest in text
    assert "--hash=sha256:" in text
    for line in (REPO / input_file).read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            assert line.strip() in text


def test_node_lock_tracks_declared_dependencies():
    package = json.loads((REPO / "package.json").read_text())
    lock = json.loads((REPO / "package-lock.json").read_text())
    assert lock["packages"][""]["dependencies"] == package["dependencies"]
    for name, value in lock["packages"].items():
        if name:
            assert value["integrity"].startswith("sha512-")


def test_devcontainer_uses_readonly_mount_strings():
    text = (REPO / ".devcontainer/devcontainer.json").read_text()
    config = json.loads("\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//")))
    assert isinstance(config["workspaceMount"], str)
    for mount in [config["workspaceMount"], *config["mounts"]]:
        assert isinstance(mount, str)
        if "type=bind" in mount:
            assert "readonly" in mount.split(",")
    assert "install-dependencies.sh" in config["postCreateCommand"]
    assert "npm ci" in (REPO / ".devcontainer/install-dependencies.sh").read_text()
