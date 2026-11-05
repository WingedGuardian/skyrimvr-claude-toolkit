#pragma once
// The one site this example observes, from a small text file the tester writes. No file means no hook: the DLL stays inert.
// A real consumer would keep its descriptors in code it has reviewed; this is a test vehicle.
#include "skyrim_native/entry_hook.hpp"
#include <cstdint>
#include <optional>
#include <string_view>

namespace example {
struct SiteConfig {
    std::uint32_t rva{};
    skyrim_native::EntryDescriptor descriptor{};
};

template <std::size_t N>
constexpr bool parseHex(std::string_view text, std::array<std::uint8_t, N>& out) noexcept {
    if (text.size() != N * 2) return false;
    const auto digit = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    for (std::size_t n = 0; n < N; ++n) {
        const int high = digit(text[2 * n]), low = digit(text[2 * n + 1]);
        if (high < 0 || low < 0) return false;
        out[n] = static_cast<std::uint8_t>(high * 16 + low);
    }
    return true;
}

// Lines of key=value; every key is required exactly once, and anything else refuses the whole file.
inline std::optional<SiteConfig> parseConfig(std::string_view text) noexcept {
    SiteConfig config;
    unsigned seen = 0;
    while (!text.empty()) {
        const auto end = text.find('\n');
        auto line = text.substr(0, end);
        text = end == std::string_view::npos ? std::string_view{} : text.substr(end + 1);
        if (!line.empty() && line.back() == '\r') line.remove_suffix(1);
        if (line.empty() || line[0] == '#') continue;
        const auto equals = line.find('=');
        if (equals == std::string_view::npos) return std::nullopt;
        const auto key = line.substr(0, equals), value = line.substr(equals + 1);
        unsigned bit = 0;
        if (key == "rva") {
            std::uint32_t parsed = 0;
            if (value.empty() || value.size() > 8) return std::nullopt;
            for (const char c : value) {
                const int d = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
                if (d < 0) return std::nullopt;
                parsed = parsed * 16 + static_cast<std::uint32_t>(d);
            }
            config.rva = parsed;
            bit = 1;
        } else if (key == "expected") {
            if (!parseHex(value, config.descriptor.expected)) return std::nullopt;
            bit = 2;
        } else if (key == "unwind") {
            if (!parseHex(value, config.descriptor.unwind)) return std::nullopt;
            bit = 4;
        } else if (key == "r11_is_not_an_input") {
            if (value != "1") return std::nullopt;
            config.descriptor.r11IsNotAnInput = true;
            bit = 8;
        } else if (key == "no_stack_arguments") {
            if (value != "1") return std::nullopt;
            config.descriptor.noStackArguments = true;
            bit = 16;
        } else if (key == "incoming_rbp_unused") {
            if (value != "1") return std::nullopt;
            config.descriptor.incomingRbpUnused = true;
            bit = 32;
        } else {
            return std::nullopt;
        }
        if (seen & bit) return std::nullopt;
        seen |= bit;
    }
    if (seen != 63 || !config.rva || !skyrim_native::validDescriptor(config.descriptor)) return std::nullopt;
    return config;
}
}
