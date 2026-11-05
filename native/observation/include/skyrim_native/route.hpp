#pragma once
#include <array>
#include <cstdint>
#include <cstring>
#include <limits>

namespace skyrim_native {
inline bool makeCall(std::uintptr_t source,std::uintptr_t target,std::array<std::uint8_t,5>& bytes) noexcept {
    const auto displacement=static_cast<std::int64_t>(target)-static_cast<std::int64_t>(source+5);
    if (displacement<std::numeric_limits<std::int32_t>::min() || displacement>std::numeric_limits<std::int32_t>::max()) return false;
    bytes[0]=0xE8;
    const auto relative=static_cast<std::int32_t>(displacement);
    std::memcpy(bytes.data()+1,&relative,4); return true;
}
// Exactly the stock SKSEVR branch trampoline, or a direct call to the pinned
// SKSE function. Never walk arbitrary chains or execute unqualified targets.
template<class Read>
std::uintptr_t resolveCall(std::uintptr_t site,std::uintptr_t target,Read read) noexcept {
    std::array<std::uint8_t,5> call{};
    if (!read(site,call.data(),call.size()) || call[0]!=0xE8) return 0;
    std::int32_t displacement=0;
    std::memcpy(&displacement,call.data()+1,4);
    const auto direct=static_cast<std::uintptr_t>(static_cast<std::intptr_t>(site+5)+displacement);
    if (direct==target) return direct;
    std::array<std::uint8_t,14> branch{};
    if (!read(direct,branch.data(),branch.size()) || branch[0]!=0xFF || branch[1]!=0x25 ||
        branch[2] || branch[3] || branch[4] || branch[5]) return 0;
    std::uintptr_t destination=0;
    std::memcpy(&destination,branch.data()+6,8);
    return destination==target ? direct : 0;
}
template<class Read,std::size_t N>
bool bytesMatch(std::uintptr_t at,const std::array<std::uint8_t,N>& expected,
                std::size_t excludedOffset,std::size_t excludedSize,Read read) noexcept {
    std::array<std::uint8_t,N> current{};
    if (excludedOffset>N || excludedSize>N-excludedOffset || !read(at,current.data(),N)) return false;
    for (std::size_t n=0;n<N;++n)
        if ((n<excludedOffset || n>=excludedOffset+excludedSize) && current[n]!=expected[n]) return false;
    return true;
}
}
