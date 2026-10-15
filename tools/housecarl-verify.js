// Read-only independent xEdit gate. No save, cleanup, activation or registry writes.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const [game, plugin, output] = process.argv.slice(2);
const base = process.env.HOUSECARL_XELIB_ROOT || path.resolve(__dirname, '..');
function parseErrorReports(entries) {
  const errors = [], warnings = [];
  for (const entry of entries) {
    const report = JSON.parse(entry);
    if (!report || !Array.isArray(report.errors)) throw Error('Unknown xEdit error report shape');
    for (const finding of report.errors) {
      // xeErrors.pas: groups 0/1 are ITM/ITPO, which can be intentional for a forwarded override.
      // Preserve them as warnings; every other/unknown group still blocks publication.
      (finding.group === 0 || finding.group === 1 ? warnings : errors).push(JSON.stringify(finding));
    }
  }
  return {errors, warnings};
}

async function main() {
  let x;
  try { x = require('xeditlib'); }
  catch (_) { x = require(path.join(base, 'tools/xelib/xelib')); }
  x.init(); x.setLanguage('English'); x.setGamePath(game + path.sep); x.setGameMode(x.GM_SSE);
  x.loadPlugins(plugin, true, false);
  await x.waitForLoader();
  const file = x.fileByName(plugin);
  const records = {};
  const lightFlags = new Map();
  function isLight(name) {
    if (!lightFlags.has(name)) {
      const descriptor = fs.openSync(path.join(game, 'Data', name), 'r');
      const header = Buffer.alloc(12);
      try {
        if (fs.readSync(descriptor, header, 0, 12, 0) !== 12) throw Error('Truncated plugin header');
      } finally { fs.closeSync(descriptor); }
      lightFlags.set(name, name.toLowerCase().endsWith('.esl') || !!(header.readUInt32LE(8) & 0x200));
    }
    return lightFlags.get(name);
  }
  for (const r of x.getRecords(file, '', true)) {
    const master = x.getMasterRecord(r) || r;
    const defining = x.name(x.getElementFile(master));
    const local = (x.getFormID(master, false) & (isLight(defining) ? 0xFFF : 0xFFFFFF)).toString(16).toLowerCase();
    const key = `${local}:${defining.toLowerCase()}`;
    if (Object.prototype.hasOwnProperty.call(records, key)) throw Error('Duplicate record key ' + key);
    const body = JSON.parse(x.elementToJson(r));
    records[key] = crypto.createHash('sha256').update(JSON.stringify(body)).digest('hex');
  }
  x.checkForErrors(file);
  const until = Date.now() + 60000;
  while (!x.getErrorThreadDone()) {
    if (Date.now() > until) throw Error('xEdit reference check timed out');
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  const {errors, warnings} = parseErrorReports(x.getErrors());
  fs.writeFileSync(output, JSON.stringify({complete:true, records, errors, warnings, count:Object.keys(records).length}, null, 2));
  console.log('RESULT: OK');
}
module.exports = {parseErrorReports};
if (require.main === module) main().catch(e => { console.error(String(e)); process.exitCode = 1; });
