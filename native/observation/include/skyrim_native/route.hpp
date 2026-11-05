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
