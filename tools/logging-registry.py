#!/usr/bin/env python3
"""Read-only discovery of Skyrim logging controls, with explicit coverage and gaps.

Python 3.10+. YAML uses optional PyYAML; TOML uses tomllib (3.11+) or tomli.
No game, DLL, archive, save, log-content, or package-manager operations.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import time

VERSION = 1
EXTENSIONS = {'.ini', '.cfg', '.conf', '.config', '.json', '.yaml', '.yml', '.toml', '.psc', '.xml'}
EXCLUDED_DIRS = {'saves', 'saves-no-cosave', '__mo_saves', '.git', '__pycache__',
                 'node_modules', 'backups', 'backupcfgs', '.venv'}
BACKUP = re.compile(r'(?:\.bak|\.old|\.disabled|\.new|\.vortex_backup|\.backup)(?:\.|$)|\.pre-', re.I)
SENSITIVE = re.compile(r'password|passwd|secret|credential|authorization|api.?key|access.?token|auth.?token', re.I)
SAFE_STRINGS = {'trace', 'debug', 'info', 'information', 'warn', 'warning', 'error', 'err',
                'critical', 'fatal', 'off', 'none', 'disabled', 'enabled', 'verbose',
                'all', 'normal', 'true', 'false', 'on', 'yes', 'no'}
DECLARATION = re.compile(r'^\s*(bool|int|float|string)\s+(?:Property\s+)?([A-Za-z_]\w*)\s*(?:=\s*([^;\r\n]*))?', re.I)
ASSIGNMENT = re.compile(r'^\s*([^=:#\s][^=:]*?)\s*[=:]\s*(.*?)\s*$')
GAPS = [
    'Native DLL internals, compiled-out logging, console commands and undocumented controls are not inspected.',
    'BSA/BA2 contents and compiled PEX behavior are not inspected; loose PSC declarations do not prove deployed-script parity.',
    'MCM definitions/defaults are not live save values; script-built menus and saved properties remain unresolved.',
    'Configured file values do not establish precedence, reload semantics, VM bindings, DLL load success or runtime output.',
    'Explicit Data roots describe disk content only; MO2 virtual winners must be supplied as an externally resolved view.',
    'Only the named scan scopes are covered. A search finding no switch does not establish absence of logging.',
    'Log metadata is historical evidence of a file, not proof of current activity or ownership. Log contents are never read.',
]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def words(value):
    return re.sub(r'[^a-z0-9]+', ' ', re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', str(value)).lower()).split()


def category(key, ancestors=()):
    tokens = words(key)
    joined = ''.join(tokens)
    if any(t in {'log', 'logs', 'logger', 'logging', 'trace', 'tracing'} for t in tokens):
        return 'logging-candidate'
    if joined in {'loglevel', 'flushlevel', 'logfile', 'logpath', 'logdirectory', 'logfilename'}:
        return 'logging-candidate'
    if any(category(a) == 'logging-candidate' for a in ancestors):
        return 'logging-candidate'
    diagnostic = {'debug', 'diagnostic', 'diagnostics', 'diag'}
    if diagnostic.intersection(tokens) or any(diagnostic.intersection(words(a)) for a in ancestors):
        return 'debug-candidate'
    return None


def safe_value(value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if len(str(value)) < 30 and str(value) not in {'nan', 'inf', '-inf'} else '[redacted-number]'
    if isinstance(value, str) and value.lower() in SAFE_STRINGS:
        return value
    return '[redacted-string]'  # paths, tokens, templates and arbitrary strings never exported


def scalar(text):
    text = re.split(r'\s+[;#]', str(text).strip(), maxsplit=1)[0].strip().strip('"\'')
    if text.lower() in {'true', 'false'}:
        return text.lower() == 'true'
    if re.fullmatch(r'[-+]?\d{1,18}', text):
        return int(text)
    if re.fullmatch(r'[-+]?\d{1,12}\.\d{1,12}', text):
        return float(text)
    return safe_value(text)


def decode(raw):
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16')
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return raw.decode('cp1252')


def path_key(parts):
    return '.'.join(str(p) for p in parts)


def strip_psc_blocks(text):
    parts, offset = [], 0
    while True:
        start = text.find(';/', offset)
        if start < 0:
            parts.append(text[offset:])
            return ''.join(parts)
        parts.append(text[offset:start])
        end = text.find('/;', start + 2)
        end = len(text) if end < 0 else end + 2
        parts.append('\n' * text[start:end].count('\n'))
        offset = end


def extract(text, suffix, reference=False, mcm=False):
    """Return findings and warnings. Values and source lines are never copied wholesale."""
    entries, warnings = [], []
    seen = Counter()

    def add(parts, value, line=None, state='configured-file', kind=None):
        if any(SENSITIVE.search(str(p)) or re.search(r'(?:sk-|Bearer\s)', str(p), re.I) for p in parts):
            return
        kind = kind or category(parts[-1], parts[:-1])
        if not kind:
            return
        key = path_key(parts)
        seen[(key, state)] += 1
        entries.append({'key': key, 'occurrence': seen[(key, state)], 'line': line,
                        'value': safe_value(value), 'value_origin': 'reference-default' if reference else state,
                        'kind': kind, 'review': 'unreviewed', 'runtime_verified': False,
                        'reload': 'unknown', 'output': []})

    if suffix == '.psc':
        trace_calls = 0
        in_function = False
        # Block comments can contain declarations and old experiments; retain line numbering.
        text = strip_psc_blocks(text)
        for number, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith(';'):
                continue
            if re.search(r'\bDebug\.(Trace|TraceStack|TraceUser)\s*\(', line, re.I):
                trace_calls += 1
            match = DECLARATION.match(line)
            if not in_function and match and (match[1].lower() != 'string' or 'level' in words(match[2])):
                value = re.split(r'\s+(?:Auto|AutoReadOnly|Hidden|Conditional)\b', match[3] or '', flags=re.I)[0]
                add([match[2]], scalar(value) if match[3] is not None else None,
                    number, 'source-declaration')
            if re.match(r'^\s*(?:\w+(?:\[\])?\s+)?(?:Function|Event)\b', line, re.I):
                in_function = True
            elif re.match(r'^\s*End(?:Function|Event)\b', line, re.I):
                in_function = False
        if trace_calls:
            warnings.append({'kind': 'papyrus-trace-calls', 'count': trace_calls,
                             'note': 'Call sites in source; execution, gates and PEX parity unverified.'})
        return entries, warnings

    if suffix in {'.ini', '.cfg', '.conf', '.config'}:
        section = []
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith('[') and stripped.endswith(']'):
                section = [stripped[1:-1]]
                continue
            commented = stripped.startswith((';', '#'))
            candidate = stripped[1:].lstrip() if commented else stripped
            match = ASSIGNMENT.match(candidate)
            if match and re.match(r'^[A-Za-z_]', match[1]):
                add(section + [match[1].strip()], scalar(match[2]), number,
                    'commented-example' if commented else 'configured-file')
        return entries, warnings

    visited_nodes = 0
    def walk(node, parts=(), depth=0, stack=None):
        nonlocal visited_nodes
        visited_nodes += 1
        if visited_nodes > 50000:
            raise ValueError('container traversal exceeds 50000 nodes')
        if depth > 40:
            raise ValueError('nesting exceeds 40 levels')
        stack = stack or set()
        if isinstance(node, (dict, list)):
            if id(node) in stack:
                raise ValueError('recursive alias/container')
            stack = stack | {id(node)}
        if isinstance(node, dict):
            if mcm and isinstance(node.get('id'), str):
                label = ' '.join(str(node.get(k, '')) for k in ('id', 'text', 'help'))
                kind = category(label)
                if kind:
                    add(parts + (node['id'],), node.get('defaultValue', node.get('value')),
                        state='mcm-definition-default', kind=kind)
            for key, value in node.items():
                if SENSITIVE.search(str(key)):
                    continue
                walk(value, parts + (key,), depth + 1, stack)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, parts + (index,), depth + 1, stack)
        elif parts:
            add(parts, node)

    if suffix == '.xml':
        import xml.etree.ElementTree as ET
        if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', text, re.I):
            raise ValueError('XML DTDs/entities are not accepted')
        def xml_walk(node, parts=(), depth=0):
            nonlocal visited_nodes
            visited_nodes += 1
            if depth > 40 or visited_nodes > 50000:
                raise ValueError('XML depth/node limit')
            parts = parts + (node.tag.rsplit('}', 1)[-1],)
            if not len(node) and node.text and node.text.strip():
                add(parts, scalar(node.text))
            named = node.attrib.get('key', node.attrib.get('name'))
            if named and 'value' in node.attrib:
                add(parts + (named,), scalar(node.attrib['value']))
            for key, value in node.attrib.items():
                if key not in {'key', 'name', 'value'}:
                    add(parts + (key,), scalar(value))
            for child in node:
                xml_walk(child, parts, depth + 1)
        xml_walk(ET.fromstring(text))
    elif suffix == '.json':
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate JSON key')
                result[key] = value
            return result
        walk(json.loads(text, object_pairs_hook=unique))
    elif suffix == '.toml':
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError as exc:
                raise ImportError('TOML needs Python 3.11+ or tomli; nothing installed automatically') from exc
        walk(tomllib.loads(text))
    elif suffix in {'.yaml', '.yml'}:
        try:
            import yaml
        except ImportError as exc:
            raise ImportError('YAML needs PyYAML; nothing installed automatically') from exc
        # safe_load performs no application-defined constructors. Duplicate keys are an error.
        class UniqueLoader(yaml.SafeLoader):
            pass
        def mapping(loader, node, deep=False):
            loader.flatten_mapping(node)
            result = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=deep)
                if key in result:
                    raise ValueError('duplicate YAML key')
                result[key] = loader.construct_object(value_node, deep=deep)
            return result
        UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        walk(yaml.load(text, Loader=UniqueLoader))
    else:
        raise ImportError('unsupported format; manual inspection required')
    return entries, warnings


class Scanner:
    def __init__(self, game, data, config=None, plugins=None, max_files=25000,
                 max_file_bytes=2 * 1024 * 1024, max_total_bytes=128 * 1024 * 1024):
        self.game, self.data = Path(game).resolve(), Path(data).resolve()
        self.config = Path(config).resolve() if config else None
        self.plugins = Path(plugins).resolve() if plugins else None
        self.max_files, self.max_file_bytes, self.max_total_bytes = max_files, max_file_bytes, max_total_bytes
        self.files, self.controls, self.components, self.logs, self.scopes, self.issues = [], [], {}, [], [], []
        self.accounting = Counter({key: 0 for key in (
            'candidate_files', 'file_limit_omissions', 'backup_files_excluded', 'duplicate_paths_omitted',
            'files_inspected', 'files_size-limit', 'files_total-byte-limit',
            'files_changed-or-size-limit', 'files_changed-during-read',
            'files_unsupported', 'files_read-or-parse-error')})
        self.visited, self.bytes_read = set(), 0
        self.active = None

    def component(self, kind, name, path, activation='unknown'):
        key = kind + ':' + name.casefold()
        self.components.setdefault(key, {'id': key, 'kind': kind, 'name': name,
                                        'path': path, 'activation': activation, 'control_ids': [],
                                        'coverage': 'no-control-discovered; unresolved'})
        return key

    def owner(self, ref):
        parts = ref.split('/')
        if ref.lower().startswith('data/mcm/config/') and len(parts) >= 4:
            return self.component('mcm-definition', parts[3], '/'.join(parts[:4]))
        if ref.startswith('data/SKSE/Plugins/') and len(parts) >= 4:
            name = parts[3] if len(parts) > 4 else Path(parts[3]).stem
            native = 'native-dll:' + name.casefold()
            if native in self.components:
                return native
        return self.component('file-group', '/'.join(parts[:-1]) or 'root', '/'.join(parts[:-1]))

    def read(self, path):
        stat = path.stat()
        if stat.st_size > self.max_file_bytes:
            return None, 'size-limit'
        if self.bytes_read + stat.st_size > self.max_total_bytes:
            return None, 'total-byte-limit'
        with path.open('rb') as stream:
            raw = stream.read(self.max_file_bytes + 1)
        self.bytes_read += len(raw)
        after = path.stat()
        if len(raw) > self.max_file_bytes or len(raw) != stat.st_size:
            return None, 'changed-or-size-limit'
        if (stat.st_mtime_ns, stat.st_size) != (after.st_mtime_ns, after.st_size):
            return None, 'changed-during-read'
        return raw, None

    def inspect_file(self, path, ref):
        if self.accounting['candidate_files'] >= self.max_files:
            self.accounting['file_limit_omissions'] += 1
            return
        self.accounting['candidate_files'] += 1
        row = {'path': ref, 'state': 'unread', 'sha256': None, 'controls': 0, 'warnings': []}
        self.files.append(row)
        owner = self.owner(ref)
        row['component'] = owner
        try:
            raw, problem = self.read(path)
            if problem:
                row['state'] = problem
                return
            row['sha256'] = digest(raw)
            row['bytes'] = len(raw)
            suffix = path.suffix.lower()
            if path.name.lower().endswith('.config.txt'):
                suffix = '.config'
            reference = path.name.lower() in {'settingsdefault.json', 'defaults_manifest.json'} or bool(
                re.search(r'\.(?:default|example|sample|template)\.', path.name, re.I))
            findings, warnings = extract(decode(raw), suffix, reference,
                                         ref.lower().startswith('data/mcm/config/'))
            row['state'], row['warnings'] = 'inspected', warnings
            for entry in findings:
                entry['source'] = ref
                entry['component'] = owner
                entry['source_sha256'] = row['sha256']
                identity = [ref, entry['key'], entry['occurrence'], entry['value_origin']]
                entry['id'] = digest(json.dumps(identity).encode())[:20]
                self.controls.append(entry)
                self.components[owner]['control_ids'].append(entry['id'])
            row['controls'] = len(findings)
        except ImportError as exc:
            row['state'] = 'unsupported'
            row['warnings'].append({'kind': 'dependency-or-format', 'note': str(exc)})
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
            # Parser exception text can contain entire private input lines; only the class is exported.
            row['state'] = 'read-or-parse-error'
            row['warnings'].append({'kind': type(exc).__name__, 'note': 'Inspect original locally; input text withheld.'})
        except Exception as exc:
            # Third-party YAML exceptions are not all ValueError subclasses.
            row['state'] = 'read-or-parse-error'
            row['warnings'].append({'kind': type(exc).__name__, 'note': 'Input text withheld.'})

    def visit(self, root, prefix, recursive=True, logs=False):
        root = Path(root)
        scope = {'path': prefix, 'recursive': recursive, 'purpose': 'log-metadata' if logs else 'controls',
                 'state': 'missing', 'files_seen': 0, 'directories_excluded': 0, 'symlinks_excluded': 0}
        self.scopes.append(scope)
        if not root.is_dir():
            return
        scope['state'] = 'enumerated'
        pending = [(root, prefix)]
        while pending:
            folder, label = pending.pop()
            try:
                with os.scandir(folder) as stream:
                    entries = sorted(stream, key=lambda e: e.name.casefold())
            except OSError:
                scope['state'] = 'enumeration-errors'
                self.issues.append({'path': label, 'kind': 'directory-unreadable'})
                continue
            for item in entries:
                ref = label + '/' + item.name
                if item.is_symlink() or getattr(Path(item.path), 'is_junction', lambda: False)():
                    scope['symlinks_excluded'] += 1
                    continue
                try:
                    if item.is_dir(follow_symlinks=False):
                        if item.name.casefold() in EXCLUDED_DIRS:
                            scope['directories_excluded'] += 1
                        elif recursive:
                            pending.append((Path(item.path), ref))
                        continue
                    if not item.is_file(follow_symlinks=False):
                        continue
                    scope['files_seen'] += 1
                    path = Path(item.path)
                    if logs:
                        if re.search(r'(?:\.log(?:\.?\d+)?|\.jsonl|\.ndjson)(?:\.gz)?$', item.name, re.I):
                            stat = item.stat(follow_symlinks=False)
                            self.logs.append({'path': ref, 'bytes': stat.st_size,
                                              'mtime_ns': stat.st_mtime_ns, 'runtime_activity': 'unverified'})
                        continue
                    resolved = path.resolve()
                    if resolved in self.visited:
                        self.accounting['duplicate_paths_omitted'] += 1
                        continue
                    self.visited.add(resolved)
                    if BACKUP.search(item.name):
                        self.accounting['backup_files_excluded'] += 1
                        continue
                    if path.suffix.lower() in EXTENSIONS or item.name.lower().endswith('.config.txt'):
                        self.inspect_file(path, ref)
                except OSError:
                    self.issues.append({'path': ref, 'kind': 'metadata-unreadable'})

    def inventory(self):
        if self.plugins:
            try:
                raw, problem = self.read(self.plugins)
                if problem:
                    raise ValueError(problem)
                lines = [line.strip() for line in decode(raw).splitlines()
                         if line.strip() and not line.lstrip().startswith('#')]
                starred = any(line.startswith('*') for line in lines)
                self.active = {line.lstrip('*').casefold() for line in lines
                               if not starred or line.startswith('*')}
                self.issues.append({'kind': 'activation-evidence', 'note': 'Persisted list only; implicit masters and runtime load status not inferred.',
                                    'sha256': digest(raw), 'listed_active': len(self.active)})
            except (OSError, UnicodeError, ValueError):
                self.issues.append({'kind': 'activation-unreadable', 'note': 'Activation remains unknown.'})
        for folder, kind, suffixes in [(self.data, 'game-plugin', {'.esp', '.esm', '.esl'}),
                                       (self.data / 'SKSE/Plugins', 'native-dll', {'.dll'})]:
            if not folder.is_dir():
                self.issues.append({'kind': 'inventory-scope-missing', 'scope': kind})
                continue
            for path in sorted(folder.iterdir()):
                if path.is_file() and path.suffix.lower() in suffixes:
                    active = 'unknown'
                    if kind == 'game-plugin' and self.active is not None:
                        active = 'listed-active' if path.name.casefold() in self.active else 'not-listed; implicit-or-inactive-unknown'
                    self.component(kind, path.stem if kind == 'native-dll' else path.name,
                                   'data/' + path.relative_to(self.data).as_posix(), active)
        for pattern, kind in [('*.bsa', 'BSA'), ('*.ba2', 'BA2')]:
            self.accounting[kind + '_archives_uninspected'] = sum(1 for _ in self.data.glob(pattern))
        scripts = self.data / 'Scripts'
        self.accounting['loose_PEX_uninspected'] = sum(1 for _ in scripts.glob('*.pex'))
        mcm = self.data / 'MCM/Config'
        if mcm.is_dir():
            for path in sorted(mcm.iterdir()):
                if path.is_dir():
                    self.component('mcm-definition', path.name, 'data/MCM/Config/' + path.name)

    def scan(self, extra=()):
        began = time.monotonic()
        if not self.data.is_dir():
            raise ValueError('Data root must be an existing directory')
        self.inventory()
        self.visit(self.game, 'game', False)
        self.visit(self.data, 'data', False)
        for suffix in ('SKSE/Plugins', 'MCM', 'Interface', 'Scripts/Source', 'Source/Scripts',
                       'Config', 'NetScriptFramework'):
            self.visit(self.data / suffix, 'data/' + suffix)
        if self.config:
            self.visit(self.config, 'config', False)
            self.visit(self.config / 'JCUser', 'config/JCUser')
            self.visit(self.config / 'SKSE', 'config/SKSE', False)
            self.visit(self.config / 'SKSE', 'logs/SKSE', True, True)
            self.visit(self.config / 'Logs', 'logs/Papyrus', True, True)
        for name, path in extra:
            self.visit(path, 'extra/' + name)
        self.visit(self.game, 'logs/game', False, True)
        self.visit(self.data / 'SKSE/Plugins/SkyrimNet/logs', 'logs/SkyrimNet', True, True)
        for component in self.components.values():
            if component['control_ids']:
                component['coverage'] = 'candidates-found; runtime-unverified'
        self.accounting.update(Counter('files_' + row['state'] for row in self.files))
        self.accounting['bytes_read'] = self.bytes_read
        self.accounting['controls'] = len(self.controls)
        self.accounting['logging_candidates'] = sum(c['kind'] == 'logging-candidate' for c in self.controls)
        self.accounting['debug_candidates'] = sum(c['kind'] == 'debug-candidate' for c in self.controls)
        fingerprint = digest(json.dumps({
            'files': [(f['path'], f['sha256'], f['state']) for f in self.files],
            'components': sorted((c['id'], c['path'], c['activation']) for c in self.components.values()),
            'authority': [i for i in self.issues if i['kind'].startswith('activation-')],
        }, sort_keys=True).encode())
        return {'schema_version': VERSION, 'scanner_sha256': digest(Path(__file__).read_bytes()),
                'generated_utc': datetime.now(timezone.utc).isoformat(),
                'elapsed_seconds': round(time.monotonic() - began, 3), 'input_fingerprint': fingerprint,
                'runtime_verified': False, 'scope': 'named loose-file scopes; no claim of complete modlist coverage',
                'limits': {'max_files': self.max_files, 'max_file_bytes': self.max_file_bytes,
                           'max_total_bytes': self.max_total_bytes},
                'accounting': dict(self.accounting), 'gaps': GAPS,
                'scopes': self.scopes, 'issues': self.issues, 'files': self.files,
                'components': sorted(self.components.values(), key=lambda c: c['id']),
                'controls': self.controls, 'log_files': self.logs}


def annotate(registry, path):
    """Reviews are bound to source bytes; stale reviews cannot establish semantics."""
    notes = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if notes.get('schema_version') != VERSION or not isinstance(notes.get('reviews'), list):
        raise ValueError('Invalid reviews schema')
    registry['reviews'] = {'applied': 0, 'stale': 0, 'unmatched': 0}
    for review in notes['reviews']:
        matches = [c for c in registry['controls'] if c['source'] == review['source'] and c['key'] == review['key']
                   and ('value_origin' not in review or c['value_origin'] == review['value_origin'])
                   and ('occurrence' not in review or c['occurrence'] == review['occurrence'])]
        if len(matches) > 1:
            raise ValueError('Ambiguous review; specify value_origin and occurrence')
        if not matches:
            registry['reviews']['unmatched'] += 1
        for control in matches:
            if control['source_sha256'] != review['source_sha256']:
                control['review'] = 'stale-review'
                registry['reviews']['stale'] += 1
                continue
            if not review.get('evidence'):
                raise ValueError('A review requires evidence')
            control['review'] = 'source-reviewed; runtime-unverified'
            for field in ('reload', 'output', 'meaning', 'evidence'):
                if field in review:
                    control[field] = review[field]
            registry['reviews']['applied'] += 1


def display(registry, term=''):
    matches = [c for c in registry['controls'] if term.casefold() in
               ' '.join(str(c.get(k, '')) for k in ('source', 'key', 'component')).casefold()]
    return {'total_controls': len(registry['controls']), 'matched': len(matches), 'controls': matches}


def markdown(registry):
    def cell(value):
        return html.escape(str(value)).replace('|', '&#124;').replace('\n', ' ')
    counts = registry['accounting']
    lines = ['# Logging registry', '',
             f"Generated {registry['generated_utc']}; scan {registry['elapsed_seconds']} seconds.", '',
             f"{counts.get('candidate_files', 0)} candidate files; {counts.get('files_inspected', 0)} inspected; "
             f"{len(registry['controls'])} candidate controls. Runtime verification: none.", '',
             'This is an inventory of configured files and source declarations. Debug candidates may control gameplay or overlays.', '',
             '## Coverage gaps', '']
    lines += ['- ' + gap for gap in registry['gaps']]
    lines += ['', '## Accounting', '', '```json', json.dumps(counts, indent=2), '```', '',
              '## Controls', '', '| Source | Key | Configured/default value | Origin | Classification | Review / reload |',
              '|---|---|---|---|---|---|']
    for c in registry['controls']:
        lines.append('| ' + ' | '.join(cell(x) for x in
                     (c['source'], c['key'], json.dumps(c['value']), c['value_origin'], c['kind'],
                      c['review'] + ' / ' + str(c['reload']))) + ' |')
    lines += ['', '## Scan exceptions', '']
    for row in registry['files']:
        if row['state'] != 'inspected':
            lines.append('- ' + cell(row['path']) + ': ' + cell(row['state']))
    for scope in registry['scopes']:
        if scope['state'] != 'enumerated':
            lines.append('- ' + cell(scope['path']) + ': ' + cell(scope['state']))
    lines += ['', 'Full component inventory, file hashes, exclusions, issues and log metadata are in registry.json.', '']
    return '\n'.join(lines)


def compare(old, new):
    def index(reg):
        return {c['id']: {k: c.get(k) for k in ('source', 'key', 'value', 'value_origin', 'review', 'reload')}
                for c in reg['controls']}
    left, right = index(old), index(new)
    old_components = {c['id']: c for c in old['components']}
    new_components = {c['id']: c for c in new['components']}
    return {'added': [right[k] for k in sorted(right.keys() - left.keys())],
            'removed': [left[k] for k in sorted(left.keys() - right.keys())],
            'changed': [{'before': left[k], 'after': right[k]} for k in sorted(left.keys() & right.keys()) if left[k] != right[k]],
            'components_added': [new_components[k] for k in sorted(new_components.keys() - old_components.keys())],
            'components_removed': [old_components[k] for k in sorted(old_components.keys() - new_components.keys())],
            'coverage_before': old['accounting'], 'coverage_after': new['accounting'],
            'scanner_changed': old.get('scanner_sha256') != new.get('scanner_sha256'),
            'input_changed': old['input_fingerprint'] != new['input_fingerprint']}


def write_outputs(registry, output, scanner, extra=()):
    output = Path(output).resolve()
    forbidden = [scanner.data, scanner.config] + [Path(p).resolve() for _, p in extra]
    if output == scanner.game or any(p and (output == p or p in output.parents) for p in forbidden):
        raise ValueError('Output must be outside scanned Data, config and extra roots, and not the game root itself')
    output.mkdir(parents=True, exist_ok=True)
    for name, content in [('registry.json', json.dumps(registry, indent=2, ensure_ascii=True) + '\n'),
                          ('registry.md', markdown(registry))]:
        target = output / name
        if target.is_symlink():
            raise ValueError('Refusing output-file symlink')
        temporary = output / (name + '.tmp-' + str(os.getpid()))
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
        os.replace(temporary, target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    scan = commands.add_parser('scan', help='Inventory exposed controls; never toggle them')
    scan.add_argument('--game-root', type=Path, required=True)
    scan.add_argument('--data-root', type=Path, help='Explicit externally resolved Data view; defaults to GAME/Data')
    scan.add_argument('--config-dir', type=Path)
    scan.add_argument('--plugins-txt', type=Path)
    scan.add_argument('--output-dir', type=Path, required=True)
    scan.add_argument('--extra-root', action='append', default=[], metavar='NAME=PATH')
    scan.add_argument('--reviews', type=Path, help='Optional source-hash-bound reviewed semantics')
    scan.add_argument('--max-files', type=int, default=25000)
    scan.add_argument('--max-file-bytes', type=int, default=2 * 1024 * 1024)
    scan.add_argument('--max-total-bytes', type=int, default=128 * 1024 * 1024)
    show = commands.add_parser('show', help='Search a saved registry')
    show.add_argument('registry', type=Path)
    show.add_argument('--filter', default='')
    diff = commands.add_parser('diff', help='Compare two saved registries')
    diff.add_argument('before', type=Path)
    diff.add_argument('after', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'show':
            result = display(json.loads(args.registry.read_text(encoding='utf-8')), args.filter)
        elif args.command == 'diff':
            result = compare(json.loads(args.before.read_text(encoding='utf-8')),
                             json.loads(args.after.read_text(encoding='utf-8')))
        else:
            if min(args.max_files, args.max_file_bytes, args.max_total_bytes) <= 0:
                raise ValueError('Scan limits must be positive')
            extra = []
            for entry in args.extra_root:
                name, separator, path = entry.partition('=')
                if not separator or not re.fullmatch(r'[A-Za-z0-9_-]+', name):
                    raise ValueError('Extra roots use NAME=PATH, with a simple name')
                extra.append((name, Path(path)))
            scanner = Scanner(args.game_root, args.data_root or args.game_root / 'Data',
                              args.config_dir, args.plugins_txt, args.max_files,
                              args.max_file_bytes, args.max_total_bytes)
            result = scanner.scan(extra)
            if args.reviews:
                annotate(result, args.reviews)
            write_outputs(result, args.output_dir, scanner, extra)
            result = {'schema_version': VERSION, 'elapsed_seconds': result['elapsed_seconds'],
                      'accounting': result['accounting'], 'components': len(result['components']),
                      'log_files': len(result['log_files']), 'gaps': result['gaps'],
                      'outputs': ['registry.json', 'registry.md']}
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print('logging-registry: ' + type(exc).__name__ + '; check arguments or input schema', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
