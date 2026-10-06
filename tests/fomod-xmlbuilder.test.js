'use strict';
// Smoke test for the container's documented "Generate FOMOD XML" use (docs/container-vs-windows.md): the pinned
// fast-xml-parser imports under require(), its XMLBuilder builds a small FOMOD ModuleConfig that parses back, and the
// builder escapes comment and CDATA delimiters (GHSA-gh4j-gqv2-49f6, XMLBuilder injection, versions before the fix).
//
// Needs `npm ci` first. Without node_modules this SKIPS ("COULD NOT CHECK") unless TOOLKIT_REQUIRE_NODE_DEPS=1, which
// the CI `node` job sets so a missing install fails instead of passing vacuously. tests/test_node_fomod_smoke.py runs
// this file against the locked install AND against fast-xml-parser 4.3.6 (injectable), proving it can go red.
const test = require('node:test');
const assert = require('node:assert');

let fxp = null;
try { fxp = require('fast-xml-parser'); } catch (e) { fxp = null; }
const required = process.env.TOOLKIT_REQUIRE_NODE_DEPS === '1';
const opts = { skip: (!fxp && !required) ? 'COULD NOT CHECK: fast-xml-parser is not installed (run npm ci)' : false };

const FOMOD = {
  config: {
    '@_xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance',
    '@_xsi:noNamespaceSchemaLocation': 'http://qconsulting.ca/fo3/ModConfig5.0.xsd',
    moduleName: 'Test <&> Mod',
    installSteps: {
      '@_order': 'Explicit',
      installStep: {
        '@_name': 'Main',
        optionalFileGroups: {
          group: {
            '@_name': 'Options', '@_type': 'SelectExactlyOne',
            plugins: { plugin: { '@_name': 'Default', description: 'plain',
              files: { folder: { '@_source': 'Data', '@_destination': '', '@_priority': '0' } } } },
          },
        },
      },
    },
  },
};
const BUILD = { ignoreAttributes: false, attributeNamePrefix: '@_', format: true, suppressEmptyNode: true };

test('fast-xml-parser is installed and exports the builder, parser and validator', opts, () => {
  assert.ok(fxp, 'fast-xml-parser is not installed: run npm ci');
  for (const name of ['XMLBuilder', 'XMLParser']) assert.strictEqual(typeof fxp[name], 'function', name);
  assert.strictEqual(typeof fxp.XMLValidator.validate, 'function', 'XMLValidator.validate');
});

test('XMLBuilder builds a small FOMOD ModuleConfig that validates and parses back', opts, () => {
  assert.ok(fxp, 'fast-xml-parser is not installed: run npm ci');
  const xml = new fxp.XMLBuilder(BUILD).build(FOMOD);
  assert.strictEqual(fxp.XMLValidator.validate(xml), true);
  assert.match(xml, /<config xmlns:xsi="http:\/\/www\.w3\.org\/2001\/XMLSchema-instance"/);
  assert.match(xml, /<moduleName>Test &lt;&amp;&gt; Mod<\/moduleName>/);
  assert.match(xml, /<installStep name="Main">/);
  assert.match(xml, /<folder source="Data" destination="" priority="0"\/>/);
  const back = new fxp.XMLParser({ ignoreAttributes: false, attributeNamePrefix: '@_' }).parse(xml);
  assert.strictEqual(back.config.moduleName, 'Test <&> Mod');
  assert.strictEqual(back.config.installSteps.installStep['@_name'], 'Main');
  assert.strictEqual(back.config.installSteps.installStep.optionalFileGroups.group.plugins.plugin['@_name'], 'Default');
});

test('XMLBuilder cannot be made to inject elements through comment or CDATA text', opts, () => {
  assert.ok(fxp, 'fast-xml-parser is not installed: run npm ci');
  const names = { commentPropName: '#comment', cdataPropName: '__cdata' };
  const xml = new fxp.XMLBuilder({ ignoreAttributes: false, ...names })
    .build({ root: { '#comment': 'a--><injected/><!--b', '__cdata': 'x]]><injected/><![CDATA[y' } });
  const back = new fxp.XMLParser({ ignoreAttributes: false, ...names }).parse(xml);
  assert.ok(!('injected' in back.root), 'user text escaped its comment/CDATA and created an element: ' + xml);
});
