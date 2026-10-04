// Plan 6 Task 3 Step 1: xelib against the scratch bed (C1-C4, P32).
//
//   node xelib_probe.js load            C1: load the bed's vanilla order (masters may be locked)
//   node xelib_probe.js spike           C2: create/modify Data/Spike.esp and SaveFile it
//   node xelib_probe.js modify-update   C3/P32 (and C4 after unlock): edit Update.esm, SaveFile it
//
// Runs as a FILE (gotcha #11). Success is judged from the RESULT line on stdout, never from
// the exit code (gotcha #12). The xelib runtime is a COPY in C:/Temp/skyguard-ml2/tools/xelib/,
// and the game path is the scratch bed -- never the registry's real path.
'use strict';
const fs = require('fs');
const path = require('path');

const BED = 'C:/Temp/skyguard-ml2/game';
const DATA = BED + '/Data';
const xelib = require('C:/Temp/skyguard-ml2/tools/xelib/xelib.js');

function listing() {
    return fs.readdirSync(DATA).sort();
}

function plugins() {
    const txt = fs.readFileSync(process.env.PLUGINS_TXT || BED + '/plugins.txt', 'utf8');
    return txt.split(/\r?\n/).filter(l => l.startsWith('*')).map(l => l.slice(1).trim()).filter(Boolean);
}

async function main(mode) {
    const before = listing();
    const out = { mode };
    xelib.init();
    try {
        // ORDER MATTERS (MEASURED 2026-10-04): setGameMode first resolves the game path from the
        // REGISTRY and a later setGamePath is ignored -- the first run of this probe loaded the
        // REAL install (read-only, nothing saved; baseline verify: 0 of 9 changed). Path first,
        // then mode, then assert the resolved path BEFORE anything is loaded.
        xelib.setGamePath(BED + '/');
        xelib.setLanguage('English');
        xelib.setGameMode(xelib.GM_SSE);
        // getGamePath(mode) reads the REGISTRY whatever setGamePath said (MEASURED: it returned the
        // real install while DataPath was the bed), so the check reads the DataPath global.
        const gp = xelib.getGlobal('DataPath').replace(/\\/g, '/').toLowerCase();
        if (!gp.startsWith('c:/temp/skyguard-ml2/')) {
            throw new Error('SANDBOX: xelib DataPath is ' + gp + ' -- refusing to load');
        }
        out.dataPath = gp;
        const list = plugins();
        if (mode === 'spike' && !list.includes('Spike.esp')) list.push('Spike.esp');
        const existing = list.filter(p => fs.existsSync(path.join(DATA, p)));
        xelib.loadPlugins(existing.join('\n'), true, false);
        await xelib.waitForLoader(300000);
        out.loadOrder = xelib.getLoadOrder();
        out.loaded = [];
        for (const name of existing) {
            try { const h = xelib.fileByName(name); out.loaded.push([name, xelib.getRecordCount(h)]); }
            catch (e) { out.loaded.push([name, 'NOT LOADED']); }
        }
        if (mode === 'spike') {
            let h;
            try { h = xelib.fileByName('Spike.esp'); }
            catch (e) { h = xelib.addFile('Spike.esp'); }
            xelib.addMaster(h, 'Skyrim.esm');
            xelib.addElement(h, 'GLOB\\GLOB');   // a new record (SetValue on its absent EDID fails)
            xelib.setValue(xelib.getElement(h, 'File Header'), 'CNAM', 'skyguard-spike');
            xelib.saveFile(h);
            out.saved = 'Spike.esp';
        } else if (mode === 'modify-update') {
            const h = xelib.fileByName('Update.esm');
            xelib.setValue(xelib.getElement(h, 'File Header'), 'CNAM', 'skyguard-spike-' + Date.now());
            try { xelib.saveFile(h); out.saveResult = 'SaveFile returned ok'; }
            catch (e) { out.saveResult = 'SaveFile FAILED: ' + String(e.message).split('\n').slice(0, 2).join(' | '); }
        }
        try { out.messages = xelib.getMessages().split(/\r?\n/).filter(l => /error|denied|fail|sav/i.test(l)).slice(-15); }
        catch (e) { out.messages = ['getMessages failed']; }
    } catch (e) {
        out.error = String(e.message).split('\n').slice(0, 3).join(' | ');
    } finally {
        try { xelib.close(); } catch (e) { /* ignore */ }
    }
    const after = listing();
    out.newFiles = after.filter(f => !before.includes(f));
    out.goneFiles = before.filter(f => !after.includes(f));
    console.log('RESULT: ' + JSON.stringify(out));
}

main(process.argv[2] || 'load').catch(e => console.log('RESULT: ' + JSON.stringify({ fatal: String(e) })));
