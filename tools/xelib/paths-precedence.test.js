// Which plugins.txt a tool reads -- the JS half of a parity check.
//
// tests/fixtures/paths_precedence.json is ONE table read by this file AND by tests/test_paths_precedence.py
// (skyguard.paths.plugins_txt), so the Node helper and the Python guard engine cannot drift apart on the
// precedence: PLUGINS_TXT > SKYRIM_LOADORDER_DIR env > paths file > LOCALAPPDATA probe (Codex handoff item 4).

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { defaultPluginsTxt } = require('./active-plugins');

const fixture = JSON.parse(fs.readFileSync(
  path.join(__dirname, '..', '..', 'tests', 'fixtures', 'paths_precedence.json'), 'utf8'));

const KEYS = ['PLUGINS_TXT', 'SKYRIM_LOADORDER_DIR', 'SKYRIM_PATHS_FILE', 'LOCALAPPDATA'];

function norm(p) {
  const n = path.normalize(p);
  return process.platform === 'win32' ? n.toLowerCase() : n;
}

for (const c of fixture.cases) {
  test('plugins.txt precedence: ' + c.id, () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'toolkit-precedence-'));
    const t = tmp.replace(/\\/g, '/');
    const sub = s => s.split('{tmp}').join(t);
    const local = path.join(tmp, 'local');
    fs.mkdirSync(local, { recursive: true });
    for (const game of c.probe) {
      fs.mkdirSync(path.join(local, game), { recursive: true });
      fs.writeFileSync(path.join(local, game, 'plugins.txt'), '*Skyrim.esm\n', 'utf8');
    }
    const pf = path.join(tmp, 'skyrim-paths.env');
    fs.writeFileSync(pf, sub(c.paths_file), 'utf8');
    const saved = {};
    for (const k of KEYS) { saved[k] = process.env[k]; delete process.env[k]; }
    const realWarn = console.warn;
    console.warn = () => {};
    try {
      process.env.LOCALAPPDATA = local;
      process.env.SKYRIM_PATHS_FILE = pf;
      for (const [k, v] of Object.entries(c.env)) process.env[k] = sub(v);
      if (c.want === 'error') {
        assert.throws(() => defaultPluginsTxt());
      } else {
        assert.strictEqual(norm(defaultPluginsTxt()), norm(sub(c.want)));
      }
    } finally {
      console.warn = realWarn;
      for (const [k, v] of Object.entries(saved)) {
        if (v === undefined) delete process.env[k]; else process.env[k] = v;
      }
    }
  });
}
