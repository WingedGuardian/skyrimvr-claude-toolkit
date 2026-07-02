// xelib loader diagnostic — captures the FULL failure picture that waitForLoader hides.
// waitForLoader only reports getExceptionMessage() (empty in our case); this also dumps
// the loader message log, exception stack, and globals, polling status the whole time.
const path = require('path');
const xelib = require('xelib');

function dumpAll(tag) {
    console.log(`\n----- ${tag} -----`);
    try { console.log('loaderStatus =', xelib.getLoaderStatus()); } catch (e) { console.log('status err', e.message); }
    try { console.log('exceptionMessage =', JSON.stringify(xelib.getExceptionMessage())); } catch (e) { console.log('excMsg err', e.message); }
    try { console.log('exceptionStack  =', JSON.stringify(xelib.getExceptionStack())); } catch (e) { console.log('excStack err', e.message); }
    try { console.log('messages =\n' + xelib.getMessages()); } catch (e) { console.log('messages err', e.message); }
}

try {
    xelib.init();
    xelib.setLanguage('English');
    xelib.setGamePath(process.env.GAME_ROOT || (path.resolve(__dirname, '..', '..') + path.sep));  // game root = two levels up from tools/xelib
    xelib.setGameMode(xelib.GM_SSE);
    xelib.clearMessages();

    console.log('=== globals after setup ===');
    try { console.log(xelib.getGlobals()); } catch (e) { console.log('getGlobals err', e.message); }
    try { console.log('GetGamePath(SSE) =', JSON.stringify(xelib.getGamePath(xelib.GM_SSE))); } catch (e) { console.log('getGamePath err', e.message); }

    // Minimal load: the 4 base masters only.
    console.log('\n=== loadPlugins (base masters only) ===');
    xelib.loadPlugins('Skyrim.esm\nUpdate.esm\nDawnguard.esm\nHearthFires.esm\nDragonborn.esm', false, false);

    let polls = 0;
    const t0 = Date.now();
    const poll = () => {
        const s = xelib.getLoaderStatus();
        polls++;
        if (polls % 4 === 0) console.log(`  poll ${polls}: status=${s} (${Date.now() - t0}ms)`);
        if (s === 2) { dumpAll('LOADER DONE (status 2)'); finishOK(); return; }
        if (s === 3) { dumpAll('LOADER FAILED (status 3)'); xelib.close(); process.exit(2); return; }
        if (Date.now() - t0 > 30000) { dumpAll('LOADER TIMEOUT (30s)'); xelib.close(); process.exit(3); return; }
        setTimeout(poll, 250);
    };
    function finishOK() {
        // Loader succeeded — prove a record resolves.
        try {
            const sky = xelib.fileByName('Skyrim.esm');
            const iron = xelib.getRecord(sky, 0x012EB7, true); // IronSword
            console.log('\nIronSword EDID =', xelib.getValue(iron, 'EDID'));
            xelib.release(iron); xelib.release(sky);
            console.log('RESULT: xelib WORKS');
        } catch (e) { console.log('record resolve err:', e.message); }
        xelib.close();
        process.exit(0);
    }
    poll();
} catch (e) {
    console.log('FATAL during setup:', e.message);
    dumpAll('FATAL');
    try { xelib.close(); } catch (_) {}
    process.exit(1);
}
