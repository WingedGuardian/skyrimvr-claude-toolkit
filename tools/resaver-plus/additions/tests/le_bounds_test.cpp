// Version33 + private allocation-bound regressions. In-memory only.
#include "core/Papyrus.h"
#include "core/Structured.h"
#include <cstdio>
#include <functional>

using namespace ess;
namespace {
int pass = 0, fail = 0;
void Check(bool value, const char* name) {
    if (value) ++pass;
    else { ++fail; std::printf("FAIL: %s\n", name); }
}
void Roundtrip(bool le, const std::function<void(Papyrus&)>& populate, const char* label) {
    Papyrus p; p.strings = {"", "Synthetic", "string"}; p.saveFileVersion = 4;
    populate(p); p.reindex();
    Writer w; w.le = le; p.write(w);
    try {
        Reader r(w.buf.data(), w.buf.size()); r.le = le;
        Papyrus parsed; parsed.read(r);
        Writer again; again.le = le; parsed.write(again);
        Check(again.buf == w.buf && r.remaining() == 0, label);
    } catch (const std::exception& e) {
        std::printf("%s: %s\n", label, e.what()); Check(false, label);
    }
}
}
int main() {
    for (bool le : {false, true}) {
        {
            SaveFile s; s.header.version = le ? 8 : 12;
            GlobalData g; g.type = 1001; g.papyrus = std::make_unique<Papyrus>();
            g.papyrus->strings = {"", "Synthetic"}; g.papyrus->unknownIDs = {1, 2};
            s.table3.push_back(std::move(g));
            const auto info = structured::Query("info", s, structured::Parse("info", {}));
            Check(info.o.at("game").s == (le ? "SKYRIM_LE" : "SKYRIM_SE"), "structured format identity");
            Writer expected; expected.le = le; s.papyrus()->write(expected);
            const auto blocks = structured::Query("globaldata", s, structured::Parse("globaldata", {}));
            Check(blocks.o.at("blocks").a[0].o.at("size").n == expected.size(), "structured Papyrus length uses source format");
            SaveFile other; other.header.version = le ? 12 : 8;
            GlobalData second; second.type = 1001; second.papyrus = std::make_unique<Papyrus>(*s.papyrus());
            other.table3.push_back(std::move(second));
            const auto delta = structured::Query("globaldata-diff", s, structured::Parse("globaldata-diff", {"second"}), &other);
            Check(delta.o.at("changedCount").n == 1, "globaldata diff preserves each input's serialization width");
        }
        Roundtrip(le, [](auto& p) { p.unknownIDs = {0x123, 0x456}; }, "two unknown IDs with minimal tail");
        Roundtrip(le, [](auto& p) { p.unbinds = {{1, 2}, {3, 4}}; }, "two queued unbinds with minimal tail");
        Roundtrip(le, [](auto& p) {
            Script s; s.name = 1;
            for (int i = 0; i < 64; ++i) s.members.push_back({2, 2});
            p.scripts.push_back(s);
        }, "dense script-member headers");
        Roundtrip(le, [](auto& p) {
            for (int i = 0; i < 32; ++i) {
                Script s; s.name = 1; p.scripts.push_back(s);
                ScriptInstance inst; inst.id = 100 + i; inst.script = 1; inst.data = InstanceData{};
                p.instances.push_back(inst);
                Reference ref; ref.id = 200 + i; ref.script = 1; ref.data = InstanceData{};
                p.references.push_back(ref);
                ArrayInfo arr; arr.id = 300 + i; arr.type = VType::kString; arr.hasData = true;
                p.arrays.push_back(arr);
            }
        }, "dense instance reference and array headers");
        Roundtrip(le, [](auto& p) {
            ArrayInfo arr; arr.id = 1; arr.type = VType::kString; arr.hasData = true; arr.length = 64;
            Variable v; v.type = VType::kString; v.value = 2; arr.vars.assign(64, v);
            p.arrays.push_back(arr);
        }, "dense string variable payload");
        // Exercise count admission at both widths before any allocation.
        for (int zeros = 0; zeros < 12; ++zeros) {
            Writer w; w.le = le; w.u16(0);
            if (le) { w.u16(0); } else { w.u32(0); }
            for (int i = 0; i < zeros; ++i) w.u32(0);
            w.u32(0xFFFFFFFF);
            Reader r(w.buf.data(), w.buf.size()); r.le = le;
            try { Papyrus p; p.read(r); Check(false, "impossible count rejected"); }
            catch (const FormatError&) { Check(true, "impossible count rejected"); }
        }
        for (bool unbind : {false, true}) {
            Papyrus p;
            if (unbind) p.unbinds = {{1, 2}}; else p.unknownIDs = {1};
            Writer w; w.le = le; p.write(w);
            w.buf.resize(w.buf.size() - 3); // remove save version and one ID/unbind byte
            Reader r(w.buf.data(), w.buf.size()); r.le = le;
            try { Papyrus parsed; parsed.read(r); Check(false, "truncated final sections rejected"); }
            catch (const FormatError&) { Check(true, "truncated final sections rejected"); }
        }
    }
    std::printf("LE/SE allocation bounds: %d passed, %d failed\n", pass, fail);
    return fail ? 1 : 0;
}
