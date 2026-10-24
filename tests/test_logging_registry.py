"""Behavioral checks against synthetic installs; never point these at a real game."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

TOOL = Path(__file__).resolve().parents[1] / 'tools/logging-registry.py'
SPEC = importlib.util.spec_from_file_location('logging_registry', TOOL)
registry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registry)


def put(root, relative, content):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    return target


@pytest.fixture
def install(tmp_path):
    game = tmp_path / 'game'
    data = game / 'Data'
    config = tmp_path / 'user-config'
    data.mkdir(parents=True)
    config.mkdir()
    put(data, 'SKSE/Plugins/Example.dll', 'not executed')
    put(data, 'SKSE/Plugins/NoSwitch.dll', 'not executed')
    put(data, 'Example.esp', 'not parsed')
    put(data, 'SKSE/Plugins/Example.ini', '[Logging]\nLogLevel=debug\n; LogLevel=trace\nApiKey=PRIVATE_SENTINEL\n')
    put(data, 'SKSE/Plugins/Example/SettingsDefault.json', '{"Logging":{"level":"info"}}')
    put(data, 'MCM/Config/Menu/config.json', json.dumps({'pages': [{'options': [
        {'id': 'recordEvents', 'text': 'Enable debug logging', 'defaultValue': False}]}]}))
    put(data, 'Scripts/Source/Example.psc', 'ScriptName Example\nbool bDebug = false\nDebug.Trace("PRIVATE_SENTINEL")\n')
    put(config, 'Skyrim.ini', '[Papyrus]\nbEnableLogging=1\nbEnableTrace=1\n')
    put(config, 'SKSE/Example.log', 'PRIVATE_LOG_CONTENT')
    return game, data, config


def scan(install, **kwargs):
    return registry.Scanner(*install[:2], config=install[2], **kwargs).scan()


def test_mixed_install_has_explicit_origins_and_unresolved_denominator(install):
    report = scan(install)
    native = [c for c in report['components'] if c['kind'] == 'native-dll']
    assert len(native) == 2
    assert next(c for c in native if c['name'] == 'NoSwitch')['coverage'].startswith('no-control-discovered')
    controls = report['controls']
    assert any(c['key'] == 'Logging.LogLevel' and c['value'] == 'debug' and c['value_origin'] == 'configured-file' for c in controls)
    assert any(c['value'] == 'trace' and c['value_origin'] == 'commented-example' for c in controls)
    assert any(c['value_origin'] == 'reference-default' for c in controls)
    mcm = next(c for c in controls if c['key'].endswith('recordEvents'))
    assert mcm['value_origin'] == 'mcm-definition-default' and mcm['value'] is False
    assert mcm['component'] == 'mcm-definition:menu'
    assert any(c['value_origin'] == 'source-declaration' for c in controls)
    assert all(c['runtime_verified'] is False for c in controls)
    assert report['accounting']['candidate_files'] == len(report['files'])
    assert sum(n for key, n in report['accounting'].items() if key.startswith('files_')) == len(report['files'])


def test_no_private_values_or_log_contents_escape(install):
    put(install[1], 'SKSE/Plugins/Privacy.json', json.dumps({
        'Logging': {'ApiKey': 'PRIVATE_SENTINEL', 'path': 'C:/PRIVATE_SENTINEL/output',
                    'format': 'PRIVATE_SENTINEL', 'level': 'trace'},
        'Credentials': {'Logging': {'level': 'PRIVATE_SENTINEL'}}}))
    encoded = json.dumps(scan(install))
    assert 'PRIVATE_SENTINEL' not in encoded
    assert 'PRIVATE_LOG_CONTENT' not in encoded
    assert '[redacted-string]' in encoded


def test_log_content_and_binary_are_never_opened(install, monkeypatch):
    original = Path.open
    def guarded(path, *args, **kwargs):
        if path.suffix in {'.dll', '.esp', '.log'}:
            pytest.fail('attempt to read forbidden content: ' + path.name)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    report = scan(install)
    assert report['log_files'][0]['bytes'] == len('PRIVATE_LOG_CONTENT')


@pytest.mark.parametrize('content,suffix', [('{"logLevel":1,"logLevel":2}', '.json'),
                                           ('logging:\n  level: trace\n  level: info\n', '.yaml')])
def test_duplicates_never_claim_effective_value(content, suffix):
    with pytest.raises(ValueError):
        registry.extract(content, suffix)


def test_yaml_alias_cycles_are_bounded():
    import yaml
    with pytest.raises((ValueError, yaml.constructor.ConstructorError)):
        registry.extract('logging: &cycle\n  next: *cycle\n', '.yaml')


def test_xml_controls_without_dtd_or_entity_expansion():
    entries, _ = registry.extract('<config><Logging><level>trace</level></Logging><add key="logLevel" value="debug" /></config>', '.xml')
    assert {c['value'] for c in entries} == {'trace', 'debug'}
    with pytest.raises(ValueError):
        registry.extract('<!DOCTYPE config [<!ENTITY secret SYSTEM "file:///private">]><config>&secret;</config>', '.xml')


def test_script_properties_exclude_comments_and_function_locals():
    entries, _ = registry.extract(';/\nbool bDebugOld = true\n/;\nbool Property bMusicDiag = false Auto\nFunction Test()\nbool debugTemporary = true\nstring log = "trace"\nEndFunction\n', '.psc')
    assert [(c['key'], c['value']) for c in entries] == [('bMusicDiag', False)]
    assert registry.extract(';/\nbool bDebug = true\n', '.psc')[0] == []


def test_parse_errors_caps_and_backups_remain_visible(install):
    put(install[1], 'SKSE/Plugins/Broken.json', '{"password":"PRIVATE_SENTINEL",')
    put(install[1], 'SKSE/Plugins/Old.ini.bak', 'logLevel=trace')
    put(install[1], 'SKSE/Plugins/Huge.ini', 'logLevel=trace\n' * 300)
    report = scan(install, max_file_bytes=1024)
    assert any(f['state'] == 'read-or-parse-error' for f in report['files'])
    assert any(f['state'] == 'size-limit' for f in report['files'])
    assert report['accounting']['backup_files_excluded'] == 1
    assert 'PRIVATE_SENTINEL' not in json.dumps(report)
    limited = scan(install, max_files=1)
    assert limited['accounting']['candidate_files'] == 1
    assert limited['accounting']['file_limit_omissions'] > 0


def test_sensitive_ancestors_and_debug_overlays_not_confirmed_as_logging():
    entries, _ = registry.extract(json.dumps({'Credentials': {'logLevel': 'trace'},
        'Debug': {'drawCollision': True}, 'Catalog': {'count': 3}}), '.json')
    assert len(entries) == 1
    assert entries[0]['kind'] == 'debug-candidate'


def test_reviews_apply_only_to_matching_source_bytes(install, tmp_path):
    report = scan(install)
    control = next(c for c in report['controls'] if c['key'] == 'Logging.LogLevel')
    review = {key: control[key] for key in ('source', 'key', 'source_sha256')}
    review['value_origin'] = 'configured-file'
    review.update(reload='restart', evidence='synthetic README', runtime_verified=True)
    path = put(tmp_path, 'reviews.json', json.dumps({'schema_version': 1, 'reviews': [review]}))
    registry.annotate(report, path)
    assert control['review'].startswith('source-reviewed') and not control['runtime_verified']
    put(install[1], 'SKSE/Plugins/Example.ini', '[Logging]\nLogLevel=info\n')
    updated = scan(install)
    registry.annotate(updated, path)
    changed = next(c for c in updated['controls'] if c['key'] == 'Logging.LogLevel')
    assert changed['review'] == 'stale-review' and changed['reload'] == 'unknown'


def test_refresh_ids_survive_line_insertions_and_diff_finds_value(install):
    before = scan(install)
    put(install[1], 'SKSE/Plugins/Example.ini', '; harmless heading\n[Logging]\nLogLevel=info\n; LogLevel=trace\n')
    after = scan(install)
    result = registry.compare(before, after)
    assert len(result['changed']) == 1
    assert result['changed'][0]['after']['value'] == 'info'
    assert not result['added'] and not result['removed']


def test_new_dll_with_no_switch_is_visible_to_refresh(install):
    before = scan(install)
    put(install[1], 'SKSE/Plugins/NewUnknown.dll', 'not opened')
    after = scan(install)
    difference = registry.compare(before, after)
    assert difference['input_changed']
    assert difference['components_added'][0]['name'] == 'NewUnknown'
    assert difference['components_added'][0]['coverage'].startswith('no-control-discovered')


def test_missing_yaml_dependency_is_reported_without_installing(install, monkeypatch):
    put(install[1], 'SKSE/Plugins/Example.yaml', 'logging:\n  level: trace\n')
    monkeypatch.setitem(sys.modules, 'yaml', None)
    report = scan(install)
    assert next(f for f in report['files'] if f['path'].endswith('Example.yaml'))['state'] == 'unsupported'


def test_config_output_refused_and_inputs_byte_identical(install, tmp_path):
    inputs = {p: p.read_bytes() for root in (install[0], install[2]) for p in root.rglob('*') if p.is_file()}
    scanner = registry.Scanner(*install[:2], config=install[2])
    report = scanner.scan()
    with pytest.raises(ValueError):
        registry.write_outputs(report, install[1] / 'output', scanner)
    registry.write_outputs(report, tmp_path / 'reports', scanner)
    assert all(p.read_bytes() == data for p, data in inputs.items())
    assert (tmp_path / 'reports/registry.md').is_file()


def test_cli_scan_search_and_diff(install, tmp_path):
    out = tmp_path / 'reports'
    cmd = [sys.executable, '-B', str(TOOL)]
    result = subprocess.run(cmd + ['scan', '--game-root', str(install[0]), '--config-dir', str(install[2]),
                                  '--output-dir', str(out)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['accounting']['controls'] > 0
    found = subprocess.run(cmd + ['show', str(out / 'registry.json'), '--filter', 'Example'],
                           capture_output=True, text=True, timeout=20)
    assert found.returncode == 0 and json.loads(found.stdout)['matched'] > 0
    diff = subprocess.run(cmd + ['diff', str(out / 'registry.json'), str(out / 'registry.json')],
                          capture_output=True, text=True, timeout=20)
    assert diff.returncode == 0 and json.loads(diff.stdout)['changed'] == []


def test_persisted_plugin_list_is_never_runtime_claim(install, tmp_path):
    authority = put(tmp_path, 'plugins.txt', '*Example.esp\nInactive.esp\n')
    report = registry.Scanner(*install[:2], config=install[2], plugins=authority).scan()
    plugin = next(c for c in report['components'] if c['name'] == 'Example.esp')
    assert plugin['activation'] == 'listed-active'
    assert not report['runtime_verified']


def test_symlink_does_not_escape_scope(install, tmp_path):
    outside = put(tmp_path, 'outside/Private.ini', 'LogLevel=trace')
    link = install[1] / 'SKSE/Plugins/Linked.ini'
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip('creating symlinks unavailable on this host')
    report = scan(install)
    assert not any(f['path'].endswith('Linked.ini') for f in report['files'])
    assert sum(s['symlinks_excluded'] for s in report['scopes']) == 1
