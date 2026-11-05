#pragma once
// A checked entry hook for one consumer-described site. Windows x64, MinHook 1.3.4 exactly.
//
// Each site supplies an EntryDescriptor: its expected bytes, the 5 displaced bytes, and the unwind codes those bytes
// need. Nothing here knows a game address. A fixture passing for one descriptor qualifies no other site.
//
// Layout of MinHook's 64-byte x64 slot once prepared (33 bytes of code, then metadata in the unused tail):
//   +0  displaced bytes (5)   +5  MOV R11,continuation / JMP R11 / NOP (14)   +19 relay to the detour (14)
//   +36 UNWIND_INFO (12)
// MOV/JMP R11 replaces MinHook's stock FF25 jump, because Windows classifies that jump as an epilogue and virtual unwind
// then goes wrong after displaced PUSHes. So R11 must not be an input at the site: the descriptor has to say so.
//
// Keep an EntryHook in static storage: once prepared, the OS function table points into it, for the life of the process.
#include <Windows.h>
#include <MinHook.h>
#include <array>
#include <cstdint>
#include <cstring>
#include "route.hpp"

namespace skyrim_native {
struct EntryDescriptor {
    std::array<std::uint8_t, 31> expected{};    // the site's bytes; anything else (a foreign hook, a game update) refuses
    std::array<std::uint8_t, 12> unwind{};      // UNWIND_INFO for the displaced bytes
    // Evidence the consumer has from the site's decoded body. Nothing here can check these for you.
    bool r11IsNotAnInput{};
    bool noStackArguments{};                    // the bridge cannot forward stack arguments: 5+ arguments or varargs get garbage
    bool incomingRbpUnused{};                   // the bridge keeps RBP as its frame pointer across the original
};

// Only the displaced shapes we have qualified, fully decoded: REX-padded or plain PUSHes filling all 5 bytes, or a single
// 5-byte store into a home slot (MOV [RSP+disp8],reg), which changes no stack. Anything else refuses. The unwind codes
// must then be exactly what those bytes do, so a PUSH cannot be declared as another register or left out.
constexpr bool unwindMatchesBytes(const std::array<std::uint8_t, 31>& expected, const std::array<std::uint8_t, 12>& unwind) noexcept {
    std::uint8_t ends[5]{}, registers[5]{};
    std::size_t pushes = 0, at = 0;
    while (at < 5) {
        const auto byte = expected[at];
        const bool plain = byte >= 0x50 && byte <= 0x57;
        const bool prefixed = (byte == 0x40 || byte == 0x41) && at + 1 < 5 && expected[at + 1] >= 0x50 && expected[at + 1] <= 0x57;
        if (!plain && !prefixed) break;
        const auto opcode = plain ? byte : expected[at + 1];
        registers[pushes] = static_cast<std::uint8_t>(opcode - 0x50 + (byte == 0x41 ? 8 : 0));
        at += plain ? 1 : 2;
        ends[pushes++] = static_cast<std::uint8_t>(at);
    }
    if (at != 5) {
        const bool homeStore = pushes == 0 && (expected[0] == 0x48 || expected[0] == 0x4C) && expected[1] == 0x89 &&
                               (expected[2] & 0xC7) == 0x44 && expected[3] == 0x24;
        if (!homeStore) return false;
    }
    if (unwind[1] != (pushes ? 5 : 0) || unwind[2] != pushes) return false;
    for (std::size_t n = 0; n < pushes; ++n) {
        const auto source = pushes - 1 - n;      // the format lists the last-executed operation first
        if (unwind[4 + 2 * n] != ends[source] || unwind[5 + 2 * n] != (registers[source] << 4)) return false;
    }
    return true;
}
constexpr bool validDescriptor(const EntryDescriptor& d) noexcept {
    if (!d.r11IsNotAnInput || !d.noStackArguments || !d.incomingRbpUnused) return false;
    if (d.unwind[0] != 1 || d.unwind[3] != 0 || d.unwind[2] > 4) return false;
    for (std::size_t n = 4 + 2 * std::size_t(d.unwind[2]); n < d.unwind.size(); ++n)
        if (d.unwind[n] != 0) return false;
    return unwindMatchesBytes(d.expected, d.unwind);
}

// No process-lifetime destructor removes a live hook or frees its trampoline.
struct EntryHook {
    enum Status : std::int32_t {
        kAlreadyPrepared = 1001, kSourceMismatch = 1002, kStockLayout = 1003, kFlush = 1004, kSourceChanged = 1005,
        kEnableRevalidation = 1006, kNotRetained = 1007, kUnwindWrite = 1009, kUnwindRegister = 1010, kBridgeInvalid = 1011,
        kContinuationWrite = 1012, kContinuationInvalid = 1013, kBadDescriptor = 1014, kBadStateMask = 1015
    };
    EntryHook() = default;
    EntryHook(const EntryHook&) = delete;
    EntryHook& operator=(const EntryHook&) = delete;
    std::uintptr_t target{}, replacement{}, trampoline{};
    EntryDescriptor site{};
    std::array<std::uint8_t, 5> patch{};
    std::int32_t status{};
    bool created{}, writeAttempted{}, unwindRegistered{};
    bool enabled{};                             // MinHook patched the site. True even if the retained check then fails.
    RUNTIME_FUNCTION runtimeFunction{0, 19, 36};
    static_assert(sizeof(RUNTIME_FUNCTION) == 12 && 36 + 12 <= 64);

