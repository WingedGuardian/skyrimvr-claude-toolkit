"""Ordinary archives: review unknown value forever; retire only exact obsolete bytes."""
import importlib.util
import hashlib
import json
import os
import time
import tempfile
import sys
import re
import subprocess
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


@pytest.mark.parametrize('occupied_suffix',['.zip','.zip.partial'])
def test_archive_never_overwrites_a_competing_publication(tmp_path,monkeypatch,occupied_suffix):
    src=tmp_path/'task'; src.mkdir(); (src/'unique.bin').write_bytes(b'only local copy')
    nas=tmp_path/'nas'; choose=ha.nas_target; foreign=[]
    def competing(root,name):
        target=choose(root,name)
        other=target if occupied_suffix=='.zip' else target.with_name(target.name+'.partial')
        other.write_bytes(b'other producer owns these bytes'); foreign.append(other)
        return target
    monkeypatch.setattr(ha,'nas_target',competing)
    with pytest.raises(ha.ArchiveError):
        ha.archive_folder(src,nas,lambda paths:pytest.fail('competing publication reached recycler'))
    assert foreign[0].read_bytes()==b'other producer owns these bytes'
    assert (src/'unique.bin').read_bytes()==b'only local copy'
    assert not (src/ha.STUB).exists()


def test_published_archive_is_bound_to_the_verified_bytes(tmp_path,monkeypatch):
    src=tmp_path/'task'; src.mkdir(); (src/'unique.bin').write_bytes(b'only local copy')
    nas=tmp_path/'nas'
    def replace_after_publish(real):
        def mutate(a,b,*args,**kwargs):
            result=real(a,b,*args,**kwargs)
            if Path(b).parent==nas and str(b).endswith('.zip'):
                Path(b).write_bytes(b'competing replacement after publication')
            return result
        return mutate
    monkeypatch.setattr(ha.os,'rename',replace_after_publish(ha.os.rename))
    monkeypatch.setattr(ha.os,'replace',replace_after_publish(ha.os.replace))
    monkeypatch.setattr(ha.os,'link',replace_after_publish(ha.os.link))
    with pytest.raises(ha.ArchiveError):
        ha.archive_folder(src,nas,lambda paths:pytest.fail('unverified published bytes reached recycler'))
    assert (src/'unique.bin').read_bytes()==b'only local copy'
    assert not (src/ha.STUB).exists()


def test_archive_destination_inside_source_is_refused_before_any_write(tmp_path):
    src=tmp_path/'task'; src.mkdir(); (src/'unique.bin').write_bytes(b'only local copy')
    with pytest.raises(ha.ArchiveError):
        ha.archive_folder(src,src/'archive',lambda paths:pytest.fail('overlap reached recycler'))
    assert sorted(p.name for p in src.iterdir())==['unique.bin']


def planned_folder(tmp_path):
    h,cfg=entry_config(tmp_path)
    cfg.update(agent_output=[str(tmp_path/'out')],archive_enabled=True)
    src=tmp_path/'out/task'; src.mkdir(parents=True)
    p=src/'unique.bin'; p.write_bytes(b'potentially useful')
    os.utime(p,(time.time()-30*86400,)*2)
    action=dict(path=str(src),nas=str(Path(cfg['archive_root'])/'out'),
                fingerprint=h.fingerprint(src),bytes=p.stat().st_size,idle_days=30,closed=False,
                **{'class':'archive'})
    return h,cfg,src,action


def test_archive_action_cannot_redirect_the_configured_destination(tmp_path):
    h,cfg,src,a=planned_folder(tmp_path)
    a['nas']=str(tmp_path/'unapproved-destination')
    assert 'destination' in h.check(a,cfg,h.Guard(cfg),time.time())


def test_archive_action_cannot_select_an_unconfigured_source(tmp_path):
    h,cfg,src,a=planned_folder(tmp_path)
    cfg['agent_output']=[]
    assert 'source' in h.check(a,cfg,h.Guard(cfg),time.time())


def test_archive_action_cannot_invent_a_closure_marker(tmp_path):
    h,cfg,src,a=planned_folder(tmp_path)
    p=src/'unique.bin'; os.utime(p,(time.time()-30*86400,time.time()-86400))
    a.update(fingerprint=h.fingerprint(src),closed=True)
    assert h.check(a,cfg,h.Guard(cfg),time.time())


