"""Read-only dependency/configuration gates also run under ordinary CI pytest."""
import hashlib
import json
import os
import subprocess
import sys

import pytest
from conftest import REPO


@pytest.mark.parametrize("input_file,lock_file", [("requirements.txt", "requirements.lock"),
                                                   ("tools/requirements-tools.txt", "tools/requirements-tools.lock")])
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


# Lives here, not in test_harness_controls.py: that module skips wholesale under plain
# pytest, so this check of the skip itself never ran in the ordinary CI suite.
def test_bare_pytest_skips_unsafe_controls(tmp_path):
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTEST_PLUGINS", "HARNESS_FIXTURE_ROOT", "TOOLKIT_REQUIRE_HARNESS"):
        env.pop(key, None)
    result = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                             "tests/test_harness_controls.py", "tests/test_harness_integrations.py"],
                            cwd=REPO, env=env, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipped" in result.stdout
    assert " passed" not in result.stdout


def test_ci_workflow_parses_and_runs_harness_modules_on_both_legs():
    """An unparseable workflow runs NOTHING. `--only-binary=:all: --require-hashes` in a plain
    scalar is a YAML mapping error (": "), which silently disabled every CI job."""
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    behavior = workflow["jobs"]["behavior"]
    assert set(behavior["strategy"]["matrix"]["os"]) >= {"ubuntu-latest", "windows-latest"}
    harness = [step for step in behavior["steps"]
               if "tests/run_harness_tests.py" in str(step.get("run", "")) and "if" not in step]
    assert harness, "no unconditional harness-contained step in the behavior job"
    # Plan 8 T10 added harness-run tripwire and timing steps before it: the suite is the one naming -rfEs on tests/.
    suite = [step for step in harness if "tests/ -v" in step["run"]]
    assert len(suite) == 1, [step.get("name") for step in harness]
    harness = suite
    command = harness[0]["run"]
    # The WHOLE suite goes through the launcher (which includes both barrier-dependent modules), on
    # both matrix legs (no `if`), and a run without the barriers is an error, not a quiet green.
    assert "python -B tests/run_harness_tests.py tests/ " in command + " ", command
    assert "-rfEs" in command, "failures, errors and skips must all be named: " + command
    assert str(harness[0].get("env", {}).get("TOOLKIT_REQUIRE_HARNESS")) == "1", harness[0]
    # ...and nothing else runs plain pytest over tests/, which would skip those modules unseen.
    plain = [step for step in behavior["steps"] if "-m pytest" in str(step.get("run", ""))
             and "run_harness_tests" not in str(step.get("run", ""))
             and "pip install" not in str(step.get("run", ""))]
    assert not plain, plain


def test_the_lock_never_pins_a_pygments_with_the_known_advisory():
    """pygments < 2.20.0 carries GHSA-5239-wwwm-4pmq (CVE-2026-4539). It enters the lock only through pytest >= 8.4."""
    import re
    versions = re.findall(r"^pygments==(\d+)\.(\d+)\.(\d+)", (REPO / "requirements.lock").read_text(), re.M | re.I)
    for v in versions:
        assert tuple(map(int, v)) >= (2, 20, 0), "pygments %s.%s.%s in requirements.lock has an open advisory" % v


def test_package_json_pins_exact_versions_that_the_lock_resolves_to():
    """A caret lets a plain `npm install` float; exact specs mean every install path gets the locked version."""
    import re
    package = json.loads((REPO / "package.json").read_text())
    lock = json.loads((REPO / "package-lock.json").read_text())
    assert package["dependencies"]
    for name, spec in package["dependencies"].items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", spec), "%s: %r is a range, not an exact version" % (name, spec)
        assert lock["packages"]["node_modules/" + name]["version"] == spec, name
