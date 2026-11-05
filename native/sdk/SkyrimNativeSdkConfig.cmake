# Consumer entry point. Stable targets only:
#   SkyrimNativeSdk::CommonLib    CommonLib VR-only static library and its pinned dependencies
#   SkyrimNativeSdk::MinHook      MinHook static library
#   SkyrimNativeSdk::Observation  observation headers; assemble bridges with skyrim_native_add_bridges()
# Nothing here may point at an experiment folder or another session's build.
include(CMakeFindDependencyMacro)
get_filename_component(_skn_prefix "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
file(GLOB _skn_triplet LIST_DIRECTORIES true "${_skn_prefix}/deps/x64-*")
list(PREPEND CMAKE_PREFIX_PATH "${_skn_prefix}" ${_skn_triplet})
find_dependency(CommonLibSSE CONFIG)
find_dependency(minhook CONFIG)
foreach(_skn_target CommonLib MinHook Observation)
    if(NOT TARGET SkyrimNativeSdk::${_skn_target})
        add_library(SkyrimNativeSdk::${_skn_target} INTERFACE IMPORTED)
    endif()
endforeach()
target_link_libraries(SkyrimNativeSdk::CommonLib INTERFACE CommonLibSSE::CommonLibSSE)
target_link_libraries(SkyrimNativeSdk::MinHook INTERFACE minhook::minhook)
target_include_directories(SkyrimNativeSdk::Observation INTERFACE "${_skn_prefix}/observation/include")
set(SkyrimNativeSdk_OBSERVATION_DIR "${_skn_prefix}/observation" CACHE INTERNAL "")

# skyrim_native_add_bridges(<target> <consumer bridges.asm>): the consumer's asm INCLUDEs bridge_common.inc. It assembles
# in its own object library, because CommonLib's exported compile flags are C++ flags that MASM rejects.
function(skyrim_native_add_bridges target asm)
    add_library(${target}_bridges OBJECT "${asm}")
    target_compile_options(${target}_bridges PRIVATE "/I${SkyrimNativeSdk_OBSERVATION_DIR}/asm" /WX)
    target_sources(${target} PRIVATE $<TARGET_OBJECTS:${target}_bridges>)
endfunction()
