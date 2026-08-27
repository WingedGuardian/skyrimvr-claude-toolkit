// Tests for the pure half of skyrim-winner.js -- argument parsing and game-path
// normalisation. Neither needs koffi or XEditLib.dll, which is the whole reason
// the xelib require was made lazy: before that, importing this file on a machine
// without the DLL threw, so none of this could be covered at all.
//
// What cannot be covered here is the who-wins answer itself. That needs a real
// 659-plugin load order and the DLL, so it stays oracle-tested on a real install
// (a known FormID with a known winner). Pretending otherwise with mocks would
// test the mock.

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const { parseTarget, ensureTrailingSep } = require('./skyrim-winner.js');

test('a bare hex FormID parses', () => {
  assert.deepStrictEqual(parseTarget('0003418E'), { formID: 0x0003418E, plugin: null });
});

test('an 0x prefix is accepted', () => {
  assert.deepStrictEqual(parseTarget('0x0003418E'), { formID: 0x0003418E, plugin: null });
});

test('a short FormID is not zero-padded into a different record', () => {
  assert.deepStrictEqual(parseTarget('3418E'), { formID: 0x3418E, plugin: null });
});

test('a qualified Plugin.esp:HEX splits into plugin and id', () => {
  assert.deepStrictEqual(parseTarget('ShinsoJCE.esp:000871'),
    { formID: 0x871, plugin: 'ShinsoJCE.esp' });
});

test('a pipe separator works the same as a colon', () => {
  assert.deepStrictEqual(parseTarget('Skyrim.esm|0C367A'),
    { formID: 0x0C367A, plugin: 'Skyrim.esm' });
});

test('.esm and .esl are accepted, not just .esp', () => {
  assert.strictEqual(parseTarget('Dawnguard.esm:001234').plugin, 'Dawnguard.esm');
  assert.strictEqual(parseTarget('Tiny.esl:000801').plugin, 'Tiny.esl');
});

test('a malformed FormID is REJECTED, never sanitised into a valid one', () => {
  // The bug this guards: stripping the stray characters out of
  // "Shinso.esp/000843" leaves "esp000843", which parses as hex and names a
  // real -- and completely different -- record. The tool then prints
  // "RESULT: OK" for a lookup nobody asked for. A wrong answer delivered
  // confidently is worse than no answer.
  for (const bad of ['Shinso.esp/000843', 'not-hex', '000871!', 'Shinso.txt:0871', '']) {
    assert.throws(() => parseTarget(bad), /unparseable FormID|no FormID given/,
      `"${bad}" must be rejected rather than coerced`);
  }
});

test('a FormID longer than 8 hex digits is rejected', () => {
  assert.throws(() => parseTarget('0123456789'), /unparseable FormID/);
});

test('the game path is given a trailing separator when it lacks one', () => {
  // Measured on a live install: XEditLib throws "SetGameMode failed" on the
  // NEXT call when setGamePath got an unterminated path -- but only when the
  // path is set first. Set it after setGameMode and the same value is
  // tolerated, which is why sibling tools never tripped over it.
  assert.strictEqual(ensureTrailingSep('C:/Games/Skyrim'), 'C:/Games/Skyrim' + path.sep);
});

test('an already-terminated game path is left exactly as it is', () => {
  // Appending a second separator is its own bug, and the naive regex fix had it.
  assert.strictEqual(ensureTrailingSep('C:/Games/Skyrim/'), 'C:/Games/Skyrim/');
  const backslashed = 'C:' + String.fromCharCode(92) + 'Games' + String.fromCharCode(92);
  assert.strictEqual(ensureTrailingSep(backslashed), backslashed);
});
