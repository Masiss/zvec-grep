cmake_minimum_required(VERSION 3.16)
project(m4_incremental_native_index VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

find_path(M4_LZ4_INCLUDE_DIR lz4.h)
find_library(M4_LZ4_LIBRARY NAMES lz4)
if(NOT M4_LZ4_INCLUDE_DIR OR NOT M4_LZ4_LIBRARY)
  message(FATAL_ERROR "LZ4 headers and library are required for the M4 WAL codec")
endif()

add_library(m4_research STATIC
  src/index.cc
  src/format.cc
  src/stream_merge.cc
)
target_include_directories(m4_research
  PUBLIC  ${CMAKE_CURRENT_SOURCE_DIR}/include
  PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src
)
target_compile_options(m4_research PRIVATE -Wall -Wextra)
target_include_directories(m4_research PRIVATE ${M4_LZ4_INCLUDE_DIR})
target_link_libraries(m4_research PUBLIC ${M4_LZ4_LIBRARY})

# Optional compatibility mode: compile pinned native Zvec tokenizer and
# lowercase-filter sources while keeping M4 files and storage research-only.
set(M4_ZVEC_ROOT "" CACHE PATH "Native Zvec source checkout")
set(M4_CPPJIEBA_INCLUDE_OVERLAY "" CACHE PATH
  "Optional research-only overlay placed before pinned cppjieba headers")
if(M4_CPPJIEBA_INCLUDE_OVERLAY AND NOT M4_ZVEC_ROOT)
  message(FATAL_ERROR "M4_CPPJIEBA_INCLUDE_OVERLAY requires M4_ZVEC_ROOT")
endif()
if(M4_ZVEC_ROOT)
  find_library(M4_ZVEC_LIBRARY
    NAMES zvec_c_api
    PATHS "${M4_ZVEC_ROOT}/../zvec-native-build/lib"
    NO_DEFAULT_PATH
    REQUIRED
  )
  set(M4_ZVEC_BUILD_ROOT "${M4_ZVEC_ROOT}/../zvec-native-build")
  find_library(M4_UTF8PROC_LIBRARY
    NAMES utf8proc
    PATHS "${M4_ZVEC_BUILD_ROOT}/lib"
    NO_DEFAULT_PATH
    REQUIRED
  )
  find_library(M4_ZVEC_AILEGO_LIBRARY
    NAMES zvec_ailego
    PATHS "${M4_ZVEC_BUILD_ROOT}/lib"
    NO_DEFAULT_PATH
    REQUIRED
  )
  target_sources(m4_research PRIVATE
    src/native_tokenizer_adapter.cc
    src/native_global_config_link_shim.cc
    "${M4_ZVEC_ROOT}/src/db/index/column/fts_column/tokenizer/jieba_tokenizer.cc"
    "${M4_ZVEC_ROOT}/src/db/index/column/fts_column/tokenizer/standard_tokenizer.cc"
    "${M4_ZVEC_ROOT}/src/db/index/column/fts_column/tokenizer/unicode_utils.cc"
    "${M4_ZVEC_ROOT}/src/db/index/column/fts_column/tokenizer/token_filter.cc"
  )
  target_compile_definitions(m4_research PUBLIC M4_USE_ZVEC_NATIVE_TOKENIZER=1)
  target_include_directories(m4_research PRIVATE
    "${M4_ZVEC_ROOT}/src"
    "${M4_ZVEC_ROOT}/src/include"
    "${M4_ZVEC_ROOT}/thirdparty/utf8proc/utf8proc-2.11.3"
  )
  if(M4_CPPJIEBA_INCLUDE_OVERLAY)
    target_include_directories(m4_research SYSTEM BEFORE PRIVATE
      "${M4_CPPJIEBA_INCLUDE_OVERLAY}"
    )
  endif()
  target_include_directories(m4_research SYSTEM PRIVATE
    "${M4_ZVEC_ROOT}/thirdparty/cppjieba/cppjieba-5.6.7/include"
    "${M4_ZVEC_ROOT}/thirdparty/limonp/limonp-v1.0.2/include"
  )
  target_link_libraries(m4_research PUBLIC
    "${M4_ZVEC_LIBRARY}"
    "${M4_ZVEC_AILEGO_LIBRARY}"
    "${M4_UTF8PROC_LIBRARY}"
  )
  set_target_properties(m4_research PROPERTIES
    BUILD_RPATH "${M4_ZVEC_ROOT}/../zvec-native-build/lib"
  )
endif()

add_executable(m4_correctness tests/m4_correctness.cc)
target_link_libraries(m4_correctness PRIVATE m4_research)
target_include_directories(m4_correctness PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)

add_executable(m4_benchmark tests/m4_benchmark.cc)
target_link_libraries(m4_benchmark PRIVATE m4_research)

find_package(Threads REQUIRED)

if(M4_CPPJIEBA_INCLUDE_OVERLAY)
  configure_file(
    tests/cppjieba_cache_lifecycle_overlay.cc.in
    "${CMAKE_CURRENT_BINARY_DIR}/cppjieba_cache_lifecycle_overlay.cc"
    COPYONLY
  )
  add_executable(m4_cppjieba_cache_lifecycle_overlay
    "${CMAKE_CURRENT_BINARY_DIR}/cppjieba_cache_lifecycle_overlay.cc")
  target_include_directories(m4_cppjieba_cache_lifecycle_overlay SYSTEM PRIVATE
    "${M4_CPPJIEBA_INCLUDE_OVERLAY}"
    "${M4_ZVEC_ROOT}/thirdparty/cppjieba/cppjieba-5.6.7/include/cppjieba"
    "${M4_ZVEC_ROOT}/thirdparty/cppjieba/cppjieba-5.6.7/include"
    "${M4_ZVEC_ROOT}/thirdparty/limonp/limonp-v1.0.2/include"
  )
  target_compile_definitions(m4_cppjieba_cache_lifecycle_overlay PRIVATE
    CPPJIEBA_CACHE_LIFECYCLE_TESTING=1)
  target_compile_options(m4_cppjieba_cache_lifecycle_overlay PRIVATE
    -Wall -Wextra -Werror)
  target_link_libraries(m4_cppjieba_cache_lifecycle_overlay PRIVATE Threads::Threads)
  add_test(NAME m4_cppjieba_cache_lifecycle_overlay
    COMMAND m4_cppjieba_cache_lifecycle_overlay)
endif()

# Bounded diagnostic for writer tail latency under concurrent compaction.
# Intentionally not a CTest target; run only through the contained harness.
add_executable(m4_compaction_writer_contention
  tests/m4_compaction_writer_contention.cc)
target_compile_options(m4_compaction_writer_contention PRIVATE
  -Wall -Wextra -Werror)
target_link_libraries(m4_compaction_writer_contention PRIVATE
  m4_research Threads::Threads)

add_executable(m4_writer_preferred_mutex_test
  tests/writer_preferred_shared_mutex_test.cc)
target_include_directories(m4_writer_preferred_mutex_test PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/src)
target_compile_options(m4_writer_preferred_mutex_test PRIVATE
  -Wall -Wextra -Werror -pthread)
target_link_libraries(m4_writer_preferred_mutex_test PRIVATE Threads::Threads)
add_test(NAME m4_writer_preferred_mutex_test
  COMMAND m4_writer_preferred_mutex_test)

if(M4_ZVEC_ROOT)
  add_executable(m4_native_fts_differential tests/native_fts_differential.cc)
  target_include_directories(m4_native_fts_differential PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/src"
    "${M4_ZVEC_ROOT}/src"
    "${M4_ZVEC_ROOT}/src/include"
  )
  target_link_libraries(m4_native_fts_differential PRIVATE m4_research)
  set_target_properties(m4_native_fts_differential PROPERTIES
    BUILD_RPATH "${M4_ZVEC_BUILD_ROOT}/lib"
  )

  add_executable(m4_native_bm25_isolation tests/native_bm25_isolation.cc)
  target_include_directories(m4_native_bm25_isolation PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/src"
    "${M4_ZVEC_ROOT}/src"
    "${M4_ZVEC_ROOT}/src/include"
  )
  target_link_libraries(m4_native_bm25_isolation PRIVATE m4_research)
  set_target_properties(m4_native_bm25_isolation PROPERTIES
    BUILD_RPATH "${M4_ZVEC_BUILD_ROOT}/lib"
  )

  add_executable(m4_native_uint32_differential
    tests/native_uint32_differential.cc)
  target_include_directories(m4_native_uint32_differential PRIVATE
    "${M4_ZVEC_ROOT}/src/include"
  )
  target_link_libraries(m4_native_uint32_differential PRIVATE m4_research)
  set_target_properties(m4_native_uint32_differential PROPERTIES
    BUILD_RPATH "${M4_ZVEC_BUILD_ROOT}/lib"
  )
  add_test(NAME m4_native_uint32_differential
    COMMAND m4_native_uint32_differential)

  add_executable(m4_native_array_uint32_differential
    tests/native_array_uint32_differential.cc)
  target_include_directories(m4_native_array_uint32_differential PRIVATE
    "${M4_ZVEC_ROOT}/src/include"
  )
  target_link_libraries(m4_native_array_uint32_differential PRIVATE m4_research)
  set_target_properties(m4_native_array_uint32_differential PROPERTIES
    BUILD_RPATH "${M4_ZVEC_BUILD_ROOT}/lib"
  )
  add_test(NAME m4_native_array_uint32_differential
    COMMAND m4_native_array_uint32_differential)

  add_executable(m4_native_boolean_filter_differential
    tests/native_boolean_filter_differential.cc)
  target_include_directories(m4_native_boolean_filter_differential PRIVATE
    "${M4_ZVEC_ROOT}/src/include"
  )
  target_link_libraries(m4_native_boolean_filter_differential PRIVATE m4_research)
  set_target_properties(m4_native_boolean_filter_differential PROPERTIES
    BUILD_RPATH "${M4_ZVEC_BUILD_ROOT}/lib"
  )
  add_test(NAME m4_native_boolean_filter_differential
    COMMAND m4_native_boolean_filter_differential)

  add_executable(m4_native_zg_workload_differential
    tests/native_zg_workload_differential.cc)
  target_include_directories(m4_native_zg_workload_differential PRIVATE
    "${M4_ZVEC_ROOT}/src/include"
  )
  target_link_libraries(m4_native_zg_workload_differential PRIVATE m4_research)
  set_target_properties(m4_native_zg_workload_differential PROPERTIES
    BUILD_RPATH "${M4_ZVEC_BUILD_ROOT}/lib"
  )
  add_test(NAME m4_native_zg_workload_differential
    COMMAND m4_native_zg_workload_differential)

  set(M4_ZVEC_JIEBA_DICT_DIR
    "${M4_ZVEC_ROOT}/thirdparty/cppjieba/cppjieba-5.6.7/dict")
  if(NOT EXISTS "${M4_ZVEC_JIEBA_DICT_DIR}/jieba.dict.utf8" OR
     NOT EXISTS "${M4_ZVEC_JIEBA_DICT_DIR}/hmm_model.utf8")
    message(FATAL_ERROR
      "Native Jieba differential requires dictionaries under ${M4_ZVEC_JIEBA_DICT_DIR}")
  endif()
  add_executable(m4_native_jieba_compatibility
    tests/native_jieba_compatibility.cc)
  target_include_directories(m4_native_jieba_compatibility PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/src"
    "${M4_ZVEC_ROOT}/src"
    "${M4_ZVEC_ROOT}/src/include"
  )
  target_include_directories(m4_native_jieba_compatibility SYSTEM PRIVATE
    "${M4_ZVEC_ROOT}/thirdparty/cppjieba/cppjieba-5.6.7/include"
    "${M4_ZVEC_ROOT}/thirdparty/limonp/limonp-v1.0.2/include"
  )
  target_compile_definitions(m4_native_jieba_compatibility PRIVATE
    M4_ZVEC_JIEBA_DICT_DIR=\"${M4_ZVEC_JIEBA_DICT_DIR}\"
  )
  target_link_libraries(m4_native_jieba_compatibility PRIVATE m4_research)
  set_target_properties(m4_native_jieba_compatibility PROPERTIES
    BUILD_RPATH "${M4_ZVEC_BUILD_ROOT}/lib"
  )
  add_test(NAME m4_native_jieba_compatibility
    COMMAND m4_native_jieba_compatibility)

  add_executable(m4_analyzer_cache_lifecycle
    tests/analyzer_cache_lifecycle.cc)
  target_include_directories(m4_analyzer_cache_lifecycle PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/src")
  target_compile_definitions(m4_analyzer_cache_lifecycle PRIVATE
    M4_ZVEC_JIEBA_DICT_DIR=\"${M4_ZVEC_JIEBA_DICT_DIR}\")
  target_compile_options(m4_analyzer_cache_lifecycle PRIVATE -Wall -Wextra -Werror)
  target_link_libraries(m4_analyzer_cache_lifecycle PRIVATE
    m4_research Threads::Threads)
  set_target_properties(m4_analyzer_cache_lifecycle PROPERTIES
    BUILD_RPATH "${M4_ZVEC_BUILD_ROOT}/lib")
  add_test(NAME m4_analyzer_cache_lifecycle
    COMMAND m4_analyzer_cache_lifecycle)

  # One-process-per-arm research harness. Not registered with CTest because it
  # creates persistent index directories and is intentionally run by the
  # paired host/resource profiler.
  add_executable(native_m4_paired_benchmark
    tests/native_m4_paired_benchmark.cc)
  target_include_directories(native_m4_paired_benchmark PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}/src"
    "${M4_ZVEC_ROOT}/src/include"
  )
  find_package(OpenSSL REQUIRED COMPONENTS Crypto)
  target_link_libraries(native_m4_paired_benchmark PRIVATE
    m4_research OpenSSL::Crypto)
  set_target_properties(native_m4_paired_benchmark PROPERTIES
    BUILD_RPATH "${M4_ZVEC_BUILD_ROOT}/lib"
  )

  add_executable(m4_native_scalar_open_resource_rehearsal
    tests/native_scalar_open_resource_rehearsal.cc)
  target_include_directories(m4_native_scalar_open_resource_rehearsal PRIVATE
    "${M4_ZVEC_ROOT}/src/include"
  )
  target_link_libraries(m4_native_scalar_open_resource_rehearsal PRIVATE m4_research)
  set_target_properties(m4_native_scalar_open_resource_rehearsal PROPERTIES
    BUILD_RPATH "${M4_ZVEC_BUILD_ROOT}/lib"
  )

  add_executable(m4_native_scalar_open_latency_diagnostic
    tests/native_scalar_open_resource_rehearsal.cc)
  target_include_directories(m4_native_scalar_open_latency_diagnostic PRIVATE
    "${M4_ZVEC_ROOT}/src/include"
  )
  target_link_libraries(m4_native_scalar_open_latency_diagnostic PRIVATE m4_research)
  set_target_properties(m4_native_scalar_open_latency_diagnostic PROPERTIES
    BUILD_RPATH "${M4_ZVEC_BUILD_ROOT}/lib"
  )

  add_executable(m4_bridge_integration tests/m4_bridge_integration.cc)
  target_include_directories(m4_bridge_integration PRIVATE
    "${M4_ZVEC_ROOT}/src/include"
  )
  target_compile_options(m4_bridge_integration PRIVATE -Wall -Wextra -Werror -pthread)
  target_link_libraries(m4_bridge_integration PRIVATE m4_research Threads::Threads)
  set_target_properties(m4_bridge_integration PROPERTIES
    BUILD_RPATH "${M4_ZVEC_BUILD_ROOT}/lib"
  )

endif()

enable_testing()
add_test(NAME m4_correctness COMMAND m4_correctness)
