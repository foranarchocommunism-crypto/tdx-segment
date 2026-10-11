cmake_minimum_required(VERSION 3.24)

project(TDXSegment VERSION 0.1.0 LANGUAGES CXX)
set(TDX_RELEASE_VERSION "0.1.0-rc.2")

if(NOT WIN32 OR NOT MSVC)
    message(FATAL_ERROR "TDXSegment must be built on Windows with MSVC.")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(
    CMAKE_MSVC_RUNTIME_LIBRARY
    "MultiThreaded$<$<CONFIG:Debug>:Debug>"
)

set(
    TDX_VENDOR_INCLUDE_DIR
    "${CMAKE_CURRENT_SOURCE_DIR}/compat/tdx"
    CACHE PATH
    "Directory containing the vendor PluginTCalcFunc.h"
)
if(NOT EXISTS "${TDX_VENDOR_INCLUDE_DIR}/PluginTCalcFunc.h")
    message(FATAL_ERROR
        "PluginTCalcFunc.h was not found in TDX_VENDOR_INCLUDE_DIR="
        "${TDX_VENDOR_INCLUDE_DIR}"
    )
endif()

function(tdx_enable_strict_warnings target_name)
    target_compile_options(
        ${target_name}
        PRIVATE
            /W4
            /WX
            /permissive-
            /utf-8
            /EHsc
    )
    target_compile_definitions(
        ${target_name}
        PRIVATE
            WIN32_LEAN_AND_MEAN
            NOMINMAX
    )
endfunction()

add_library(tdx_segment_core STATIC
    src/segment_engine.cpp
    src/tdx_segment.cpp
    src/feature_sequences.cpp
    src/pen_snapshot.cpp
    src/input_decoder.cpp
    src/event_encoder.cpp
    src/segment_evaluator.cpp
    src/long_stall_recovery.cpp
    src/pen_formula.cpp
)
target_include_directories(
    tdx_segment_core
    PUBLIC
        "${CMAKE_CURRENT_SOURCE_DIR}/include"
)
tdx_enable_strict_warnings(tdx_segment_core)

add_library(TDXSegment SHARED
    src/tdx_plugin_entry.cpp
)
target_link_libraries(TDXSegment PRIVATE tdx_segment_core)
target_include_directories(
    TDXSegment
    PRIVATE
        "${TDX_VENDOR_INCLUDE_DIR}"
)
tdx_enable_strict_warnings(TDXSegment)
set_target_properties(TDXSegment PROPERTIES
    OUTPUT_NAME "TDXSegment"
    DEBUG_POSTFIX "d"
)

# 通达信按字面名称 GetProcAddress("RegisterTdxFunc")。MSVC 链接器在 x86
# 下会把这个 C 名称解析到对象文件中的 _RegisterTdxFunc，同时把导出名
# 保持为无修饰形式；x64 使用同一选项即可。
target_link_options(TDXSegment PRIVATE "/EXPORT:RegisterTdxFunc")

include(CTest)
if(BUILD_TESTING)

add_executable(tdx_vendor_abi_layout_selftest
    tests/tdx_vendor_abi_layout_selftest.cpp
)
target_include_directories(
    tdx_vendor_abi_layout_selftest
    PRIVATE
        "${TDX_VENDOR_INCLUDE_DIR}"
)
tdx_enable_strict_warnings(tdx_vendor_abi_layout_selftest)
add_test(
    NAME vendor_abi_layout_selftest
    COMMAND tdx_vendor_abi_layout_selftest
)

add_executable(tdx_segment_core_selftest
    tests/tdx_segment_core_selftest.cpp
)
target_link_libraries(tdx_segment_core_selftest PRIVATE tdx_segment_core)
tdx_enable_strict_warnings(tdx_segment_core_selftest)
add_test(NAME core_selftest COMMAND tdx_segment_core_selftest)

# Test private A selection with doubles; the production library has no test exports.
add_executable(tdx_primary_left_a_scope_selftest
    tests/tdx_primary_left_a_scope_selftest.cpp
)
target_include_directories(tdx_primary_left_a_scope_selftest PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${CMAKE_CURRENT_SOURCE_DIR}/src"
)
target_link_libraries(tdx_primary_left_a_scope_selftest PRIVATE tdx_segment_core)
tdx_enable_strict_warnings(tdx_primary_left_a_scope_selftest)
add_test(NAME primary_left_a_scope_selftest COMMAND tdx_primary_left_a_scope_selftest)

