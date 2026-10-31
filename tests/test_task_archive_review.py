"""Ordinary archives: review unknown value forever; retire only exact obsolete bytes."""
import importlib.util
import hashlib
import json
import os
import time
import tempfile
import sys
import pytest
from pathlib import Path

from conftest import REPO

spec = importlib.util.spec_from_file_location('task_archive_review_tests', REPO/'tools/housekeep_archive.py')
ha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ha)


@pytest.fixture
def tmp_path():
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def config(tmp_path):
    return dict(root=tmp_path, archive_root=str(tmp_path/'archives'), archive_retention_days=0,
                archive_review_days=30, archive_review_seconds=15, obsolete_items={})


def payload(cfg):
    p = Path(cfg['archive_root'])/'backups'/'old.zip'
    p.parent.mkdir(parents=True)
    p.write_bytes(b'potentially useful, including a damaged archive')
    os.utime(p, (time.time()-400*86400,)*2)
    return p


def declare(cfg, p):
    cfg['obsolete_items'][str(p)] = dict(basis='operator-confirmed-obsolete',
        reason='superseded diagnostic, operator reviewed', evidence='fixture disposition',
        content_hash=hashlib.blake2b(p.read_bytes(),digest_size=16).hexdigest())


def test_zero_retention_still_reviews_unknown_without_retiring(tmp_path):
    cfg=config(tmp_path); p=payload(cfg)
    result=ha.review_archives(cfg)
    assert result['state']=='complete'
    assert len(result['rows'])==1 and result['rows'][0]['state']=='retained'
    assert p.exists()


def test_explicit_content_decision_is_verified_even_when_retention_zero(tmp_path):
    cfg=config(tmp_path); p=payload(cfg); declare(cfg,p)
    row=ha.review_archives(cfg)['rows'][0]
    assert row['state']=='review retirement'
    assert row['content_hash']==cfg['obsolete_items'][str(p)]['content_hash']
    assert p.exists()  # review never removes or recycles


def test_changed_bytes_or_incomplete_decision_stay(tmp_path):
    cfg=config(tmp_path); p=payload(cfg); declare(cfg,p)
    p.write_bytes(b'revised and still useful')
    assert ha.review_archives(cfg)['rows'][0]['state']=='retained'
    declare(cfg,p); del cfg['obsolete_items'][str(p)]['evidence']
    assert ha.review_archives(cfg)['rows'][0]['state']=='retained'


def test_protected_archive_stays_despite_obsolete_decision(tmp_path):
    cfg=config(tmp_path); p=payload(cfg); declare(cfg,p)
    row=ha.review_archives(cfg, protect=lambda p:'linked evidence')['rows'][0]
    assert row['state']=='held' and 'linked evidence' in row['reason']


def test_interrupted_enumeration_cannot_offer_any_retirement(tmp_path,monkeypatch):
    cfg=config(tmp_path); p=payload(cfg); declare(cfg,p)
    def broken(root):
        yield str(p.parent),[],[p.name]
        raise ha.ArchiveError('unreadable other branch')
    monkeypatch.setattr(ha,'_walk',broken)
    result=ha.review_archives(cfg)
    assert result['state']=='partial'
    assert not any(row['state']=='review retirement' for row in result['rows'])
    assert p.exists()


def test_periodic_review_updates_only_local_state_and_retries_holds(tmp_path):
    cfg=config(tmp_path); p=payload(cfg)
    before={str(q):q.read_bytes() for q in Path(cfg['archive_root']).rglob('*') if q.is_file()}
    calls=[]
    def scan():
        calls.append(True)
        return ha.review_archives(cfg)
    assert 'unknown usefulness retained' in ha.periodic_note(cfg,scan,now=1000000)
    assert ha.periodic_note(cfg,scan,now=1000001)=='' and len(calls)==1
    assert ha.periodic_note(cfg,scan,now=1000000+31*86400)
    assert before=={str(q):q.read_bytes() for q in Path(cfg['archive_root']).rglob('*') if q.is_file()}
    state=tmp_path/'.claude/diag/housekeep/task-archive-review.json'
    d=json.loads(state.read_text()); d['state']='partial'; state.write_text(json.dumps(d))
    assert ha.periodic_note(cfg,scan,now=d['checked_at']+2*86400)


def entry_config(tmp_path):
    spec=importlib.util.spec_from_file_location('archive_review_entry',REPO/'tools/housekeep.py')
    h=importlib.util.module_from_spec(spec); sys.modules[spec.name]=h; spec.loader.exec_module(h)
    return h,dict(h.DEFAULTS,**config(tmp_path))


