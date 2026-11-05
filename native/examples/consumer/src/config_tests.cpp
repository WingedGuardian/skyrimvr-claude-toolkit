#include "site_config.hpp"
#include <cstdio>
#include <string>

namespace {
unsigned checks{}, failures{};
void check(bool pass, const char* name) { ++checks; if (!pass) { ++failures; std::printf("FAIL %s\n", name); } }
const std::string expected(62, '9');
const std::string unwind = "010503000" "5E0037002600000";
std::string good() { return "rva=A27730\nexpected=" + expected + "\nunwind=" + unwind + "\nr11_is_not_an_input=1\n"; }
}
int main() {
    const auto parsed = example::parseConfig(good());
    check(parsed && parsed->rva == 0xA27730 && parsed->descriptor.expected[0] == 0x99 && parsed->descriptor.unwind[2] == 3, "a complete site file parses");
    check(example::parseConfig("# comment\r\n" + good()).has_value(), "comments and CRLF are fine");
    check(!example::parseConfig(""), "an empty file is not a site");
    check(!example::parseConfig("rva=A27730\nexpected=" + expected + "\nunwind=" + unwind + "\n"), "the R11 claim is required");
    check(!example::parseConfig(good() + "rva=1\n"), "a repeated key refuses");
    check(!example::parseConfig(good() + "extra=1\n"), "an unknown key refuses");
    check(!example::parseConfig("rva=0\nexpected=" + expected + "\nunwind=" + unwind + "\nr11_is_not_an_input=1\n"), "RVA zero refuses");
    check(!example::parseConfig("rva=A27730\nexpected=99\nunwind=" + unwind + "\nr11_is_not_an_input=1\n"), "short expected bytes refuse");
    check(!example::parseConfig("rva=A27730\nexpected=" + expected + "\nunwind=020503000" "5E0037002600000\nr11_is_not_an_input=1\n"), "an invalid unwind refuses");
    check(!example::parseConfig("rva=A27730\nexpected=" + expected + "\nunwind=" + unwind + "\nr11_is_not_an_input=0\n"), "R11 claim must be affirmative");
    std::printf("checks=%u failures=%u game_code_executed=false\n", checks, failures);
    return failures ? 1 : 0;
}