add_executable(tdx_pen_decoder_selftest
    tests/tdx_pen_decoder_selftest.cpp
)
target_link_libraries(tdx_pen_decoder_selftest PRIVATE tdx_segment_core)
target_include_directories(
    tdx_pen_decoder_selftest
    PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/src"
)
tdx_enable_strict_warnings(tdx_pen_decoder_selftest)
add_test(NAME pen_decoder_selftest COMMAND tdx_pen_decoder_selftest)

add_executable(tdx_plugin_load_selftest
    tests/tdx_plugin_load_selftest.cpp
)
target_include_directories(
    tdx_plugin_load_selftest
    PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/include"
        "${TDX_VENDOR_INCLUDE_DIR}"
)
tdx_enable_strict_warnings(tdx_plugin_load_selftest)
add_test(
    NAME plugin_load_selftest
    COMMAND tdx_plugin_load_selftest "$<TARGET_FILE:TDXSegment>"
)

# Link the production adapter against controlled calculators to exercise its
# exception boundary and atomic commit without a production test export.
add_executable(tdx_plugin_atomic_selftest
    tests/tdx_plugin_atomic_selftest.cpp
    src/tdx_plugin_entry.cpp
)
target_include_directories(tdx_plugin_atomic_selftest PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${TDX_VENDOR_INCLUDE_DIR}"
)
tdx_enable_strict_warnings(tdx_plugin_atomic_selftest)
add_test(NAME plugin_atomic_selftest COMMAND tdx_plugin_atomic_selftest)

add_executable(tdx_segment_vector_runner
    tests/tdx_segment_vector_runner.cpp
)
target_link_libraries(tdx_segment_vector_runner PRIVATE tdx_segment_core)
tdx_enable_strict_warnings(tdx_segment_vector_runner)

add_executable(tdx_segment_inprocess_benchmark
    tests/tdx_segment_inprocess_benchmark.cpp
)
target_link_libraries(
    tdx_segment_inprocess_benchmark
    PRIVATE tdx_segment_core
)
tdx_enable_strict_warnings(tdx_segment_inprocess_benchmark)
add_test(
    NAME many_short_segments_performance_regression
    COMMAND tdx_segment_inprocess_benchmark
)
set_tests_properties(
    many_short_segments_performance_regression
    PROPERTIES TIMEOUT 30
)

add_executable(tdx_segment_audit_runner
    tests/tdx_segment_audit_runner.cpp
)
target_link_libraries(tdx_segment_audit_runner PRIVATE tdx_segment_core)
target_include_directories(
    tdx_segment_audit_runner
    PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src"
)
tdx_enable_strict_warnings(tdx_segment_audit_runner)

find_package(Python3 3.12 COMPONENTS Interpreter REQUIRED)
add_test(
    NAME python_cpp_reference_crosscheck
    COMMAND
        "${Python3_EXECUTABLE}"
        -X utf8
        -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/crosscheck_reference.py"
        --runner "$<TARGET_FILE:tdx_segment_vector_runner>"
        --reference
        "${CMAKE_CURRENT_SOURCE_DIR}/reference/segment_reference.py"
        --random-cases 150
        --seed 20260828
)
set_tests_properties(
    python_cpp_reference_crosscheck
    PROPERTIES TIMEOUT 180
)

add_test(
    NAME python_cpp_audit_crosscheck
    COMMAND
        "${Python3_EXECUTABLE}"
        -X utf8
        -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/crosscheck_audit.py"
        --runner "$<TARGET_FILE:tdx_segment_audit_runner>"
        --reference
        "${CMAKE_CURRENT_SOURCE_DIR}/reference/segment_reference.py"
        --random-cases 150
        --seed 20260828
)
set_tests_properties(
    python_cpp_audit_crosscheck
    PROPERTIES TIMEOUT 180
)

add_test(
    NAME window_growth_performance_regression
    COMMAND
        "${Python3_EXECUTABLE}"
        -X utf8
        -B
        "${CMAKE_CURRENT_SOURCE_DIR}/tests/window_growth_benchmark.py"
        --runner "$<TARGET_FILE:tdx_segment_vector_runner>"
        --mode prepend
        --lengths 2048,4096,8192,16384
        --bars-per-pen 4
        --calls-per-sample 1
        --repetitions 5
        --max-exponent 1.35
)
set_tests_properties(
    window_growth_performance_regression
    PROPERTIES TIMEOUT 60
)



