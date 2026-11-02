"""Read-only source check: compare actual sibling policy/helpers, not just local pins.

Pass the other toolkit's source root. Nothing is fetched, installed or modified.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def snapshot(root):
    root = Path(root).resolve()
    x4 = (root/'RETENTION.md').is_file()
    folder = root/('scripts' if x4 else 'tools')
    doc = root/('RETENTION.md' if x4 else 'docs/RETENTION.md')
    pin = root/('tools/x4validate/tests/test_retention_policy.py' if x4 else 'tests/test_retention_policy.py')
    paths = {'shared_policy':doc, 'archive_helper':folder/'housekeep_archive.py',
             'backup_helper':folder/'housekeep_backups.py', 'parity_checker':folder/'check-housekeep-parity.py',
             'pin_source':pin}
    raw = {key:p.read_bytes() for key,p in paths.items()}
    match = re.search(rb'<!-- shared-policy:begin -->\n(.*?)<!-- shared-policy:end -->',
                      raw['shared_policy'].replace(b'\r\n',b'\n'),re.S)
    if not match:
        raise ValueError('shared policy markers missing: '+str(doc))
    nodes = [n for n in ast.parse(raw['pin_source']).body if isinstance(n,ast.Assign)
             and any(isinstance(t,ast.Name) and t.id=='PINNED' for t in n.targets)]
    if len(nodes)!=1 or not isinstance(nodes[0].value,ast.Constant) or not isinstance(nodes[0].value.value,str):
        raise ValueError('one literal policy pin required: '+str(pin))
    hashes = {key:sha(data) for key,data in raw.items()}
    # A multi-file observation must still describe current bytes at completion.
    if any(sha(p.read_bytes())!=hashes[key] for key,p in paths.items()):
        raise ValueError('peer inputs changed during comparison')
    return dict(root=str(root), files={key:dict(path=str(paths[key]),sha256=value) for key,value in hashes.items()},
                artifacts=dict(shared_policy=sha(match.group(1)),
                               **{k:hashes[k] for k in ('archive_helper','backup_helper','parity_checker')}),
                pin=nodes[0].value.value)


def compare(root, peer):
    expected = 4
    try:
        if Path(root).resolve()==Path(peer).resolve():
            raise ValueError('peer must be a distinct toolkit source root')
        own, other = snapshot(root), snapshot(peer)
    except (OSError, ValueError, SyntaxError) as exc:
        return dict(state='unavailable',compared=0,expected=expected,rows=[],errors=[str(exc)])
    rows = []
    for key,value in own['artifacts'].items():
        same = value==other['artifacts'][key]
        if key=='shared_policy':
            same = same and value==own['pin']==other['pin']
        rows.append(dict(artifact=key,own_sha256=value,peer_sha256=other['artifacts'][key],equal=same))
    return dict(state='equal' if all(r['equal'] for r in rows) else 'different',
                compared=len(rows),expected=expected,rows=rows,own=own,peer=other)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('peer',type=Path)
    args=p.parse_args()
    report=compare(Path(__file__).resolve().parent.parent,args.peer)
    print(json.dumps(report,indent=2))
    return 0 if report['state']=='equal' else 1


if __name__=='__main__':
    raise SystemExit(main())