def test_plan_only_offers_verified_obsolete_archive_and_rechecks_bytes(tmp_path):
    h,cfg=entry_config(tmp_path); p=payload(cfg)
    assert h.plan(cfg,only={'expire'})['actions']==[]
    declare(cfg,p)
    doc=h.plan(cfg,only={'expire'}); assert len(doc['actions'])==1
    action=doc['actions'][0]; old=p.stat()
    p.write_bytes(b'X'*old.st_size); os.utime(p,ns=(old.st_atime_ns,old.st_mtime_ns))
    assert 'bytes changed' in h.check(action,cfg,h.Guard(cfg),time.time())


def test_backup_group_label_cannot_hide_custom_or_linked_protection(tmp_path):
    h,cfg=entry_config(tmp_path); p=payload(cfg); declare(cfg,p)
    cfg['protected_globs']=[str(p).replace('\\','/').lower()]
    assert h.plan(cfg,only={'expire'})['actions']==[]
    cfg['protected_globs']=[]; guard=h.Guard(cfg); guard.links.add(h._key(p))
    assert h.archive_protection(guard,p)=='linked evidence'


def test_cross_game_save_and_profile_members_are_protected(tmp_path):
    for relative in ('save/save_001.xml.gz','save/quicksave.xml','Egosoft/X4/123/content.xml','copy.ess','copy.skse'):
        task=tmp_path/relative.replace('/','_')
        p=task/relative; p.parent.mkdir(parents=True); p.write_bytes(b'fake save/profile data')
        with pytest.raises(ha.ArchiveError):
            ha.archive_folder(task,tmp_path/'nas',lambda paths:pytest.fail('protected tree reached recycler'))
    assert not (tmp_path/'nas').exists()


def test_version_review_precedes_archive_retirement_and_fake_bin_keeps_bytes(tmp_path):
    h,cfg=entry_config(tmp_path); p=payload(cfg); declare(cfg,p)
    doc=h.plan(cfg,only={'expire'}); assert len(doc['actions'])==1
    def refuse(paths):
        pytest.fail('unapproved code reached recycler')
    assert h.apply(doc,cfg,recycler=refuse)==1 and p.exists()
    cfg['approved_sha256']=h.cleanup_code_sha256()
    expected=p.read_bytes(); dest=tmp_path/'fake-bin.zip'
    def fake_bin(paths):
        assert paths==[str(p)]
        p.rename(dest)
        return {str(p):None}
    assert h.apply(doc,cfg,recycler=fake_bin)==0
    assert not p.exists() and dest.read_bytes()==expected


def test_read_capability_probe_never_changes_source_metadata(tmp_path,monkeypatch):
    h,cfg=entry_config(tmp_path); p=payload(cfg)
    h._READS_RECORDED.clear()
    real=h.os.utime
    def only_owned_probe(path,*args,**kwargs):
        assert not Path(path).is_relative_to(Path(cfg['archive_root'])), 'read probe changed source metadata'
        return real(path,*args,**kwargs)
    monkeypatch.setattr(h.os,'utime',only_owned_probe)
    assert isinstance(h.reads_recorded(Path(cfg['archive_root'])),bool)


@pytest.mark.skipif(os.name!='nt',reason='Windows UNC boundary')
def test_read_probe_refuses_remote_temp_before_accessing_it(tmp_path,monkeypatch):
    base=tmp_path/'temp-base'; base.mkdir()
    remote=Path(chr(92)*2+'unapproved.invalid/share/temp')
    resolve,stat=Path.resolve,Path.stat
    monkeypatch.setattr(ha.tempfile,'gettempdir',lambda:str(base))
    monkeypatch.setattr(Path,'resolve',lambda p,*a,**k:remote if p==base else resolve(p,*a,**k))
    def local_only(p,*a,**k):
        if p==remote:
            pytest.fail('read probe attempted access to a remote temp directory')
        return stat(p,*a,**k)
    monkeypatch.setattr(Path,'stat',local_only)
    assert ha.reads_recorded(tmp_path,{}) is False


def test_hash_proof_preserves_a_concurrent_write_and_refuses_it(tmp_path,monkeypatch):
    h,cfg=entry_config(tmp_path); p=payload(cfg); old=p.stat()
    factory=h.hashlib.blake2b
    new_mtime=old.st_mtime_ns+123000000
    class ConcurrentWrite:
        def __init__(self,*a,**k):self.d=factory(*a,**k)
        def update(self,data):
            self.d.update(data)
            p.write_bytes(b'new bytes must survive')
            os.utime(p,ns=(old.st_atime_ns,new_mtime))
        def hexdigest(self):return self.d.hexdigest()
    monkeypatch.setattr(h.hashlib,'blake2b',ConcurrentWrite)
    with pytest.raises(Exception,match='changed during proof'):
        h.blake(p)
    assert p.read_bytes()==b'new bytes must survive'
    assert p.stat().st_mtime_ns==new_mtime
