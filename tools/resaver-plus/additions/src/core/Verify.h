// Reusable expected-result verification for ReSaver++ (Apache-2.0).
#pragma once
#include "Save.h"

namespace ess::verify {
struct Difference {
  std::string section;
  std::size_t offset = 0, expectedSize = 0, actualSize = 0;
};
struct Result {
  std::vector<Difference> differences;
  bool identical() const { return differences.empty(); }
};
// Compare serialized native model regions, including opaque data and every
// Papyrus variable. Derived location-table offsets are rebuilt by the core.
// Counts alone, and successful reopening alone, are insufficient.
Result Expected(const SaveFile &expected, const SaveFile &actual);
std::string Describe(const Result &result);
} // namespace ess::verify
