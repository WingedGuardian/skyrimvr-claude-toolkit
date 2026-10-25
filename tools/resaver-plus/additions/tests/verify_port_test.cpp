// In-memory adversarial tests for complete expected-result verification.
#include "core/Verify.h"
#include <cstdio>
#include <functional>
using namespace ess;
namespace {
int pass = 0, fail = 0;
void Check(bool ok, const char* name) {
    if (ok) ++pass; else { ++fail; std::printf("FAIL: %s\n", name); }
}
SaveFile Make() {
    SaveFile s; s.header.magic = "TESV_SAVEGAME"; s.header.version = 12;
    s.plugins.full = {"Synthetic.esm"}; s.formVersion = 78; s.plugins.hasLite = true;
    GlobalData p; p.type = 1001; p.papyrus = std::make_unique<Papyrus>();
    p.papyrus->strings = {"", "Script", "Var"}; p.papyrus->saveFileVersion = 4;
    Script sc; sc.name = 1; sc.members.push_back({2, 2}); p.papyrus->scripts.push_back(sc);
    ScriptInstance si; si.id = 0xF123456789ABCDEF; si.script = 1; si.data = InstanceData{};
    Variable v; v.type = VType::kInt; v.value = 42; si.data->vars.push_back(v);
    p.papyrus->instances.push_back(si); p.papyrus->reindex(); s.table3.push_back(std::move(p));
    GlobalData g; g.type = 42; g.raw = {1, 2, 3}; s.table1.push_back(std::move(g));
    ChangeForm f; f.refID.data = 0x400014; f.typeField = 8; f.version = 1;
    f.data = {3, 4}; f.length1 = 2; s.changeForms.push_back(f);
    s.formIDs = {0x14}; s.visitedWorldspaces = {3}; s.unknown3 = {9, 8}; s.cosave = {7, 6};
    return s;
}
void Mutation(const char* name, const std::function<void(SaveFile&)>& mutate) {
    auto a = Make(), b = Make(); mutate(b);
    Check(!verify::Expected(a, b).identical(), name);
}
}
int main() {
    // Impossible counts must fail before reserve(), even on tiny malformed input.
    // Each prefix reaches one successive top-level Papyrus collection count.
    for (int zeros = 0; zeros < 12; ++zeros) {
        Writer bad; bad.u16(0);
        for (int i = 0; i < zeros; ++i) bad.u32(0);
        bad.u32(0xFFFFFFFFu);
        Reader r(bad.buf.data(), bad.buf.size());
        try { Papyrus p; p.read(r); Check(false, "impossible count rejected before allocation"); }
        catch (const FormatError&) { Check(true, "impossible count rejected before allocation"); }
    }
    auto a = Make(), b = Make(); Check(verify::Expected(a, b).identical(), "equal full models pass");
    Mutation("same-count Papyrus variable corruption", [](auto& s){s.papyrus()->instances[0].data->vars[0].value++;});
    Mutation("Papyrus opaque tail corruption", [](auto& s){s.papyrus()->tail.push_back(9);});
    Mutation("changeform flags corruption", [](auto& s){s.changeForms[0].changeFlags++;});
    Mutation("changeform identity corruption", [](auto& s){s.changeForms[0].refID.data++;});
    Mutation("changeform version corruption", [](auto& s){s.changeForms[0].version++;});
    Mutation("same-size changeform body corruption", [](auto& s){s.changeForms[0].data[0]++;});
    Mutation("same-count global-data corruption", [](auto& s){s.table1[0].raw[1]++;});
    Mutation("global-data type corruption", [](auto& s){s.table1[0].type++;});
    Mutation("header screenshot corruption", [](auto& s){s.header.screenshot.push_back(9);});
    Mutation("header field corruption", [](auto& s){s.header.saveNumber++;});
    Mutation("plugin order/name corruption", [](auto& s){s.plugins.full[0] = "Other.esm";});
    Mutation("form table corruption", [](auto& s){s.formIDs[0]++;});
    Mutation("visited worlds corruption", [](auto& s){s.visitedWorldspaces[0]++;});
    Mutation("unknown body region corruption", [](auto& s){s.unknown3[0]++;});
    Mutation("unused file-location word corruption", [](auto& s){s.flt.unused[1]++;});
    Mutation("same-size co-save corruption", [](auto& s){s.cosave[1]++;});
    Mutation("missing co-save", [](auto& s){s.cosave.clear();});
    {
        auto present = Make(), absent = Make();
        present.cosave.clear(); present.hasCosave = true; absent.cosave.clear();
        Check(!verify::Expected(present, absent).identical(), "empty and absent co-saves are distinct");
    }
    Mutation("structural problem on reopening", [](auto& s){s.problems.push_back("synthetic damaged layout");});
    b.cosave[0]++; auto result = verify::Expected(a,b);
    Check(verify::Describe(result).find("cosave at byte 0") != std::string::npos, "diagnostic section and byte offset");
    std::printf("Expected-result regressions: %d passed, %d failed\n", pass, fail);
    return fail ? 1 : 0;
}
