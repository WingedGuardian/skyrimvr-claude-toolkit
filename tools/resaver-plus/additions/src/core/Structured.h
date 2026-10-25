// Structured queries over the existing ReSaver++ native model (Apache-2.0).
#pragma once
#include "Json.h"
#include "Save.h"

namespace ess::structured {
struct Arguments {
  std::vector<std::string> positional;
  std::map<std::string, std::string> options;
  bool has(const std::string &flag) const { return options.contains(flag); }
  std::string option(const std::string &flag,
                     const std::string &fallback = {}) const;
};
Arguments Parse(const std::string &operation,
                const std::vector<std::string> &args);
json::Value Query(const std::string &operation, SaveFile &save,
                  const Arguments &args, SaveFile *second = nullptr);
bool IsEdit(const std::string &operation);
json::Value Edit(const std::string &operation, SaveFile &save,
                 const Arguments &args);
json::Value ChangeDifferences(const SaveFile &before, const SaveFile &after,
                              const Arguments &args);
std::string Serialize(const json::Value &value);
// Native CLI dispatch; compatibility and local preservation remain external.
int Main(int argc, char **argv);
// CLI-only boundary: preserve Unicode arguments on Windows.
int Entry(int argc, char **argv);
} // namespace ess::structured