add_executable(tdx_recovery_runner tests/tdx_recovery_runner.cpp)
target_link_libraries(tdx_recovery_runner PRIVATE tdx_segment_core)
target_include_directories(tdx_recovery_runner PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src")
tdx_enable_strict_warnings(tdx_recovery_runner)

add_executable(tdx_audit_seam_selftest tests/tdx_audit_seam_selftest.cpp)
target_link_libraries(tdx_audit_seam_selftest PRIVATE tdx_segment_core)
target_include_directories(tdx_audit_seam_selftest PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src")
tdx_enable_strict_warnings(tdx_audit_seam_selftest)
add_test(NAME audit_seam_selftest COMMAND tdx_audit_seam_selftest)

set(TDX_PENDING_DEFAULT_RECOVERY 0 CACHE STRING "Default recovery mode for pending report runner")
set(TDX_PENDING_DEFAULT_RESIDENCE 9 CACHE STRING "Default residence threshold for pending report runner")
add_executable(tdx_pending_runner tests/tdx_pending_runner.cpp)
target_link_libraries(tdx_pending_runner PRIVATE tdx_segment_core)
target_include_directories(tdx_pending_runner PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_compile_definitions(tdx_pending_runner PRIVATE
    TDX_PENDING_DEFAULT_RECOVERY=${TDX_PENDING_DEFAULT_RECOVERY}
    TDX_PENDING_DEFAULT_RESIDENCE=${TDX_PENDING_DEFAULT_RESIDENCE})
tdx_enable_strict_warnings(tdx_pending_runner)

add_executable(final_pending_probe tests/tdx_pending_runner.cpp)
target_link_libraries(final_pending_probe PRIVATE tdx_segment_core)
target_include_directories(final_pending_probe PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_compile_definitions(final_pending_probe PRIVATE
    TDX_PENDING_SECOND_FORMAT=1 TDX_PENDING_DEFAULT_RECOVERY=1 TDX_PENDING_DEFAULT_RESIDENCE=13)
tdx_enable_strict_warnings(final_pending_probe)

add_executable(tdx_refactor_benchmark tests/tdx_refactor_benchmark.cpp)
target_link_libraries(tdx_refactor_benchmark PRIVATE tdx_segment_core Psapi)
tdx_enable_strict_warnings(tdx_refactor_benchmark)

add_executable(tdx_refactor_allocation_benchmark tests/tdx_refactor_benchmark.cpp)
target_link_libraries(tdx_refactor_allocation_benchmark PRIVATE tdx_segment_core Psapi)
target_compile_definitions(tdx_refactor_allocation_benchmark PRIVATE TDX_MEASURE_ALLOCATIONS=1)
tdx_enable_strict_warnings(tdx_refactor_allocation_benchmark)

add_executable(tdx_pen_range_selftest tests/tdx_pen_range_selftest.cpp)
target_link_libraries(tdx_pen_range_selftest PRIVATE tdx_segment_core)
target_include_directories(tdx_pen_range_selftest PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src")
tdx_enable_strict_warnings(tdx_pen_range_selftest)
add_test(NAME pen_range_selftest COMMAND tdx_pen_range_selftest)

add_executable(tdx_evaluation_continuation_selftest
    tests/tdx_evaluation_continuation_selftest.cpp
)
target_link_libraries(tdx_evaluation_continuation_selftest PRIVATE tdx_segment_core)
target_include_directories(tdx_evaluation_continuation_selftest PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/src"
)
tdx_enable_strict_warnings(tdx_evaluation_continuation_selftest)
add_test(NAME evaluation_continuation_selftest COMMAND tdx_evaluation_continuation_selftest)

add_executable(tdx_dll_runner tests/tdx_dll_runner.cpp)
target_include_directories(tdx_dll_runner PRIVATE "${TDX_VENDOR_INCLUDE_DIR}")
tdx_enable_strict_warnings(tdx_dll_runner)
set_tests_properties(many_short_segments_performance_regression window_growth_performance_regression PROPERTIES LABELS "performance")
add_executable(tdx_pen_formula_selftest tests/tdx_pen_formula_selftest.cpp)
target_link_libraries(tdx_pen_formula_selftest PRIVATE tdx_segment_core)
target_include_directories(tdx_pen_formula_selftest PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src")
tdx_enable_strict_warnings(tdx_pen_formula_selftest)
add_test(NAME pen_formula_selftest COMMAND tdx_pen_formula_selftest)
add_test(NAME pen_formula_dll_crosscheck COMMAND "${Python3_EXECUTABLE}" -X utf8 -B
    "${CMAKE_CURRENT_SOURCE_DIR}/tests/crosscheck_pen_formula.py"
    --runner "$<TARGET_FILE:tdx_dll_runner>" --dll "$<TARGET_FILE:TDXSegment>" --random-cases 150)
set_tests_properties(pen_formula_dll_crosscheck PROPERTIES TIMEOUT 180)

endif()

install(TARGETS TDXSegment RUNTIME DESTINATION .)
