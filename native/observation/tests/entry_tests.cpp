// Native entry hook checks: synthetic fixtures only, no game code runs. Promoted from the Souls diagnostic's entry_tests,
// generalised so the same machinery is exercised through several consumer descriptors.
#include "skyrim_native/entry_hook.hpp"
#include "skyrim_native/forward.hpp"
#include <cstdio>
#include <initializer_list>
#include <stdexcept>
#include <thread>

extern "C" void EntryFrame(void*) noexcept(false);
extern "C" void EntryFrameB(void*) noexcept(false);
extern "C" void EntryFrameC(void*) noexcept(false);
extern "C" int EntryAbi(void (*)(void*), void*) noexcept(false);
using skyrim_native::EntryDescriptor;
using skyrim_native::EntryHook;
namespace {
constexpr std::array<std::uint8_t, 12> kUnwindA{1, 5, 3, 0, 5, 0xE0, 3, 0x70, 2, 0x60, 0, 0};
constexpr std::array<std::uint8_t, 12> kUnwindB{1, 5, 3, 0, 5, 0xE0, 3, 0x50, 2, 0x30, 0, 0};
constexpr std::array<std::uint8_t, 12> kUnwindC{1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0};
unsigned checks{}, failures{}, bodyCalls{}, beforeCalls{}, afterCalls{}, outerAfter{};
bool throwBody{}, throwObserver{};
void* expected{};
skyrim_native::ForwardCounts counts;
void (*entryOriginal)(void*){};
void (*savedOuterOriginal)(void*) = EntryFrame;
void check(bool value, const char* name) { ++checks; if (!value) { ++failures; std::printf("FAIL %s\n", name); } }
bool readMemory(std::uintptr_t at, void* out, std::size_t size) noexcept {
    SIZE_T copied = 0;
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(at), out, size, &copied) && copied == size;
}
void wrapper(void* arg) {
    skyrim_native::forwardOne(counts, arg, entryOriginal,
        [] { ++beforeCalls; SetLastError(0xFA110000u); if (throwObserver) throw std::runtime_error("before"); },
        [] { ++afterCalls; SetLastError(0xFA110001u); if (throwObserver) throw std::runtime_error("after"); });
}
void outer(void* arg) {
    savedOuterOriginal(arg);
    check(counts.returned == bodyCalls, "original-body after facts precede outer continuation");
    ++outerAfter;
}
void returnMarker() {}
void noDetour(void*) {}

// offset into the trampoline, and how many displaced PUSHes have run by then.
struct Boundary { unsigned offset, pushed; };

// Virtual unwind from every copied-instruction and continuation boundary of a prepared hook. `slots` are the three values
// the fixture pushes, and `saved` selects which registers each displaced PUSH saved.
void checkUnwind(const EntryHook& hook, std::initializer_list<Boundary> boundaries, DWORD64 CONTEXT::*const (&saved)[3]) {
    const auto trampoline = hook.trampoline;
    DWORD64 base = 0;
    const auto fn = RtlLookupFunctionEntry(trampoline + 5, &base, nullptr);
    check(fn != nullptr, "trampoline has registered unwind metadata");
    if (!fn) return;
    const bool shape = base == trampoline && fn->BeginAddress == 0 && fn->EndAddress == 19;
    check(shape, "trampoline unwind region excludes the leaf relay");
    if (!shape) return;
    for (const auto b : boundaries) {
        std::array<DWORD64, 5> stack{};
        std::size_t slot = 0;
        if (b.pushed > 2) stack[slot++] = 0x3333;
        if (b.pushed > 1) stack[slot++] = 0x2222;
        if (b.pushed > 0) stack[slot++] = 0x1111;
        stack[slot] = reinterpret_cast<DWORD64>(&returnMarker);
        CONTEXT context{};
        context.ContextFlags = CONTEXT_FULL;
        context.Rip = trampoline + b.offset;
        context.Rsp = reinterpret_cast<DWORD64>(stack.data());
        const DWORD64 originals[3]{0x1111, 0x2222, 0x3333};
        for (unsigned n = 0; n < 3; ++n)
            if (saved[n]) context.*saved[n] = b.pushed > n ? 0xAAAA + n : originals[n];
        context.R15 = 0x4444;
        context.R11 = b.offset == 15 ? hook.target + 5 : 0xFEED;
        PVOID handler = nullptr;
        DWORD64 establisher = 0;
        RtlVirtualUnwind(UNW_FLAG_NHANDLER, base, context.Rip, fn, &context, &handler, &establisher, nullptr);
        if (context.Rip != reinterpret_cast<DWORD64>(&returnMarker))
            std::printf("unwind offset=%u recovered_rip=%llx\n", b.offset, context.Rip);
        check(context.Rip == reinterpret_cast<DWORD64>(&returnMarker), "unwind recovers original return at each displaced instruction boundary");
        check(context.Rsp == reinterpret_cast<DWORD64>(stack.data() + slot + 1), "unwind recovers original stack at each displaced instruction boundary");
        bool preserved = context.R15 == 0x4444;
        for (unsigned n = 0; n < 3; ++n)
            if (saved[n]) preserved = preserved && context.*saved[n] == originals[n];
        check(preserved, "unwind preserves displaced nonvolatile saves");
    }
}

