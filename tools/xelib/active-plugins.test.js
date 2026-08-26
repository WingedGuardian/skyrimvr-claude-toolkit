// Tests for the pure-JS half of active-plugins.js -- the parsing and path
// resolution, which need neither koffi nor XEditLib.dll.
//
// These also exist to make `npm test` mean something. Before this file,
// `node --test` found zero test files, printed nothing, and exited 0, so the
// CI job reported success having examined nothing at all.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { readActivePlugins, defaultPluginsTxt } = require('./active-plugins');

function tmpPluginsTxt(contents) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'toolkit-plugins-'));
  const file = path.join(dir, 'plugins.txt');
  fs.writeFileSync(file, contents, 'utf8');
  return file;
}

test('only *-prefixed lines are treated as active', () => {
  const file = tmpPluginsTxt(
    '*Skyrim.esm\nUnchecked.esp\n*Dawnguard.esm\n# a comment\n*Mod.esp\n'
  );
  assert.deepStrictEqual(readActivePlugins(file),
    ['Skyrim.esm', 'Dawnguard.esm', 'Mod.esp']);
});

test('CRLF line endings parse the same as LF', () => {
  // plugins.txt is written by Windows tooling and is routinely CRLF. Without
  // the \r being stripped, every plugin name would carry a trailing carriage
  // return and no lookup against it would ever match.
  const file = tmpPluginsTxt('*Skyrim.esm\r\n*Dawnguard.esm\r\n');
  assert.deepStrictEqual(readActivePlugins(file), ['Skyrim.esm', 'Dawnguard.esm']);
});

test('blank lines and stray whitespace are dropped', () => {
  const file = tmpPluginsTxt('*Skyrim.esm\n\n*  Spaced.esp  \n*\n');
  assert.deepStrictEqual(readActivePlugins(file), ['Skyrim.esm', 'Spaced.esp']);
});

test('an empty plugins.txt yields an empty list, not a crash', () => {
  assert.deepStrictEqual(readActivePlugins(tmpPluginsTxt('')), []);
});

test('a missing plugins.txt throws rather than silently returning nothing', () => {
  // Silence here would be the worst outcome: xelib would load zero plugins and
  // every answer derived from the load order would be confidently wrong.
  const missing = path.join(os.tmpdir(), 'toolkit-does-not-exist', 'plugins.txt');
  assert.throws(() => readActivePlugins(missing));
});

test('PLUGINS_TXT overrides path discovery', () => {
  const previous = process.env.PLUGINS_TXT;
  process.env.PLUGINS_TXT = 'X:/explicit/plugins.txt';
  try {
    assert.strictEqual(defaultPluginsTxt(), 'X:/explicit/plugins.txt');
  } finally {
    if (previous === undefined) delete process.env.PLUGINS_TXT;
    else process.env.PLUGINS_TXT = previous;
  }
});
