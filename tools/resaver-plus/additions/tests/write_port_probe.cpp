// A single protected disk roundtrip probe. The external adapter preserves
// pairs. Creates one NEW output pair; never overwrites, deletes or launches the
// game.
#include "core/Save.h"
#include "core/Verify.h"
#include "SyntheticSave.h"
#include <cstdio>
#include <fstream>
#include <iterator>
using namespace ess;
int main(int argc, char **argv) {
  try {
    if (argc != 3 && argc != 4)
      throw std::runtime_error(
          "input, new output and optional probe case required");
    const std::string mode = argc == 4 ? argv[3] : "noop";
    if (mode != "noop" && mode != "synthetic-fixture" && mode != "staged-ess" && mode != "staged-cosave" &&
        mode != "destination-collision" &&
        mode != "cosave-publication-collision")
      throw std::runtime_error("unknown probe case");
    const std::filesystem::path output = argv[2];
    auto co = output;
    co.replace_extension(".skse");
    if (std::filesystem::exists(output) || std::filesystem::exists(co))
      throw std::runtime_error("output pair already exists");
    auto expected = mode == "synthetic-fixture" ? synthetic::Make() : SaveFile::load(argv[1]);
    if ((mode == "staged-cosave" || mode == "cosave-publication-collision") &&
        !expected->hasCosave && expected->cosave.empty())
      throw std::runtime_error("co-save probe requires a source co-save");
    bool injected = false, rejected = false;
    auto probe = [&](SaveFile::WriteStage stage,
                     const std::filesystem::path &staged) {
      if (mode == "staged-ess" && stage == SaveFile::WriteStage::kStaged) {
        std::fstream f(staged, std::ios::binary | std::ios::in | std::ios::out);
        f.put('X');
        f.flush();
        if (!f)
          throw std::runtime_error("fault injection failed");
        injected = true;
      } else if (mode == "staged-cosave" &&
                 stage == SaveFile::WriteStage::kStaged) {
        auto partner = staged;
        partner.replace_extension(".skse");
        std::ofstream f(partner, std::ios::binary | std::ios::app);
        f.put('X');
        f.flush();
        if (!f)
          throw std::runtime_error("fault injection failed");
        injected = true;
      } else if (mode == "destination-collision" &&
                 stage == SaveFile::WriteStage::kVerified) {
        std::ofstream f(output, std::ios::binary);
        f << "COLLISION";
        f.flush();
        if (!f)
          throw std::runtime_error("collision injection failed");
        injected = true;
      } else if (mode == "cosave-publication-collision" &&
                 stage == SaveFile::WriteStage::kEssPublished) {
        if (!std::filesystem::create_directory(co))
          throw std::runtime_error("co-save collision injection failed");
        injected = true;
      }
    };
    try {
      expected->write(output, false, probe);
    } catch (const std::exception &e) {
      rejected = true;
      std::fprintf(stderr, "write rejected: %s\n", e.what());
    }
    if (mode != "noop" && mode != "synthetic-fixture") {
      if (!injected || !rejected)
        throw std::runtime_error("fault was not injected and rejected");
      if (mode == "destination-collision") {
        std::ifstream f(output, std::ios::binary);
        std::string bytes((std::istreambuf_iterator<char>(f)), {});
        if (bytes != "COLLISION" || std::filesystem::exists(co))
          throw std::runtime_error("collision destination was changed");
      } else if (std::filesystem::exists(output))
        throw std::runtime_error(
            "rejected ESS was published or rollback failed");
      if (mode == "cosave-publication-collision" &&
          !std::filesystem::is_directory(co))
        throw std::runtime_error("external collision object was not preserved");
      std::printf("{\"ok\":true,\"verified\":true,\"faultRejected\":true,"
                  "\"case\":\"%s\"}\n",
                  mode.c_str());
      return 0;
    }
    if (rejected)
      throw std::runtime_error("no-op write unexpectedly rejected");
    auto actual = SaveFile::load(output);
    auto result = verify::Expected(*expected, *actual);
    if (!result.identical())
      throw std::runtime_error(verify::Describe(result));
    std::puts("{\"ok\":true,\"verified\":true,\"diskRoundtrip\":true}");
    return 0;
  } catch (const std::exception &e) {
    std::fprintf(stderr, "%s\n", e.what());
    return 2;
  }
}