// A descriptor that is not this fixture's must not unwind correctly: a passing fixture proves nothing about another site.
void otherSiteProofs(std::uintptr_t target, std::array<std::uint8_t, 31> prefix) {
    EntryDescriptor wrongBytes{prefix, kUnwindA, true};
    wrongBytes.expected[6] ^= 1;
    EntryHook denied;
    check(!denied.prepare(target, reinterpret_cast<std::uintptr_t>(&noDetour), wrongBytes, readMemory) && !denied.created,
          "another site's bytes refuse before any hook exists");
    EntryDescriptor noClaim{prefix, kUnwindA, false};
    EntryHook unclaimed;
    check(!unclaimed.prepare(target, reinterpret_cast<std::uintptr_t>(&noDetour), noClaim, readMemory) &&
              unclaimed.status == EntryHook::kBadDescriptor, "a descriptor must claim R11 is not an input");
}
void descriptorProofs() {
    using skyrim_native::validDescriptor;
    check(validDescriptor({{}, kUnwindA, true}) && validDescriptor({{}, kUnwindB, true}) && validDescriptor({{}, kUnwindC, true}),
          "the qualified unwind shapes are accepted");
    auto bad = [](std::array<std::uint8_t, 12> u) { return !validDescriptor({{}, u, true}); };
    check(bad({2, 5, 3, 0, 5, 0xE0, 3, 0x70, 2, 0x60, 0, 0}), "unwind version refused");
    check(bad({9, 5, 3, 0, 5, 0xE0, 3, 0x70, 2, 0x60, 0, 0}), "unwind flags refused");
    check(bad({1, 6, 3, 0, 5, 0xE0, 3, 0x70, 2, 0x60, 0, 0}), "a prolog longer than the displaced bytes refused");
    check(bad({1, 5, 3, 3, 5, 0xE0, 3, 0x70, 2, 0x60, 0, 0}), "a frame register refused");
    check(bad({1, 5, 5, 0, 5, 0xE0, 3, 0x70, 2, 0x60, 0, 0}), "too many codes refused");
    check(bad({1, 5, 3, 0, 2, 0x60, 3, 0x70, 5, 0xE0, 0, 0}), "codes out of descending order refused");
    check(bad({1, 5, 3, 0, 6, 0xE0, 3, 0x70, 2, 0x60, 0, 0}), "a code past the prolog refused");
    check(bad({1, 5, 3, 0, 5, 0xE1, 3, 0x70, 2, 0x60, 0, 0}), "an operation other than PUSH refused");
    check(bad({1, 5, 1, 0, 5, 0xE0, 3, 0x70, 0, 0, 0, 0}), "bytes past the declared codes refused");
}

