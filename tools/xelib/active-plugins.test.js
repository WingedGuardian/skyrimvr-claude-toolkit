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

const { readActivePlugins, defaultPluginsTxt, findPluginsTxt } = require('./active-plugins');

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

// --- multiple plugins.txt candidates ---------------------------------------
//
// Found live 2026-08-27 on the VR install these helpers were written for: BOTH
// %LOCALAPPDATA%\Skyrim VR\plugins.txt AND ...\Skyrim Special Edition\plugins.txt
// exist. The comment above says the SSE one "usually does NOT exist", and the
// probe silently returns the first hit -- so on that machine the correct file
// wins only because of the order the candidates happen to be listed in.
//
// The two files disagreed: the stale SSE copy (18 days older) had a different
// mod variant active. Loading it would not have failed -- it would have
// produced a complete, plausible, WRONG load order, which is the one outcome
// every tool downstream of this helper exists to prevent. Silence about a
// second candidate is therefore a bug, not a detail.

function fakeLocalAppData(games) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'toolkit-localappdata-'));
  for (const [game, contents] of Object.entries(games)) {
    fs.mkdirSync(path.join(base, game), { recursive: true });
    fs.writeFileSync(path.join(base, game, 'plugins.txt'), contents, 'utf8');
  }
  return base;
}

test('a single candidate is chosen with nothing else reported', () => {
  const base = fakeLocalAppData({ 'Skyrim VR': '*Skyrim.esm\n' });
  const found = findPluginsTxt(base);
  assert.strictEqual(found.chosen, path.join(base, 'Skyrim VR', 'plugins.txt'));
  assert.deepStrictEqual(found.others, []);
});

test('every rival candidate is reported, not just the winner', () => {
  const base = fakeLocalAppData({
    'Skyrim VR': '*Skyrim.esm\n',
    'Skyrim Special Edition': '*Skyrim.esm\n',
  });
  const found = findPluginsTxt(base);
  assert.strictEqual(found.chosen, path.join(base, 'Skyrim VR', 'plugins.txt'));
  assert.deepStrictEqual(found.others,
    [path.join(base, 'Skyrim Special Edition', 'plugins.txt')]);
});

test('no candidate at all still names a real location', () => {
  const base = fakeLocalAppData({});
  const found = findPluginsTxt(base);
  assert.strictEqual(found.chosen, path.join(base, 'Skyrim VR', 'plugins.txt'));
  assert.deepStrictEqual(found.others, []);
});

test('an ambiguous choice WARNS, naming both the winner and the loser', () => {
  const base = fakeLocalAppData({
    'Skyrim VR': '*Skyrim.esm\n',
    'Skyrim Special Edition': '*Skyrim.esm\n',
  });
  const previous = process.env.PLUGINS_TXT;
  delete process.env.PLUGINS_TXT;
  const warnings = [];
  const realWarn = console.warn;
  console.warn = (...a) => warnings.push(a.join(' '));
  try {
    defaultPluginsTxt(base);
  } finally {
    console.warn = realWarn;
    if (previous !== undefined) process.env.PLUGINS_TXT = previous;
  }
  const text = warnings.join('\n');
  assert.match(text, /Skyrim VR/,
    'the warning must name the file actually being used');
  assert.match(text, /Skyrim Special Edition/,
    'the warning must name the candidate being ignored');
  assert.match(text, /PLUGINS_TXT/,
    'the warning must say how to override the choice');
});

test('an unambiguous choice stays silent', () => {
  // A warning on every ordinary run is a warning nobody reads.
  const base = fakeLocalAppData({ 'Skyrim VR': '*Skyrim.esm\n' });
  const previous = process.env.PLUGINS_TXT;
  delete process.env.PLUGINS_TXT;
  const warnings = [];
  const realWarn = console.warn;
  console.warn = (...a) => warnings.push(a.join(' '));
  try {
    defaultPluginsTxt(base);
  } finally {
    console.warn = realWarn;
    if (previous !== undefined) process.env.PLUGINS_TXT = previous;
  }
  assert.deepStrictEqual(warnings, []);
});
