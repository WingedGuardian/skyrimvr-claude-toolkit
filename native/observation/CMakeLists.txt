# Native tests for the observation layer: synthetic fixtures only. The tests compile the headers in this source tree;
# MinHook comes from the qualified SDK prefix. Configure with -DCMAKE_PREFIX_PATH=<sdk prefix>.
cmake_minimum_required(VERSION 3.25)
project(SkyrimNativeObservationTests LANGUAGES CXX ASM_MASM)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreadedDLL)
find_package(minhook CONFIG REQUIRED)
enable_testing()
set(flags /W4 /WX /EHsc /O2 /utf-8)
add_compile_definitions(NOMINMAX WINVER=0x0601 _WIN32_WINNT=0x0601)
include_directories("${CMAKE_CURRENT_SOURCE_DIR}/include")
set_property(SOURCE tests/bridges.asm APPEND PROPERTY COMPILE_OPTIONS "/I${CMAKE_CURRENT_SOURCE_DIR}/asm" /WX)

add_executable(gate-tests tests/gate_tests.cpp)
target_compile_options(gate-tests PRIVATE ${flags})
add_test(NAME capture-gate COMMAND gate-tests)

add_executable(bridge-tests tests/bridge_tests.cpp tests/capture.asm tests/bridges.asm)
target_compile_options(bridge-tests PRIVATE "$<$<COMPILE_LANGUAGE:CXX>:${flags}>")
target_compile_definitions(bridge-tests PRIVATE SKYRIM_NATIVE_HAVE_BRIDGES=1)
add_test(NAME raw-context-bridge COMMAND bridge-tests)

add_executable(entry-tests tests/entry_tests.cpp tests/entry_fixture.asm tests/entry_abi.asm)
target_compile_options(entry-tests PRIVATE "$<$<COMPILE_LANGUAGE:CXX>:${flags}>")
target_link_libraries(entry-tests PRIVATE minhook::minhook)
add_test(NAME checked-entry-hook COMMAND entry-tests)
