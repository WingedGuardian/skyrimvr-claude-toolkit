"""Cold backups preserve data through failures and never expire on age alone."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import time
from types import SimpleNamespace
import zipfile

import pytest

def test_obsolescence_is_bound_to_current_content(tmp_path):
    p = tmp_path/"retired.bin"
    p.write_bytes(b"old")
    cfg = {"obsolete_items":{str(p):dict(basis="operator-confirmed-obsolete",
           reason="retired", evidence="operator review", content_hash=b.content_hash(p))}}
    assert b.obsolete_reason(p, cfg) == ""
    p.write_bytes(b"new")
    assert b.obsolete_reason(p, cfg)
    cfg["obsolete_items"][str(p)]["content_hash"] = b.content_hash(p)
    del cfg["obsolete_items"][str(p)]["evidence"]
    assert b.obsolete_reason(p, cfg)

def test_temporary_ownership_is_not_a_disposition(tmp_path):
    p = tmp_path/"scratch"; p.mkdir()
    identity = dict(directory=os.path.normcase(os.path.realpath(p)),
                    toolkit=os.path.normcase(os.path.realpath(tmp_path)))
    (p/".x4-temp-owner.json").write_text(json.dumps(dict(v=1, **identity)))
    assert not b.disposable_temp(p, tmp_path)
    with pytest.raises(b.Refused, match="populated"):
        b.mark_disposable_temp(p, tmp_path)
    new = tmp_path/"new"; new.mkdir()
    b.mark_disposable_temp(new, tmp_path)
    (new/"temporary.bin").write_bytes(b"temporary")
    assert b.disposable_temp(new, tmp_path)


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cold_backups', ROOT/'tools/housekeep_backups.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


@pytest.fixture
def setup(tmp_path):
    source = tmp_path/'backups'
    source.mkdir()
    now = time.time()
    for name, days in [('oldest', 80), ('old', 60), ('new', 1)]:
        d = source/name
        d.mkdir()
        (d/'script.py').write_bytes(b'rollback source\n')
        stamp = now-days*b.DAY
        os.utime(d/'script.py', (stamp, stamp))
        os.utime(d, (stamp, stamp))
    cfg = dict(root=tmp_path, active_hours=48, backup_archive=dict(enabled=True,
        destination=str(tmp_path/'nas/vault'), sources=[dict(path=str(source), reason='rollback history')],
        min_age_days=30, keep_local=1))
    moved = tmp_path/'recycle'
    moved.mkdir()
    def recycler(paths):
        for p in paths:
            Path(p).rename(moved/Path(p).name)
        return {str(p):None for p in paths}
    return source, cfg, recycler


def archived(setup):
    source, cfg, recycler = setup
    doc = b.plan(cfg)
    result = b.apply(doc, cfg, recycler, b.digest(doc))
    assert all(r['recycled'] for r in result)
    return source, cfg, result


def test_disabled_is_inert():
    assert b.plan({})['actions'] == []
    assert b.review({}) == []
    with pytest.raises(b.Refused, match='disabled'):
        b.prune_plan({})


def test_empty_subdirectories_survive_roundtrip(setup):
    source, cfg, recycler = setup
    empty = source/'oldest/placeholder'
    empty.mkdir()
    stamp = time.time()-80*b.DAY
    os.utime(empty, (stamp, stamp))
    os.utime(empty.parent, (stamp, stamp))
    doc = b.plan(cfg)
    rows = b.apply(doc, cfg, recycler, b.digest(doc))
    row = next(r for r in rows if Path(r['source']).name == 'oldest')
    out = source.parent/'restored-with-empty'
    b.restore(row['receipt'], out)
    assert (out/'placeholder').is_dir()
    assert (out/'script.py').read_bytes() == b'rollback source\n'


def test_verified_roundtrip_and_identical_generations_share_payload(setup):
    source, cfg, rows = archived(setup)
    assert len(rows) == 2
    assert (source/'new/script.py').read_bytes() == b'rollback source\n'
    assert len(list((Path(cfg['backup_archive']['destination'])/'objects').glob('*.zip'))) == 1
    assert len({r['receipt'] for r in rows}) == 2
    out = source.parent/'restored'
    b.restore(rows[0]['receipt'], out)
    assert (out/'script.py').read_bytes() == b'rollback source\n'
    with pytest.raises(b.Refused, match='must be new'):
        b.restore(rows[0]['receipt'], out)


def test_plan_hash_and_changed_source_cannot_retire_original(setup):
    source, cfg, recycler = setup
    doc = b.plan(cfg)
    with pytest.raises(b.Refused, match='approval mismatch'):
        b.apply(doc, cfg, recycler, 'not the approval')
    (source/'oldest/script.py').write_text('new edit')
    with pytest.raises(b.Refused, match='changed since plan'):
        b.apply(doc, cfg, recycler, b.digest(doc))
    assert (source/'oldest/script.py').read_text() == 'new edit'
    assert not Path(cfg['backup_archive']['destination']).exists()


def test_missing_local_reserve_refuses(setup):
    source, cfg, recycler = setup
    doc = b.plan(cfg)
    (source/'new').rename(source.parent/'moved-new')
    with pytest.raises((b.Refused, OSError)):
        b.apply(doc, cfg, recycler, b.digest(doc))
    assert (source/'oldest').is_dir()


def test_failed_nas_verification_keeps_every_source(setup, monkeypatch):
    source, cfg, recycler = setup
    doc = b.plan(cfg)
    def bad(*args, **kwargs):
        raise b.Refused('injected NAS readback failure')
    monkeypatch.setattr(b, 'verify_object', bad)
    with pytest.raises(b.Refused, match='NAS readback'):
        b.apply(doc, cfg, recycler, b.digest(doc))
    assert (source/'oldest/script.py').exists() and (source/'old/script.py').exists()
    assert list((Path(cfg['backup_archive']['destination'])/'objects').glob('*.partial'))


@pytest.mark.parametrize('protected', ['save', 'pin', 'repository', 'tracked'])
def test_protected_generation_is_named_and_kept(setup, protected):
    source, cfg, _ = setup
    p = source/'oldest'
    protect = lambda _: ''
    if protected == 'save':
        (p/'save').mkdir()
        (p/'save/test.xml.gz').write_bytes(b'not a save parser input')
    elif protected == 'pin':
        (p/'.housekeep-keep').touch()
    elif protected == 'repository':
        (p/'.git').mkdir()
    else:
        protect = lambda path: 'git-tracked' if path.name == 'script.py' and path.parent == p else ''
    doc = b.plan(cfg, protect)
    assert not any(a['path'] == str(p) for a in doc['actions'])
    assert any(k['path'] == str(p) and 'protected' in k['reason'] or k['path'] == str(p) and 'save/profile' in k['reason'] for k in doc['kept'])


def test_destination_reparse_and_source_overlap_refuse(setup, monkeypatch):
    source, cfg, _ = setup
    cfg['backup_archive']['destination'] = str(source/'inside')
    with pytest.raises(b.Refused, match='overlap'):
        b.plan(cfg)
    cfg['backup_archive']['destination'] = str(source.parent/'nas/vault')
    (source.parent/'nas').mkdir()
    original = Path.lstat
    def linked(path):
        s = original(path)
        return SimpleNamespace(st_mode=s.st_mode, st_file_attributes=0x400) if path == source.parent/'nas' else s
    monkeypatch.setattr(Path, 'lstat', linked)
    with pytest.raises(b.Refused, match='reparse'):
        b.plan(cfg)


def test_uncertain_usefulness_never_expires_and_review_budget_is_honest(setup):
    _, cfg, rows = archived(setup)
    for p in Path(cfg['backup_archive']['destination']).rglob('*'):
        os.utime(p, (1, 1))
    assert {r['state'] for r in b.review(cfg)} == {'retained'}
    assert {r['state'] for r in b.review(cfg, budget=-1)} == {'deferred'}
    assert b.prune_plan(cfg)['objects'] == []


def test_corruption_blocks_review_and_prune(setup):
    _, cfg, rows = archived(setup)
    Path(rows[0]['archive']).write_bytes(b'corrupt')
    assert {r['state'] for r in b.review(cfg)} == {'failed'}
    with pytest.raises(b.Refused, match='incomplete'):
        b.prune_plan(cfg)


def test_age_declaration_is_not_obsolescence(setup):
    _, cfg, rows = archived(setup)
    record = json.loads(Path(rows[0]['receipt']).read_text())
    cfg['backup_archive']['obsolete'] = {record['id']:dict(reason='old', evidence='calendar')}
    assert {r['state'] for r in b.review(cfg)} == {'retained'}
    assert b.prune_plan(cfg)['records'] == []


def test_explicit_prune_keeps_shared_archives_until_all_restore_points_retire(setup):
    _, cfg, rows = archived(setup)
    records = [json.loads(Path(r['receipt']).read_text()) for r in rows]
    decisions = cfg['backup_archive']['obsolete'] = {}
    decisions[records[0]['id']] = dict(basis='operator-confirmed-obsolete', reason='reviewed disposition', evidence='receipt A')
    doc = b.prune_plan(cfg)
    assert doc['records'] and not doc['objects']
    assert b.prune(doc, cfg, b.digest(doc)) == []
    assert Path(rows[0]['archive']).exists()
    with pytest.raises(b.Refused, match='retired'):
        b.restore(rows[0]['receipt'], Path(cfg['root'])/'old-restore')
    decisions[records[1]['id']] = dict(basis='operator-confirmed-obsolete', reason='reviewed disposition', evidence='receipt B')
    doc = b.prune_plan(cfg)
    assert len(doc['objects']) == 1
    with pytest.raises(b.Refused, match='approval mismatch'):
        b.prune(doc, cfg, 'wrong')
    with pytest.raises(b.Refused, match='approval mismatch'):
        b.prune(dict(doc, kind='tampered'), cfg, b.digest(doc))
    removed = b.prune(doc, cfg, b.digest(doc), remover=lambda p:p.unlink())
    assert len(removed) == 1 and not Path(rows[0]['archive']).exists()
    assert {r['state'] for r in b.review(cfg)} == {'retired'}


def test_restore_receipt_cannot_escape_vault(setup):
    _, cfg, rows = archived(setup)
    record = json.loads(Path(rows[0]['receipt']).read_text())
    record['object_id'] = '../../escape'
    p = Path(cfg['root'])/'bad-receipt.json'
    p.write_text(json.dumps(record))
    with pytest.raises(b.Refused, match='identity'):
        b.restore(p, Path(cfg['root'])/'new-restore')


def test_periodic_review_is_read_only_for_the_vault_and_obeys_interval(setup):
    _, cfg, rows = archived(setup)
    dest = Path(cfg['backup_archive']['destination'])
    before = {p.relative_to(dest).as_posix():b.file_hash(p) for p in dest.rglob('*') if p.is_file()}
    assert '2 checked' in b.periodic_note(cfg)
    assert b.periodic_note(cfg) == ''
    assert {p.relative_to(dest).as_posix():b.file_hash(p) for p in dest.rglob('*') if p.is_file()} == before


def test_skyrim_entry_point_preserves_protections_in_opt_in_tier(setup, monkeypatch, capsys):
    source, cfg, _ = setup
    monkeypatch.syspath_prepend(str(ROOT/'tools'))
    spec = importlib.util.spec_from_file_location('cold_entry', ROOT/'tools/housekeep.py')
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    monkeypatch.setattr(entry, 'tracked_files', lambda root:{entry._key(source/'oldest/script.py')})
    config_path = Path(cfg['root'])/'housekeep.json'
    config_path.write_text(json.dumps(dict(root=str(cfg['root']), backup_archive=cfg['backup_archive'])))
    assert entry.main(['--config',str(config_path),'backups','report']) == 0
    report = json.loads(capsys.readouterr().out)['plan']
    assert any('tracked' in r['reason'] for r in report['kept'])
    assert not any(r['path'] == str(source/'oldest') for r in report['actions'])
