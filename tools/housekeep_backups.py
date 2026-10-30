"""Opt-in cold backup storage. Unknown usefulness never expires a backup.

Shared verbatim by the X4 and Skyrim housekeep entry points. Local retirement
always uses the caller's recycler. Remote pruning requires a separate exact
plan approval; session maintenance only checks and reports.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import time
import zipfile

DAY = 86400
DEFAULTS = dict(enabled=False, destination="", sources=[], min_age_days=30,
                keep_local=2, review_days=30, review_seconds=15, obsolete={})
INTERMEDIATES = {'.obj', '.pch', '.ilk', '.iobj', '.ipdb', '.idb', '.tlog', '.pyc'}


class Refused(ValueError):
    pass


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def file_hash(path, deadline=float('inf')):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            if time.monotonic() > deadline:
                raise TimeoutError('backup review time budget reached')
            h.update(block)
    return h.hexdigest()


def plain_path(path):
    """Refuse links/junctions at every existing component, including the root."""
    p = Path(path)
    if not p.is_absolute():
        raise Refused('backup paths must be absolute')
    for part in (p, *p.parents):
        if part.exists() or part.is_symlink():
            s = part.lstat()
            if stat.S_ISLNK(s.st_mode) or getattr(s, 'st_file_attributes', 0) & 0x400:
                raise Refused(f'link/reparse path: {part}')
    return p.resolve()


def settings(cfg):
    raw = cfg.get('backup_archive', {})
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise Refused('unknown/invalid backup_archive configuration')
    out = dict(DEFAULTS, **raw)
    if not out['enabled']:
        return out
    out['destination'] = str(plain_path(out['destination']))
    for key in ('min_age_days', 'keep_local', 'review_days', 'review_seconds'):
        if not isinstance(out[key], (int, float)) or isinstance(out[key], bool) or out[key] < 0:
            raise Refused(f'invalid {key}')
    if int(out['keep_local']) != out['keep_local'] or out['keep_local'] < 1:
        raise Refused('keep_local must retain at least one newest generation')
    if out['review_days'] < 1 or out['review_seconds'] <= 0:
        raise Refused('review interval/budget must be positive')
    if not isinstance(out['sources'], list) or not isinstance(out['obsolete'], dict):
        raise Refused('sources must be a list; obsolete must be a mapping')
    dest = Path(out['destination'])
    sources = []
    for source in out['sources']:
        if not isinstance(source, dict) or set(source) - {'path', 'reason', 'pattern'}:
            raise Refused('invalid backup source declaration')
        root = plain_path(source.get('path', ''))
        if not source.get('reason') or root.parent == root or root.name.lower() in {'data', 'saves', 'reference'}:
            raise Refused('source needs a backup purpose and a bounded backup-store path')
        if root.is_relative_to(dest) or dest.is_relative_to(root):
            raise Refused('backup source and destination overlap')
        if any(root.is_relative_to(Path(s['path'])) or Path(s['path']).is_relative_to(root) for s in sources):
            raise Refused('backup source roots overlap')
        sources.append(dict(path=str(root), reason=str(source['reason']), pattern=source.get('pattern', '*')))
    out['sources'] = sources
    return out


def inventory(folder, protect=lambda p: ''):
    """Whole directory generation; no silent exclusions from a backup."""
    root = plain_path(folder)
    rows = []
    newest = root.stat().st_mtime
    read = root.stat().st_atime
    for dp, dirs, files in os.walk(root, followlinks=False, onerror=lambda e: (_ for _ in ()).throw(e)):
        for name in dirs + files:
            p = Path(dp) / name
            plain_path(p)
            if name in {'.git', '.housekeep-keep'}:
                raise Refused(f'protected generation contains {name}')
            why = protect(p)
            if why:
                raise Refused(f'protected generation: {why}: {p}')
            s = p.stat()
            newest = max(newest, s.st_mtime)
            read = max(read, s.st_atime)
            if stat.S_ISDIR(s.st_mode):
                rows.append(dict(path=p.relative_to(root).as_posix()+'/', bytes=0,
                                 sha256=hashlib.sha256(b'').hexdigest(), directory=True))
                continue
            low = p.as_posix().lower()
            if low.endswith(('.ess', '.skse')) or ('/save/' in low and low.endswith(('.xml', '.xml.gz'))) or '/egosoft/x4/' in low:
                raise Refused('save/profile data needs its separate save policy')
            if not stat.S_ISREG(s.st_mode):
                raise Refused('non-regular backup member')
            sha = file_hash(p)
            after = p.stat()
            if (s.st_size, s.st_mtime_ns, s.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise Refused(f'backup changed while hashing: {p}')
            rows.append(dict(path=p.relative_to(root).as_posix(), bytes=s.st_size, sha256=sha))
    if not any(not r.get('directory') for r in rows):
        raise Refused('empty generation')
    return dict(files=sorted(rows, key=lambda r: r['path']), newest=newest, last_read=read)


def plan(cfg, protect=lambda p: '', now=None):
    c = settings(cfg)
    now = time.time() if now is None else now
    actions, kept, reserves, unselected = [], [], {}, {}
    if c['enabled']:
        for source in c['sources']:
            root = Path(source['path'])
            if not root.is_dir():
                kept.append(dict(path=str(root), reason='source unavailable'))
                continue
            candidates = []
            unselected[str(root)] = 0
            for p in root.iterdir():
                if p.name.startswith('.') or not p.is_dir() or not fnmatch.fnmatch(p.name, source['pattern']):
                    unselected[str(root)] += 1
                    continue
                try:
                    snap = inventory(p, protect)
                    candidates.append((snap['newest'], p, snap))
                except (OSError, Refused) as exc:
                    kept.append(dict(path=str(p), reason=str(exc)))
            candidates.sort(key=lambda x: (x[0], str(x[1])), reverse=True)
            reserves[str(root)] = [str(p) for _, p, _ in candidates[:int(c['keep_local'])]]
            for rank, (newest, p, snap) in enumerate(candidates):
                reason = ''
                if rank < c['keep_local']:
                    reason = 'newest local generation'
                elif newest > now - max(c['min_age_days'], cfg.get('active_hours', 48) / 24) * DAY:
                    reason = 'generation too recent/active'
                elif snap['last_read'] > now - cfg.get('active_hours', 48) * 3600:
                    reason = 'generation recently read'
                elif all(Path(f['path']).suffix.lower() in INTERMEDIATES for f in snap['files'] if not f.get('directory')):
                    reason = 'only disposable intermediates; cleanup separately, do not archive'
                if reason:
                    kept.append(dict(path=str(p), reason=reason))
                else:
                    actions.append(dict(path=str(p), source=str(root), purpose=source['reason'],
                        files=snap['files'], bytes=sum(f['bytes'] for f in snap['files']),
                        object_id=digest(snap['files']), newest=newest))
    return dict(v=1, kind='cold-backup-archive', created=now, config_sha256=digest(c),
                destination=c['destination'], actions=actions, kept=kept, local_reserves=reserves,
                unselected_entries=unselected)


def verify_object(path, files, deadline=float('inf')):
    with zipfile.ZipFile(path) as z:
        if len(z.namelist()) != len(files) or set(z.namelist()) != {r['path'] for r in files}:
            raise Refused('archive member population mismatch')
        for row in files:
            h = hashlib.sha256()
            size = 0
            with z.open(row['path']) as stream:
                while block := stream.read(1024 * 1024):
                    if time.monotonic() > deadline:
                        raise TimeoutError('backup review time budget reached')
                    size += len(block)
                    h.update(block)
            if size != row['bytes'] or h.hexdigest() != row['sha256']:
                raise Refused('archive member hash/size mismatch')


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def apply(doc, cfg, recycler, approved_sha256, protect=lambda p: ''):
    c = settings(cfg)
    if not c['enabled'] or digest(doc) != approved_sha256 or doc.get('config_sha256') != digest(c) or doc.get('kind') != 'cold-backup-archive':
        raise Refused('exact plan/config approval mismatch')
    dest = plain_path(c['destination'])
    result = []
    for source in {a['source'] for a in doc['actions']}:
        reserve = doc.get('local_reserves', {}).get(source, [])
        if len(reserve) < c['keep_local']:
            raise Refused('newest local reserve missing from plan')
        for p in reserve:
            if plain_path(p).parent != Path(source):
                raise Refused('local reserve outside backup store')
            inventory(p, protect)  # Empty/missing/protected reserves cannot authorize retirement.
    def checked_source(a):
        p = plain_path(a['path'])
        source = next((s for s in c['sources'] if p.parent == Path(s['path'])), None)
        if source is None or not fnmatch.fnmatch(p.name, source['pattern']):
            raise Refused('generation outside configured source')
        current = inventory(p, protect)
        if current['files'] != a['files'] or current['newest'] != a['newest'] or digest(a['files']) != a['object_id'] or sum(f['bytes'] for f in current['files']) != a['bytes']:
            raise Refused('backup changed since plan')
        return p
    # Refuse any already-stale member before the first archive or retirement.
    for a in doc['actions']:
        checked_source(a)
    for a in doc['actions']:
        p = checked_source(a)
        objects = dest / 'objects'
        objects.mkdir(parents=True, exist_ok=True)
        target = objects / (a['object_id'] + '.zip')
        if not target.exists():
            partial = target.with_suffix('.partial')
            # Exclusive creation refuses an interrupted/competing writer.
            with partial.open('xb') as stream:
                with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
                    for row in a['files']:
                        z.write(p / row['path'], row['path'])
            verify_object(partial, a['files'])
            if inventory(p, protect)['files'] != a['files']:
                raise Refused('backup changed during archive; original retained')
            if target.exists():
                raise Refused('competing archive writer; original retained')
            partial.rename(target)
        verify_object(target, a['files'])
        record_id = digest(dict(source=str(p), files=a['files']))
        record = dict(v=1, id=record_id, source=str(p), purpose=a['purpose'],
                      object_id=a['object_id'], files=a['files'], archive_sha256=file_hash(target),
                      archived_at=time.time(), usefulness='potentially useful; retain until proven obsolete')
        receipt = dest / 'records' / (record_id + '.json')
        if receipt.exists():
            old = json.loads(receipt.read_text(encoding='utf-8'))
            if any(old.get(k) != record[k] for k in ('source', 'object_id', 'files', 'archive_sha256')):
                raise Refused('existing receipt conflicts')
            record = old
        else:
            write_json(receipt, record)
        if json.loads(receipt.read_text(encoding='utf-8')) != record:
            raise Refused('remote receipt did not verify')
        # A small local receipt survives retirement; no source bytes removed before proof.
        local = p.parent / '.housekeep-archive-receipts' / (record_id + '.json')
        if not local.exists():
            write_json(local, dict(record, vault=str(dest)))
        if json.loads(local.read_text(encoding='utf-8')) != dict(record, vault=str(dest)):
            raise Refused('local restore receipt did not verify')
        if inventory(p, protect)['files'] != a['files']:
            raise Refused('backup changed before retirement')
        errors = recycler([str(p)])
        result.append(dict(source=str(p), receipt=str(local), archive=str(target),
                           bytes=a['bytes'], recycled=errors.get(str(p), 'no recycler result') is None,
                           error=errors.get(str(p), 'no recycler result')))
    return result


def review(cfg, budget=None):
    c = settings(cfg)
    rows = []
    if not c['enabled']:
        return rows
    dest = plain_path(c['destination'])
    if not dest.is_dir():
        return [dict(state='unavailable', reason='backup vault unavailable/not initialized')]
    deadline = time.monotonic() + (c['review_seconds'] if budget is None else budget)
    for path in sorted((dest / 'records').glob('*.json')):
        plain_path(path)
        record = json.loads(path.read_text(encoding='utf-8'))
        oid = record.get('object_id', '')
        rid = record.get('id', '')
        if any(len(v) != 64 or any(ch not in '0123456789abcdef' for ch in v) for v in (oid, rid)) or rid != path.stem:
            raise Refused('invalid archive object identity')
        if digest(record['files']) != oid or digest(dict(source=record['source'], files=record['files'])) != rid:
            raise Refused('archive manifest identity mismatch')
        target = dest / 'objects' / (oid + '.zip')
        try:
            tomb = dest / 'retired' / (record['id'] + '.json')
            if tomb.exists():
                plain_path(tomb)
                retired = json.loads(tomb.read_text(encoding='utf-8'))
                if retired.get('archive_sha256') != record['archive_sha256']:
                    raise Refused('retirement receipt mismatch')
                rows.append(dict(id=record['id'], state='retired', reason='explicitly approved obsolete backup'))
                continue
            plain_path(target)
            if file_hash(target, deadline) != record['archive_sha256']:
                raise Refused('archive checksum changed')
            state, why = 'retained', 'usefulness unknown/potential; age never expires backups'
            decision = c['obsolete'].get(record['id'], {})
            if decision.get('basis') == 'operator-confirmed-obsolete' and decision.get('reason') and decision.get('evidence'):
                state, why = 'review deletion', 'explicit obsolescence declaration; separate exact-plan approval required'
            rows.append(dict(id=record['id'], source=record['source'], archive=str(target), state=state,
                             reason=why, obsolescence=decision, archive_sha256=record['archive_sha256']))
        except TimeoutError:
            rows.append(dict(id=record['id'], state='deferred', reason='review time budget reached'))
        except (OSError, Refused) as exc:
            rows.append(dict(id=record['id'], state='failed', reason=str(exc)))
    return rows


def prune_plan(cfg):
    """Only explicit evidence-backed declarations; never infer uselessness from age."""
    c = settings(cfg)
    if not c['enabled']:
        raise Refused('backup archive disabled')
    rows = review(cfg, budget=float('inf'))
    if any(r['state'] in {'failed', 'unavailable', 'deferred'} for r in rows):
        raise Refused('vault verification incomplete; no deletion plan')
    retire = [r for r in rows if r['state'] == 'review deletion']
    by_object = {}
    for path in (Path(c['destination']) / 'records').glob('*.json'):
        rec = json.loads(path.read_text(encoding='utf-8'))
        by_object.setdefault(rec['object_id'], []).append(rec['id'])
    ids = {r['id'] for r in retire} | {r['id'] for r in rows if r['state'] == 'retired'}
    objects = []
    for oid, refs in by_object.items():
        p = Path(c['destination']) / 'objects' / (oid + '.zip')
        if set(refs) <= ids and p.exists():
            objects.append(dict(path=str(p), sha256=file_hash(p), record_ids=sorted(refs)))
    return dict(v=1, kind='obsolete-backup-prune', config_sha256=digest(c),
                records=retire, objects=sorted(objects, key=lambda r: r['path']))


def prune(doc, cfg, approved_sha256, remover=None):
    """Permanent NAS removal only under an exact, freshly revalidated approval."""
    if digest(doc) != approved_sha256 or doc != prune_plan(cfg):
        raise Refused('exact deletion plan/config/evidence approval mismatch')
    dest = Path(settings(cfg)['destination'])
    for row in doc['records']:
        tomb = dest / 'retired' / (row['id'] + '.json')
        if not tomb.exists():
            write_json(tomb, dict(row, retired_at=time.time(), approved_plan_sha256=approved_sha256))
    removed = []
    for row in doc['objects']:
        p = plain_path(row['path'])
        if p.parent != dest / 'objects' or file_hash(p) != row['sha256']:
            raise Refused('archive changed before removal')
        (remover or os.remove)(p)
        removed.append(str(p))
    return removed


def restore(receipt, destination):
    record = json.loads(Path(receipt).read_text(encoding='utf-8'))
    dest = plain_path(destination)
    if dest.exists():
        raise Refused('restore destination must be new')
    vault = plain_path(record['vault'])
    for key in ('id', 'object_id', 'archive_sha256'):
        value = record.get(key, '')
        if len(value) != 64 or any(ch not in '0123456789abcdef' for ch in value):
            raise Refused('invalid restore receipt identity')
    if (vault / 'retired' / (record['id'] + '.json')).exists():
        raise Refused('backup was explicitly retired as obsolete')
    target = vault / 'objects' / (record['object_id'] + '.zip')
    plain_path(target)
    if file_hash(target) != record['archive_sha256']:
        raise Refused('archive checksum changed')
    verify_object(target, record['files'])
    for row in record['files']:
        rel = PurePosixPath(row['path'])
        if rel.is_absolute() or '..' in rel.parts or '\\' in row['path'] or ':' in row['path']:
            raise Refused('unsafe archive member')
    dest.mkdir(parents=True)
    with zipfile.ZipFile(target) as z:
        for row in record['files']:
            p = dest / row['path']
            if row.get('directory'):
                p.mkdir(parents=True, exist_ok=True)
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open('xb') as out, z.open(row['path']) as stream:
                while block := stream.read(1024 * 1024):
                    out.write(block)
    return str(dest)


def periodic_note(cfg):
    """Session-governed reminder; never an unattended archive or deletion."""
    c = settings(cfg)
    if not c['enabled']:
        return ''
    state = Path(cfg['root']) / '.claude/diag/housekeep/backup-review.json'
    try:
        last = json.loads(state.read_text(encoding='utf-8'))
        interval = min(c['review_days'], 1) if any(r['state'] not in {'retained', 'retired'} for r in last.get('rows', [])) else c['review_days']
        if time.time() - last['checked_at'] < interval * DAY:
            return ''
    except FileNotFoundError:
        pass
    rows = review(cfg)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps(dict(checked_at=time.time(), rows=rows), indent=2)+'\n', encoding='utf-8')
    return 'cold backups: {} checked, {} need attention; unknown usefulness retained'.format(
        sum(r['state'] in {'retained', 'retired', 'review deletion'} for r in rows),
        sum(r['state'] not in {'retained', 'retired'} for r in rows))


def main(argv, cfg, recycler, protect=lambda p: ''):
    p = argparse.ArgumentParser(prog='housekeep backups')
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('report', 'plan'):
        q = sub.add_parser(name)
        if name == 'plan':
            q.add_argument('-o', '--output', required=True)
    q = sub.add_parser('apply')
    q.add_argument('plan')
    q.add_argument('--approved-plan-sha256', required=True)
    sub.add_parser('review')
    q = sub.add_parser('prune-plan')
    q.add_argument('-o', '--output', required=True)
    q = sub.add_parser('prune')
    q.add_argument('plan')
    q.add_argument('--approved-plan-sha256', required=True)
    q = sub.add_parser('restore')
    q.add_argument('receipt')
    q.add_argument('--to', required=True)
    a = p.parse_args(argv)
    try:
        if a.command in ('report', 'plan'):
            doc = plan(cfg, protect)
            if a.command == 'plan':
                Path(a.output).write_text(json.dumps(doc, indent=2)+'\n', encoding='utf-8')
            print(json.dumps(dict(plan=doc, approval_sha256=digest(doc)), indent=2))
        elif a.command == 'apply':
            rows = apply(json.loads(Path(a.plan).read_text(encoding='utf-8')), cfg, recycler, a.approved_plan_sha256, protect)
            print(json.dumps(rows, indent=2))
            return int(any(not r['recycled'] for r in rows))
        elif a.command == 'prune-plan':
            doc = prune_plan(cfg)
            Path(a.output).write_text(json.dumps(doc, indent=2)+'\n', encoding='utf-8')
            print(json.dumps(dict(plan=doc, approval_sha256=digest(doc)), indent=2))
        elif a.command == 'prune':
            print(json.dumps(prune(json.loads(Path(a.plan).read_text(encoding='utf-8')), cfg, a.approved_plan_sha256), indent=2))
        elif a.command == 'review':
            rows = review(cfg)
            print(json.dumps(rows, indent=2))
            return int(any(r['state'] in {'failed', 'deferred', 'unavailable'} for r in rows))
        else:
            print(restore(a.receipt, a.to))
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print('BACKUP ARCHIVE REFUSED: '+str(exc))
        return 2
