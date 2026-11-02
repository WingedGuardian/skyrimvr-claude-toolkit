"""Different sibling bytes must fail even when both local policy pins pass."""
import hashlib
import importlib.util
import tempfile
from pathlib import Path
import pytest

ROOT=next(p for p in Path(__file__).resolve().parents
          if (p/'scripts/x4housekeep.py').is_file() or (p/'tools/housekeep.py').is_file())
FOLDER=ROOT/('scripts' if (ROOT/'RETENTION.md').is_file() else 'tools')
spec=importlib.util.spec_from_file_location('housekeep_parity_test',FOLDER/'check-housekeep-parity.py')
parity=importlib.util.module_from_spec(spec); spec.loader.exec_module(parity)


@pytest.fixture
def pair():
    with tempfile.TemporaryDirectory() as directory:
        roots=[Path(directory)/'x4',Path(directory)/'skyrim']
        for root,x4 in zip(roots,(True,False)):
            folder=root/('scripts' if x4 else 'tools'); folder.mkdir(parents=True)
            doc=root/('RETENTION.md' if x4 else 'docs/RETENTION.md'); doc.parent.mkdir(parents=True,exist_ok=True)
            doc.write_text('<!-- shared-policy:begin -->\nForever\n<!-- shared-policy:end -->\n')
            pin=root/('tools/x4validate/tests/test_retention_policy.py' if x4 else 'tests/test_retention_policy.py')
            pin.parent.mkdir(parents=True,exist_ok=True)
            pin.write_text('PINNED = '+repr(hashlib.sha256(b'Forever\n').hexdigest()))
            for name in ('housekeep_archive.py','housekeep_backups.py','check-housekeep-parity.py'):
                (folder/name).write_bytes(b'identical owned fixture')
        yield roots


def test_current_siblings_and_pins_agree(pair):
    report=parity.compare(*pair)
    assert report['state']=='equal' and report['compared']==report['expected']==4


def test_two_individually_valid_pins_cannot_hide_policy_divergence(pair):
    root=pair[1]
    (root/'docs/RETENTION.md').write_text('<!-- shared-policy:begin -->\nChanged\n<!-- shared-policy:end -->\n')
    (root/'tests/test_retention_policy.py').write_text('PINNED = '+repr(hashlib.sha256(b'Changed\n').hexdigest()))
    report=parity.compare(*pair)
    assert report['state']=='different'
    assert [r['artifact'] for r in report['rows'] if not r['equal']]==['shared_policy']


def test_helper_fork_is_detected(pair):
    (pair[1]/'tools/housekeep_archive.py').write_bytes(b'forked helper')
    report=parity.compare(*pair)
    assert report['state']=='different'
    assert [r['artifact'] for r in report['rows'] if not r['equal']]==['archive_helper']


def test_missing_peer_is_a_non_answer(pair):
    report=parity.compare(pair[0],pair[1]/'missing')
    assert report['state']=='unavailable' and report['compared']==0 and report['expected']==4
    assert report['errors']
