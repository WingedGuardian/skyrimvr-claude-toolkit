#include "Verify.h"
#include <algorithm>

namespace ess::verify {
namespace {
void Bytes(Result &result, const std::string &section,
           const std::vector<std::uint8_t> &a,
           const std::vector<std::uint8_t> &b) {
  const auto n = std::min(a.size(), b.size());
  std::size_t at = 0;
  while (at < n && a[at] == b[at])
    ++at;
  if (at != n || a.size() != b.size())
    result.differences.push_back({section, at, a.size(), b.size()});
}
void Table(Result &result, const std::string &name,
           const std::vector<GlobalData> &a, const std::vector<GlobalData> &b) {
  if (a.size() != b.size())
    result.differences.push_back({name + ".count", 0, a.size(), b.size()});
  for (std::size_t i = 0; i < std::min(a.size(), b.size()); ++i) {
    Writer x, y;
    x.u32(a[i].type);
    y.u32(b[i].type);
    if (a[i].papyrus)
      a[i].papyrus->write(x);
    else
      x.bytes(a[i].raw);
    if (b[i].papyrus)
      b[i].papyrus->write(y);
    else
      y.bytes(b[i].raw);
    Bytes(result, name + "[" + std::to_string(i) + "]", x.buf, y.buf);
  }
}
} // namespace
Result Expected(const SaveFile &expected, const SaveFile &actual) {
  Result result;
  Writer a, b;
  expected.header.write(a);
  actual.header.write(b);
  Bytes(result, "header", a.buf, b.buf);
  Table(result, "table1", expected.table1, actual.table1);
  Table(result, "table2", expected.table2, actual.table2);
  Table(result, "table3", expected.table3, actual.table3);
  // Whole body additionally covers ordered changeform headers/raw bytes,
  // plugin/form tables, visited worlds, unused FLT words and unknown3.
  Bytes(result, "body", expected.buildBody(), actual.buildBody());
  Bytes(result, "cosave", expected.cosave, actual.cosave);
  const bool expectedCo = expected.hasCosave || !expected.cosave.empty();
  const bool actualCo = actual.hasCosave || !actual.cosave.empty();
  if (expectedCo != actualCo)
    result.differences.push_back({"cosavePresence", 0, expectedCo ? 1u : 0u,
                                  actualCo ? 1u : 0u});
  if (!actual.problems.empty())
    result.differences.push_back(
        {"structuralProblems", 0, 0, actual.problems.size()});
  return result;
}
std::string Describe(const Result &result) {
  std::string text;
  for (const auto &d : result.differences) {
    if (!text.empty())
      text += "; ";
    text += d.section + " at byte " + std::to_string(d.offset) + " (expected " +
            std::to_string(d.expectedSize) + ", actual " +
            std::to_string(d.actualSize) + ")";
  }
  return text;
}
} // namespace ess::verify
