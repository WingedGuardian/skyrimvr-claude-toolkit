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
    bool r11IsNotAnInput{};                     // evidence the consumer has from the site's decoded body
};

// Only what a 5-byte displaced prologue can legally need: version 1, no flags or frame register, PUSH-nonvolatile codes
// inside the displaced bytes, in the descending order the format requires.
constexpr bool validDescriptor(const EntryDescriptor& d) noexcept {
    if (!d.r11IsNotAnInput) return false;
    const auto prolog = d.unwind[1];
    const auto count = d.unwind[2];
    if (d.unwind[0] != 1 || d.unwind[3] != 0 || prolog > 5 || count > 4) return false;
    std::uint8_t previous = prolog + 1;
    for (std::size_t n = 0; n < count; ++n) {
        const auto offset = d.unwind[4 + 2 * n];
        const auto code = d.unwind[5 + 2 * n];
        if (offset == 0 || offset > prolog || offset >= previous || (code & 0x0F) != 0) return false;
        previous = offset;
    }
    for (std::size_t n = 4 + 2 * std::size_t(count); n < d.unwind.size(); ++n)
        if (d.unwind[n] != 0) return false;
    return true;
}

// No process-lifetime destructor removes a live hook or frees its trampoline.
struct EntryHook {
    enum Status : std::int32_t {
        kAlreadyPrepared = 1001, kSourceMismatch = 1002, kStockLayout = 1003, kFlush = 1004, kSourceChanged = 1005,
        kEnableRevalidation = 1006, kNotRetained = 1007, kUnwindWrite = 1009, kUnwindRegister = 1010, kBridgeInvalid = 1011,
        kContinuationWrite = 1012, kContinuationInvalid = 1013, kBadDescriptor = 1014
    };
    EntryHook() = default;
    EntryHook(const EntryHook&) = delete;
    EntryHook& operator=(const EntryHook&) = delete;
    std::uintptr_t target{}, replacement{}, trampoline{};
    EntryDescriptor site{};
    std::array<std::uint8_t, 5> patch{};
    std::int32_t status{};
    bool created{}, writeAttempted{}, unwindRegistered{};
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
    template <class Read> bool enable(Read read) noexcept {
        if (!created || !bridgeValid(read) || !bytesMatch(target, site.expected, 0, 0, read)) { status = kEnableRevalidation; return false; }
        writeAttempted = true;
        const auto result = MH_EnableHook(reinterpret_cast<void*>(target));
        status = static_cast<int>(result);
        if (result != MH_OK) return false;
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
