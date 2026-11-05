#pragma once
// Capture lifecycle for a passive observer. Promoted from the Delirium probe's core.hpp, with producer draining added.
#include <atomic>
#include <cstdint>

namespace skyrim_native {
// SKSE's PostLoadGame message carries success as the pointer VALUE 1 with dataLen 1. It is not a bool*: dereferencing
// it faults.
inline bool loadSucceeded(std::uintptr_t encoded, std::uint32_t size) noexcept { return encoded == 1 && size == 1; }

// state: bit 0 = a save is loaded, bit 1 = armed, the rest = generation. Every invalidation moves the generation, so an
// arm request computed before a PreLoadGame fails its compare-exchange instead of arming the next load.
struct CaptureGate {
    std::atomic<std::uint64_t> state{}, deadline{}, active{};

    void loaded() noexcept { state.fetch_or(1, std::memory_order_acq_rel); }
    void invalidate(bool clearLoaded) noexcept {
        auto old = state.load(std::memory_order_acquire);
        while (!state.compare_exchange_weak(old, (old + 4) & ~(clearLoaded ? 3ull : 2ull), std::memory_order_acq_rel)) {}
    }
    void preload() noexcept { invalidate(true); }
    void disarm() noexcept { invalidate(false); }
    std::uint64_t ticket() const noexcept { return state.load(std::memory_order_acquire); }
    bool arm(std::uint64_t expected, std::uint64_t now, std::uint64_t duration) noexcept {
        if ((expected & 3) != 1 || !duration || now > UINT64_MAX - duration) return false;
        deadline.store(now + duration, std::memory_order_release);
        return state.compare_exchange_strong(expected, expected | 2, std::memory_order_acq_rel, std::memory_order_acquire);
    }
    bool admits(std::uint64_t now) const noexcept {
        return (state.load(std::memory_order_acquire) & 3) == 3 && now < deadline.load(std::memory_order_acquire);
    }
    // Hot path: a producer registers before checking, so disarm-then-drained() cannot miss one that was admitted.
    bool enter(std::uint64_t now) noexcept {
        active.fetch_add(1, std::memory_order_seq_cst);
        if (admits(now)) return true;
        active.fetch_sub(1, std::memory_order_release);
        return false;
    }
    void leave() noexcept { active.fetch_sub(1, std::memory_order_release); }
    // True only after every admitted producer has left. If this stays false at the deadline, report the capture as
    // incomplete: never publish it as complete.
    bool drained() const noexcept { return active.load(std::memory_order_acquire) == 0; }
};

// After a callback is published the DLL can never unload, so a later start failure must leave inert resident code.
// Returning failure from SKSEPlugin_Load here would unload code a registered callback still points at.
template <class Publish, class Start, class Failure>
bool lifetimeStart(Publish publish, Start start, Failure failure) noexcept {
    try { if (!publish()) return false; } catch (...) { return false; }
    try { start(); } catch (...) { failure(); }
    return true;
}
}
