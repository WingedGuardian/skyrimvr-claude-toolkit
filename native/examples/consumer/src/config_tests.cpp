#include "site_config.hpp"
#include <cstdio>
#include <string>

namespace {
unsigned checks{}, failures{};
void check(bool pass, const char* name) { ++checks; if (!pass) { ++failures; std::printf("FAIL %s\n", name); } }
const std::string expected = "405657415" "6" + std::string(52, '9');
const std::string unwind = "010503000" "5E0037002600000";
const std::string claims = "r11_is_not_an_input=1\nno_stack_arguments=1\nincoming_rbp_unused=1\n";
std::string good() { return "rva=123456\nexpected=" + expected + "\nunwind=" + unwind + "\n" + claims; }
}
int main() {
    const auto parsed = example::parseConfig(good());
    check(parsed && parsed->rva == 0x123456 && parsed->descriptor.expected[0] == 0x40 && parsed->descriptor.unwind[2] == 3, "a complete site file parses");
    check(example::parseConfig("# comment\r\n" + good()).has_value(), "comments and CRLF are fine");
    check(!example::parseConfig(""), "an empty file is not a site");
    check(!example::parseConfig("rva=123456\nexpected=" + expected + "\nunwind=" + unwind + "\nno_stack_arguments=1\nincoming_rbp_unused=1\n"), "the R11 claim is required");
    check(!example::parseConfig("rva=123456\nexpected=" + expected + "\nunwind=" + unwind + "\nr11_is_not_an_input=1\nincoming_rbp_unused=1\n"), "the stack argument claim is required");
    check(!example::parseConfig("rva=123456\nexpected=" + expected + "\nunwind=" + unwind + "\nr11_is_not_an_input=1\nno_stack_arguments=1\n"), "the RBP claim is required");
    check(!example::parseConfig(good() + "rva=1\n"), "a repeated key refuses");
    check(!example::parseConfig(good() + "extra=1\n"), "an unknown key refuses");
    check(!example::parseConfig("rva=0\nexpected=" + expected + "\nunwind=" + unwind + "\n" + claims), "RVA zero refuses");
    check(!example::parseConfig("rva=123456\nexpected=99\nunwind=" + unwind + "\n" + claims), "short expected bytes refuse");
    check(!example::parseConfig("rva=123456\nexpected=" + expected + "\nunwind=020503000" "5E0037002600000\n" + claims), "an invalid unwind refuses");
    check(!example::parseConfig("rva=123456\nexpected=" + expected + "\nunwind=" + unwind + "\nr11_is_not_an_input=0\nno_stack_arguments=1\nincoming_rbp_unused=1\n"), "R11 claim must be affirmative");
    std::printf("checks=%u failures=%u game_code_executed=false\n", checks, failures);
    return failures ? 1 : 0;
}