def test_configured_archive_scope_remains_eligible(tmp_path):
    h,cfg,src,a=planned_folder(tmp_path)
    assert h.check(a,cfg,h.Guard(cfg),time.time())==''


@pytest.mark.skipif(os.name!='nt',reason='Windows recycler admission')
@pytest.mark.parametrize('capacity,data',[(None,b'unique'),(0,b'')])
def test_unproven_bin_route_never_starts_the_shell(tmp_path,monkeypatch,capacity,data):
    h,cfg=entry_config(tmp_path); p=tmp_path/'payload.bin'; p.write_bytes(data)
    monkeypatch.setattr(h,'bin_capacity',lambda path:capacity)
    monkeypatch.setenv('HOUSEKEEP_REAL_BIN_IN_TESTS','1')  # past the test-harness refusal; the shell is faked
    monkeypatch.setattr(h.subprocess,'run',lambda *a,**k:pytest.fail('unproven recycle route started a shell'))
    result=h.recycle([str(p)])
    assert result[str(p)] and p.read_bytes()==data


@pytest.mark.skipif(os.name!='nt',reason='Windows shell contract')
def test_recycler_requests_no_error_ui_and_retains_permanent_delete_warning(tmp_path,monkeypatch):
    h,cfg=entry_config(tmp_path); p=tmp_path/'payload.bin'; p.write_bytes(b'unique')
    monkeypatch.setattr(h,'bin_capacity',lambda path:1<<30)
    monkeypatch.setenv('HOUSEKEEP_REAL_BIN_IN_TESTS','1')  # past the test-harness refusal; the shell is faked
    def fake_shell(argv,**kwargs):
        expression=re.search(r'fFlags\s*=\s*([^;}]+)',argv[-1]).group(1)
        flags=0
        for number in re.findall(r'0x[0-9a-fA-F]+',expression):flags|=int(number,16)
        assert flags&0x400 and flags&0x4000 and flags&0x40
        assert kwargs['creationflags']&0x08000000
        return subprocess.CompletedProcess(argv,0,stdout='ERR\t'+str(p)+'\ttest refusal\n',stderr='')
    monkeypatch.setattr(h.subprocess,'run',fake_shell)
    assert h.recycle([str(p)])[str(p)]=='test refusal'
    assert p.exists()


@pytest.mark.skipif(os.name!='nt',reason='Windows child-process contract')
@pytest.mark.parametrize('method',['tracked_files','worktree_count'])
def test_repository_metadata_children_are_hidden(tmp_path,monkeypatch,method):
    h,cfg=entry_config(tmp_path)
    def hidden(argv,**kwargs):
        assert kwargs['creationflags']&0x08000000
        empty='' if kwargs.get('text') else b''
        return subprocess.CompletedProcess(argv,0,stdout=empty,stderr=empty)
    monkeypatch.setattr(h.subprocess,'run',hidden)
    getattr(h,method)(tmp_path)


def test_partial_archive_review_is_visible_and_not_reported_complete(tmp_path,monkeypatch,capsys):
    h,cfg=entry_config(tmp_path)
    monkeypatch.setattr(h,'review_archives',lambda *a,**k:dict(state='partial',root=cfg['archive_root'],
        rows=[],held=[dict(path=cfg['archive_root'],reason='unreadable archive branch')]))
    doc=h.plan(cfg,only={'expire'})
    assert doc['coverage']['state']=='accounted_with_holds'
    assert doc['coverage']['unmeasured_held_bytes'] and not doc['actions']
    h.print_coverage(doc)
    output=capsys.readouterr().out
    assert 'ordinary archive review: partial' in output and 'HELD ARCHIVES' in output


def test_cli_version_is_read_only_and_names_the_current_cleanup_code(tmp_path,capsys):
    h,cfg=entry_config(tmp_path); path=tmp_path/'config.json'
    path.write_text(json.dumps(cfg,default=str))
    assert h.main(['--config',str(path),'version'])==0
    assert capsys.readouterr().out.strip()==h.cleanup_code_sha256()
    assert list(tmp_path.iterdir())==[path]