// Each extra shape: prepared, unwound at every boundary of that shape, then fully released. Never enabled, never run.
void checkShape(void (*fixture)(void*), const std::array<std::uint8_t, 12>& unwind, std::initializer_list<Boundary> boundaries,
                DWORD64 CONTEXT::*const (&saved)[3]) {
    const auto target = reinterpret_cast<std::uintptr_t>(fixture);
    std::array<std::uint8_t, 31> prefix{};
    check(readMemory(target, prefix.data(), prefix.size()), "read shape prefix");
    EntryHook hook;
    if (!hook.prepare(target, reinterpret_cast<std::uintptr_t>(&noDetour), EntryDescriptor{prefix, unwind, true}, readMemory)) {
        std::printf("shape prepare status=%d\n", hook.status);
        check(false, "prepare a further descriptor");
        return;
    }
    check(hook.bridgeValid(readMemory), "further descriptor bridge verified");
    checkUnwind(hook, boundaries, saved);
    DWORD64 base = 0;
    const auto table = RtlLookupFunctionEntry(hook.trampoline + 5, &base, nullptr);
    check(table && RtlDeleteFunctionTable(const_cast<PRUNTIME_FUNCTION>(table)), "fixture unregisters its own metadata");
    check(MH_RemoveHook(reinterpret_cast<void*>(target)) == MH_OK, "fixture removes its own unpublished hook");
}
}
extern "C" void EntryBody(void* arg) noexcept(false) {
    check(arg == expected, "exact original argument");
    check(GetLastError() == 0x13572468u, "before observer preserves original incoming LastError");
    ++bodyCalls;
    check(counts.forwarded == bodyCalls, "original forwarding counted once");
    void* frames[16]{};
    check(CaptureStackBackTrace(0, 16, frames, nullptr) > 2, "original body has traversable native stack");
    SetLastError(0x24681357u);
    if (throwBody) throw std::runtime_error("original body");
}
int main() {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    int object{};
    expected = &object;
    descriptorProofs();
    const auto target = reinterpret_cast<std::uintptr_t>(&EntryFrame);
    const auto replacement = reinterpret_cast<std::uintptr_t>(&wrapper);
    std::array<std::uint8_t, 31> prefix{};
    check(readMemory(target, prefix.data(), prefix.size()), "read authored entry prefix");
    const std::array<std::uint8_t, 5> displaced{0x40, 0x56, 0x57, 0x41, 0x56};
    check(std::memcmp(prefix.data(), displaced.data(), 5) == 0, "authored entry has pinned displaced shape");
    for (unsigned n = 0; n < 31; ++n) {
        auto bad = prefix;
        bad[n] ^= 1;
        EntryHook denied;
        check(!denied.prepare(target, replacement, EntryDescriptor{bad, kUnwindA, true}, readMemory) && !denied.created && !denied.writeAttempted,
              "every mismatched source byte refuses before publication");
    }
    otherSiteProofs(target, prefix);
    std::array<std::uint8_t, 32> outerBefore{};
    const auto outerAt = reinterpret_cast<std::uintptr_t>(&outer);
    check(readMemory(outerAt, outerBefore.data(), outerBefore.size()), "capture existing outer wrapper bytes");
    EntryHook hook;
    if (!hook.prepare(target, replacement, EntryDescriptor{prefix, kUnwindA, true}, readMemory)) {
        std::printf("prepare status=%d\n", hook.status);
        check(false, "prepare actual MinHook adapter");
        return 1;
    }
    check(hook.bridgeValid(readMemory), "prepared trampoline verified");
    check(std::memcmp(reinterpret_cast<void*>(target), prefix.data(), 31) == 0, "preparation does not patch source");
    static DWORD64 CONTEXT::*const savedA[3]{&CONTEXT::Rsi, &CONTEXT::Rdi, &CONTEXT::R14};
    checkUnwind(hook, {{0, 0}, {2, 1}, {3, 2}, {5, 3}, {15, 3}}, savedA);
    auto deniedRead = [&](std::uintptr_t at, void* out, std::size_t size) { return at != target && readMemory(at, out, size); };
    check(!hook.enable(deniedRead) && !hook.writeAttempted, "failed final source revalidation does not attempt a patch");
    entryOriginal = reinterpret_cast<void (*)(void*)>(hook.trampoline);
    if (!hook.enable(readMemory)) { check(false, "enable verified entry adapter"); return 1; }
    check(hook.retained(readMemory), "published adapter retained");
    SetLastError(0x13572468u);
    check(EntryAbi(EntryFrame, expected) == 1, "entry preserves Windows nonvolatile ABI");
    check(GetLastError() == 0x24681357u, "after observer preserves original changed LastError");
    SetLastError(0x13572468u);
    outer(expected);
    check(savedOuterOriginal == EntryFrame && outerAfter == 1, "outer saved-original pointer and continuation retained");
    std::thread foreign([&] { SetLastError(0x13572468u); outer(expected); });
    foreign.join();
    check(bodyCalls == 3 && outerAfter == 2, "foreign caller forwards once");
    throwBody = true;
    bool escaped = false;
    SetLastError(0x13572468u);
    try { EntryAbi(EntryFrame, expected); } catch (const std::runtime_error&) { escaped = true; }
    check(escaped && bodyCalls == 4 && counts.unwound == 1 && afterCalls == 3, "original unwind propagates without after callback or replay");
    throwBody = false;
    throwObserver = true;
    SetLastError(0x13572468u);
    EntryFrame(expected);
    throwObserver = false;
    check(bodyCalls == 5 && counts.returned == 4 && counts.observerFaults == 2, "failing before and after observers still forward exactly once");
    check(GetLastError() == 0x24681357u, "observer exceptions preserve original return LastError");
    for (unsigned n = 0; n < 31; ++n) {
        auto changed = [&](std::uintptr_t at, void* out, std::size_t size) {
            if (!readMemory(at, out, size)) return false;
            if (at <= target + n && target + n < at + size) static_cast<std::uint8_t*>(out)[target + n - at] ^= 1;
            return true;
        };
        check(!hook.retained(changed), "retained source mutation invalidates evidence");
    }
    for (unsigned n = 0; n < 33; ++n) {
        auto changed = [&](std::uintptr_t at, void* out, std::size_t size) {
            if (!readMemory(at, out, size)) return false;
            if (at == hook.trampoline && n < size) static_cast<std::uint8_t*>(out)[n] ^= 1;
            return true;
        };
        check(!hook.retained(changed), "retained trampoline mutation invalidates evidence");
    }
    for (unsigned n = 0; n < 12; ++n) {
        auto changed = [&](std::uintptr_t at, void* out, std::size_t size) {
            if (!readMemory(at, out, size)) return false;
            if (at == hook.trampoline + 36 && n < size) static_cast<std::uint8_t*>(out)[n] ^= 1;
            return true;
        };
        check(!hook.retained(changed), "retained unwind metadata mutation invalidates evidence");
    }
    check(MH_DisableHook(reinterpret_cast<void*>(target)) == MH_OK, "fixture disables only its own hook");
    check(std::memcmp(reinterpret_cast<void*>(target), prefix.data(), 31) == 0, "fixture source restored exactly");
    DWORD64 base = 0;
    const auto table = RtlLookupFunctionEntry(hook.trampoline + 5, &base, nullptr);
    if (table && base == hook.trampoline) {
        const bool removed = RtlDeleteFunctionTable(const_cast<PRUNTIME_FUNCTION>(table)) != 0;
        check(removed, "fixture unregisters its own metadata before freeing trampoline");
        if (!removed) return 1;
    }
    check(MH_RemoveHook(reinterpret_cast<void*>(target)) == MH_OK, "fixture removes only owned hook after disable");
    check(std::memcmp(reinterpret_cast<void*>(outerAt), outerBefore.data(), outerBefore.size()) == 0, "outer hook bytes never changed");

    static DWORD64 CONTEXT::*const savedB[3]{&CONTEXT::Rbx, &CONTEXT::Rbp, &CONTEXT::R14};
    checkShape(EntryFrameB, kUnwindB, {{0, 0}, {2, 1}, {3, 2}, {5, 3}, {15, 3}}, savedB);
    static DWORD64 CONTEXT::*const savedC[3]{nullptr, nullptr, nullptr};
    checkShape(EntryFrameC, kUnwindC, {{0, 0}, {5, 0}, {15, 0}}, savedC);

    check(MH_Uninitialize() == MH_OK, "fixture releases own MinHook instance");
    std::printf("checks=%u failures=%u game_code_executed=false\n", checks, failures);
    return failures ? 1 : 0;
}