    template <class Read> bool codeValid(Read read, bool stock = false) const noexcept {
        std::array<std::uint8_t, 33> wanted{}, actual{};
        std::memcpy(wanted.data(), site.expected.data(), 5);
        const auto continuation = target + 5;
        if (stock) {
            wanted[5] = 0xFF; wanted[6] = 0x25; std::memcpy(wanted.data() + 11, &continuation, 8);
        } else {
            wanted[5] = 0x49; wanted[6] = 0xBB; std::memcpy(wanted.data() + 7, &continuation, 8);
            wanted[15] = 0x41; wanted[16] = 0xFF; wanted[17] = 0xE3; wanted[18] = 0x90;
        }
        wanted[19] = 0xFF; wanted[20] = 0x25;    // relay: FF25, zero disp32, absolute destination at +25
        std::memcpy(wanted.data() + 25, &replacement, 8);
        return trampoline && read(trampoline, actual.data(), actual.size()) && actual == wanted;
    }
    template <class Read> bool bridgeValid(Read read) const noexcept {
        std::array<std::uint8_t, 12> actual{};
        DWORD64 base = 0;
        if (!unwindRegistered || !codeValid(read) || !read(trampoline + 36, actual.data(), actual.size()) || actual != site.unwind)
            return false;
        const auto found = RtlLookupFunctionEntry(trampoline + 5, &base, nullptr);
        return found == &runtimeFunction && base == trampoline && runtimeFunction.BeginAddress == 0 &&
               runtimeFunction.EndAddress == 19 && runtimeFunction.UnwindInfoAddress == 36;
    }
    template <class Read> bool prepare(std::uintptr_t at, std::uintptr_t detour, const EntryDescriptor& descriptor, Read read) noexcept {
        if (created) { status = kAlreadyPrepared; return false; }
        if (!validDescriptor(descriptor)) { status = kBadDescriptor; return false; }
        target = at; replacement = detour; site = descriptor;
        if (!bytesMatch(target, site.expected, 0, 0, read)) { status = kSourceMismatch; return false; }
        const auto init = MH_Initialize();
        if (init != MH_OK && init != MH_ERROR_ALREADY_INITIALIZED) { status = static_cast<int>(init); return false; }
        void* original = nullptr;
        const auto result = MH_CreateHook(reinterpret_cast<void*>(target), reinterpret_cast<void*>(replacement), &original);
        status = static_cast<int>(result);
        if (result != MH_OK) return false;
        created = true;
        trampoline = reinterpret_cast<std::uintptr_t>(original);
        if (!codeValid(read, true) || !makeCall(target, trampoline + 19, patch)) { status = kStockLayout; return false; }
        patch[0] = 0xE9;
        SIZE_T copied = 0;
        std::array<std::uint8_t, 14> jump{0x49, 0xBB, 0, 0, 0, 0, 0, 0, 0, 0, 0x41, 0xFF, 0xE3, 0x90};
        const auto continuation = target + 5;
        std::memcpy(jump.data() + 2, &continuation, 8);
        if (!WriteProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(trampoline + 5), jump.data(), jump.size(), &copied) || copied != jump.size()) {
            status = kContinuationWrite; return false;
        }
        if (!codeValid(read)) { status = kContinuationInvalid; return false; }
        if (!WriteProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(trampoline + 36), site.unwind.data(), site.unwind.size(), &copied) || copied != site.unwind.size()) {
            status = kUnwindWrite; return false;
        }
        unwindRegistered = RtlAddFunctionTable(&runtimeFunction, 1, trampoline) != 0;
        if (!unwindRegistered) { status = kUnwindRegister; return false; }
        if (!bridgeValid(read)) { status = kBridgeInvalid; return false; }
        if (!FlushInstructionCache(GetCurrentProcess(), original, 48)) { status = kFlush; return false; }
        if (!bytesMatch(target, site.expected, 0, 0, read)) { status = kSourceChanged; return false; }
        return true;
    }
    // For a site whose detour is a MASM BRIDGE. An unset state mask makes XSAVE and XRSTOR legal no-ops, so the observer
    // would silently clobber the original's vector registers. Set the mask before this call.
    template <class Read> bool prepareBridge(std::uintptr_t at, std::uintptr_t bridge, const EntryDescriptor& descriptor,
                                             std::uint64_t stateMask, Read read) noexcept {
        if ((stateMask & 3) != 3) { status = kBadStateMask; return false; }
        return prepare(at, bridge, descriptor, read);
    }
    template <class Read> bool enable(Read read) noexcept {
        if (!created || !bridgeValid(read) || !bytesMatch(target, site.expected, 0, 0, read)) { status = kEnableRevalidation; return false; }
        writeAttempted = true;
        const auto result = MH_EnableHook(reinterpret_cast<void*>(target));
        status = static_cast<int>(result);
        if (result != MH_OK) return false;
        enabled = true;
        if (!retained(read)) { status = kNotRetained; return false; }
        return true;
    }
    template <class Read> bool retained(Read read) const noexcept {
        std::array<std::uint8_t, 5> actual{};
        return created && read(target, actual.data(), actual.size()) && actual == patch &&
               bytesMatch(target, site.expected, 0, 5, read) && bridgeValid(read);
    }
};
}
