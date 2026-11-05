#pragma once
// Capture lifecycle for a passive observer. Promoted from the Delirium probe's core.hpp. Producer draining added, and the
// deadline moved inside the one atomic word, so a stale arm can never change a live capture's deadline.
#include <atomic>
#include <cstdint>

namespace skyrim_native {
// SKSE's PostLoadGame message carries success as the pointer VALUE 1 with dataLen 1. It is not a bool*: dereferencing
// it faults.
inline bool loadSucceeded(std::uintptr_t encoded, std::uint32_t size) noexcept { return encoded == 1 && size == 1; }

// state: bit 0 = a save is loaded, bit 1 = armed, bits 2-15 = generation, bits 16-63 = deadline in the caller's ticks.
// Every invalidation moves the generation, so an arm request computed before a PreLoadGame fails its compare-exchange
// instead of arming the next load. The generation wraps after 16384 invalidations; a stalled arm request surviving exactly
// that many is treated as impossible. `now` must fit 48 bits, which at QueryPerformanceCounter's usual 10 MHz is 325 days.
struct CaptureGate {
    static constexpr std::uint64_t kLoaded = 1, kArmed = 2, kGenerationUnit = 4, kGenerationMask = 0xFFFC;
    static constexpr unsigned kDeadlineShift = 16;
    static constexpr std::uint64_t kMaxTick = (1ull << 48) - 1;
    std::atomic<std::uint64_t> state{}, active{};

    void loaded() noexcept { state.fetch_or(kLoaded, std::memory_order_acq_rel); }
    void invalidate(bool clearLoaded) noexcept {
        auto old = state.load(std::memory_order_acquire);
        for (;;) {
            const auto next = ((old + kGenerationUnit) & kGenerationMask) | (clearLoaded ? 0 : (old & kLoaded));
            if (state.compare_exchange_weak(old, next, std::memory_order_acq_rel)) return;
        }
    }
    void preload() noexcept { invalidate(true); }
    void disarm() noexcept { invalidate(false); }
    std::uint64_t ticket() const noexcept { return state.load(std::memory_order_acquire); }
    // Refuses while a producer from an earlier capture is still inside: it could otherwise write into this one.
    bool arm(std::uint64_t expected, std::uint64_t now, std::uint64_t duration) noexcept {
        if ((expected & (kLoaded | kArmed)) != kLoaded || !duration || now > kMaxTick || duration > kMaxTick - now) return false;
        if (!drained()) return false;
        const auto desired = expected | kArmed | ((now + duration) << kDeadlineShift);
        return state.compare_exchange_strong(expected, desired, std::memory_order_acq_rel, std::memory_order_acquire);
    }
    bool admits(std::uint64_t now) const noexcept {
        const auto current = state.load(std::memory_order_acquire);
        return (current & (kLoaded | kArmed)) == (kLoaded | kArmed) && now < (current >> kDeadlineShift);
    }
    // Hot path. The first check is a fast refusal that leaves the count alone: a hot function hammering a stopped gate must
    // not keep the count above zero, or drained() would never converge. A producer then registers before the check that
    // counts, so disarm-then-drained() cannot miss one that was admitted.
    bool enter(std::uint64_t now) noexcept {
        if (!admits(now)) return false;
        active.fetch_add(1, std::memory_order_seq_cst);
        if (admits(now)) return true;
        active.fetch_sub(1, std::memory_order_release);
        return false;
    }
    void leave() noexcept { active.fetch_sub(1, std::memory_order_release); }
    // Meaningful only after disarm() or preload(): true then means every admitted producer has left. If it stays false at
    // the deadline, report the capture as incomplete, never as complete.
    bool drained() const noexcept { return active.load(std::memory_order_seq_cst) == 0; }
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
