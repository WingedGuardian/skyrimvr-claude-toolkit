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


def test_no_mountpoint_nested_in_readonly_workspace():
    """A fresh checkout has no gitignored node_modules/; Docker cannot create a nested
    mountpoint through the read-only workspace bind, so startup would fail."""
    text = (REPO / ".devcontainer/devcontainer.json").read_text()
    config = json.loads("\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//")))
    workspace = config["workspaceFolder"].rstrip("/") + "/"
    for mount in config["mounts"]:
        target = dict(part.split("=", 1) for part in mount.split(",") if "=" in part)["target"]
        assert not target.startswith(workspace), mount
    helper = (REPO / "devshell-docker.sh").read_text()
    assert "target=/workspace/" not in helper and ":/workspace/" not in helper
    install = (REPO / ".devcontainer/install-dependencies.sh").read_text()
    assert "SKYRIM_NODE_PREFIX" in install
    dockerfile = (REPO / ".devcontainer/Dockerfile").read_text()
    assert "NODE_PATH=" in dockerfile and "/workspace" not in dockerfile.split("NODE_PATH=")[1].split()[0]
