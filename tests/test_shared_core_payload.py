"""Independent artifact verifier must reject incomplete, changed and extra core bytes."""
import importlib.util
import shutil

import pytest

from conftest import REPO


def check(root):
    spec = importlib.util.spec_from_file_location("shared_payload_check", REPO / "tools/check-release-payload.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [problem for _label, problem in module.check_shared_core(root) if problem]


@pytest.fixture
def payload(tmp_path):
    (tmp_path / "tools").mkdir()
    shutil.copytree(REPO / "tools/modding_harness", tmp_path / "tools/modding_harness")
    shutil.copy2(REPO / "tools/modding-harness.lock.json", tmp_path / "tools/modding-harness.lock.json")
    assert not check(tmp_path), "control: the exact vendored source must verify"
    return tmp_path


def test_changed_core_bytes_refuse(payload):
    file = payload / "tools/modding_harness/preservation.py"
    file.write_bytes(file.read_bytes() + b"\n# changed\n")
    assert check(payload)


def test_missing_core_module_refuses(payload):
    (payload / "tools/modding_harness/approval.py").unlink()
    assert check(payload)


def test_extra_core_file_refuses(payload):
    (payload / "tools/modding_harness/unowned.py").write_text("# unexpected")
    assert check(payload)
