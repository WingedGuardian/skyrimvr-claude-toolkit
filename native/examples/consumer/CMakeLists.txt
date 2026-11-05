# The offline consumer build: everything comes through the SDK's exported targets. Configure with
# -DCMAKE_PREFIX_PATH=<sdk prefix>. This is also what `sdk.py qualify` links for real.
cmake_minimum_required(VERSION 3.25)
project(SkyrimNativeExample LANGUAGES CXX ASM_MASM VERSION 0.1.0)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreadedDLL)
find_package(SkyrimNativeSdk CONFIG REQUIRED)
enable_testing()

set(flags /W4 /WX /permissive- /Zc:preprocessor /Zc:__cplusplus /utf-8 /EHsc /bigobj /external:anglebrackets /external:W0)
add_library(SkyrimNativeExample SHARED src/plugin.cpp)
target_compile_options(SkyrimNativeExample PRIVATE "$<$<COMPILE_LANGUAGE:CXX>:${flags}>")
target_compile_definitions(SkyrimNativeExample PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
target_link_libraries(SkyrimNativeExample PRIVATE SkyrimNativeSdk::CommonLib SkyrimNativeSdk::MinHook SkyrimNativeSdk::Observation)
skyrim_native_add_bridges(SkyrimNativeExample "${CMAKE_CURRENT_SOURCE_DIR}/src/bridges.asm")

add_executable(example-config-tests src/config_tests.cpp)
target_compile_options(example-config-tests PRIVATE /W4 /WX /EHsc /utf-8)
target_compile_definitions(example-config-tests PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
target_link_libraries(example-config-tests PRIVATE SkyrimNativeSdk::MinHook SkyrimNativeSdk::Observation)
add_test(NAME example-site-config COMMAND example-config-tests)
