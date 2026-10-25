// Preserve the actual Windows command line for the UTF-8 structured interface.
// The original CLI keeps its existing dispatch and argument behavior.
#include "core/Structured.h"
#include <cstdio>
#ifdef _WIN32
#include <windows.h>
#include <shellapi.h>
#endif

namespace ess::structured {
int Entry(int argc, char **argv) {
#ifdef _WIN32
  int count = 0;
  auto wide = CommandLineToArgvW(GetCommandLineW(), &count);
  if (!wide) {
    std::puts("{\"ok\":false,\"error\":\"could not read Unicode arguments\"}");
    return 2;
  }
  struct Owner { LPWSTR *value; ~Owner() { LocalFree(value); } } owner{wide};
  std::vector<std::string> words;
  words.reserve(count);
  for (int i = 0; i < count; ++i) {
    const int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide[i], -1, nullptr, 0, nullptr, nullptr);
    if (!size) {
      std::puts("{\"ok\":false,\"error\":\"invalid Unicode argument\"}");
      return 2;
    }
    std::string word(size, '\0');
    if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide[i], -1, word.data(), size, nullptr, nullptr) != size) {
      std::puts("{\"ok\":false,\"error\":\"Unicode argument conversion failed\"}");
      return 2;
    }
    word.pop_back();
    words.push_back(std::move(word));
  }
  std::vector<char *> utf8;
  for (auto &word : words) utf8.push_back(word.data());
  return Main(count, utf8.data());
#else
  return Main(argc, argv);
#endif
}
}
