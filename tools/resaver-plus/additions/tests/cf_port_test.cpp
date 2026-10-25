// Synthetic decoder regressions. No save files or game assets are required.
#include "core/ChangeFormData.h"
#include <cstdio>
#include <cstdlib>

using namespace ess;
namespace {
int passed = 0, failed = 0;
void Check(bool ok, const char* name) {
    if (ok) ++passed;
    else { ++failed; std::printf("FAIL: %s\n", name); }
}
void VS(Writer& w, std::uint32_t n) {
    if (n < 64) w.u8(static_cast<std::uint8_t>(n << 2));
    else if (n < 16384) w.u16(static_cast<std::uint16_t>((n << 2) | 1));
    else { n = (n << 2) | 2; w.u8(n); w.u8(n >> 8); w.u8(n >> 16); }
}
void Ref(Writer& w, std::uint32_t n = 0x400014) { RefID{n}.write(w); }
void Floats(Writer& w, int n) { for (int i = 0; i < n; ++i) w.f32(i + 0.25f); }
ChangeForm Form(const Writer& w, ChangeFormType type, std::uint32_t flags) {
    ChangeForm f; f.refID.data = 0x400014; f.typeField = static_cast<std::uint8_t>(type);
    f.changeFlags = flags; cf::SetBody(f, w.buf); return f;
}
bool Has(const cf::Node& n, const std::string& label) {
    if (n.label == label) return true;
    for (const auto& k : n.kids) if (Has(k, label)) return true;
    return false;
}
void Truncations(const SaveFile& s, const ChangeForm& f) {
    for (std::size_t n = 0; n < f.data.size(); ++n) {
        auto shortForm = f; shortForm.data.resize(n); shortForm.length1 = n;
        const auto d = cf::Decode(s, shortForm);
        Check(!d.complete && !d.error.empty() && d.consumed <= n, "truncated payload rejected within boundary");
    }
}
void Breadcrumb(Writer& w, std::uint32_t count) {
    w.u8(135); Floats(w, 3); Ref(w); w.u32(0x12345678); VS(w, count);
    for (std::uint32_t i = 0; i < count; ++i) {
        Floats(w, 3); Ref(w); Floats(w, 3); Ref(w); w.u8(0xA5);
    }
}
void Instances(Writer& w, bool populated) {
    w.u32(42); VS(w, populated ? 1 : 0);
    if (!populated) return;
    w.u32(7); VS(w, 1); w.u32(8); Ref(w);
    VS(w, 1); Ref(w); w.u32(9); w.u16(10); w.u8(11);
}
void RunData(Writer& w, int type) {
    w.u8(0); w.u32(0); w.u32(0); w.u8(1);
    w.u32(12); w.f32(0.5f); w.u32(1); w.i32(type);
    if (type == 3) w.i32(-123); else Ref(w);
}
}
int main() {
    SaveFile s;
    {
        Writer w; VS(w, 1); w.u8(16); w.u8(22); w.u8(23); w.u8(0); w.u8(33); Ref(w);
        auto f = Form(w, ChangeFormType::REFR, 1u << 6);
        const auto original = f.data; const auto d = cf::Decode(s, f);
        Check(d.complete && d.consumed == w.size(), "general type16 consumes four children");
        Check(Has(d.root, "ExtraExtraData4") && Has(d.root, "Ownership"), "type16 values exposed");
        Check(f.data == original, "decode preserves raw payload");
        Truncations(s, f);
        Writer nested; VS(nested, 1); nested.u8(16);
        nested.u8(4); nested.u8(22); nested.u8(8); nested.u8(0); nested.u8(23);
        nested.u8(12); nested.u8(0); nested.u8(0); nested.u8(0); nested.u8(22);
        auto nf = Form(nested, ChangeFormType::REFR, 1u << 6);
        Check(cf::Decode(s, nf).complete, "nested type16 groups consume exactly");
        Truncations(s, nf);
    }
    for (auto count : {0u, 2u, 64u}) {
        Writer w; VS(w, 2); Breadcrumb(w, count); w.u8(22);
        auto f = Form(w, ChangeFormType::REFR, 1u << 6); const auto d = cf::Decode(s, f);
        Check(d.complete && d.consumed == w.size(), "breadcrumb lengths and following element stay synchronized");
        Check(Has(d.root, "FollowerSwimBreadcrumbs") && Has(d.root, "Worn"), "breadcrumb and successor decoded");
        if (count <= 2) Truncations(s, f);
    }
    {
        Writer w; VS(w, 1); w.u8(135); Floats(w, 3); Ref(w); w.u32(0); VS(w, 100001);
        const auto d = cf::Decode(s, Form(w, ChangeFormType::REFR, 1u << 6));
        Check(!d.complete && d.error.find("implausible count") != std::string::npos, "excessive breadcrumb count rejected");
    }
    for (bool populated : {false, true}) for (bool already : {false, true}) {
        Writer w; Instances(w, populated); if (already) w.u8(1);
        auto f = Form(w, ChangeFormType::QUST, (1u << 27) | (already ? 1u << 26 : 0));
        auto d = cf::Decode(s, f);
        Check(d.complete && d.consumed == w.size() && Has(d.root, "QUEST_INSTANCES"), "quest instances precede already-run");
        Truncations(s, f);
    }
    for (int type = 0; type <= 4; ++type) {
        Writer w; RunData(w, type); Instances(w, true); w.u8(1);
        auto f = Form(w, ChangeFormType::QUST, (1u << 28) | (1u << 27) | (1u << 26));
        const auto d = cf::Decode(s, f);
        Check(d.complete && d.consumed == w.size(), "all valid run-data types followed by instances");
        Check(Has(d.root, type == 3 ? "UNK_INT" : "UNK_REF"), "run-data variant payload exposed");
        Truncations(s, f);
    }
    {
        Writer w; RunData(w, 5);
        Check(!cf::Decode(s, Form(w, ChangeFormType::QUST, 1u << 28)).complete, "unknown run-data type rejected");
        Writer p; Ref(p); Floats(p, 6); Ref(p); p.u16(0); p.u16(0);
        VS(p, 1); p.u8(140); VS(p, 1); Ref(p);
        const auto d = cf::Decode(s, Form(p, ChangeFormType::REFR, 1u << 25));
        Check(d.complete && d.consumed == p.size(), "promoted REFR has no duplicate promotion read");
        Writer tail; tail.u8(99);
        auto t = cf::Decode(s, Form(tail, ChangeFormType::QUST, 0));
        Check(!t.complete && t.consumed == 0, "unknown tail is explicitly incomplete");
    }
    std::printf("Decoder regressions: %d passed, %d failed\n", passed, failed);
    return failed ? EXIT_FAILURE : EXIT_SUCCESS;
}
