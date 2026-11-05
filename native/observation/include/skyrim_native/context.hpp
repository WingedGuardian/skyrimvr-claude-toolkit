#pragma once
#include <array>
#include <cstddef>
#include <cstdint>

namespace skyrim_native {
inline constexpr std::size_t xstateCapacity=16384;
// Ephemeral bridge packet. The aligned XSAVE storage belongs to this invocation;
// callbacks may copy scalars but must never retain or publish its pointer.
struct alignas(16) Context {
    std::array<std::uint64_t,16> gpr; // RAX RCX RDX RBX RSP RBP RSI RDI R8..R15
    std::uint64_t flags, caller;
    std::uint8_t* xstate;
    std::uint32_t lastError{}, reserved{};
};
static_assert(offsetof(Context,flags)==0x80 && offsetof(Context,caller)==0x88);
static_assert(offsetof(Context,xstate)==0x90 && sizeof(Context)==0xA0);
}
