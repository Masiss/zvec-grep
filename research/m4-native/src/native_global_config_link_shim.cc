// Research-only link shim for the native JiebaTokenizer source. Its
// per-field jieba_dict_dir is always explicitly populated by Analyzer::Init,
// so the native process-wide fallback accessors below are unreachable. The
// linked zvec C API has a separate hidden GlobalConfig and shares no state.
#include <zvec/db/config.h>

#if defined(__GNUC__)
#define M4_HIDDEN __attribute__((visibility("hidden")))
#else
#define M4_HIDDEN
#endif

namespace zvec {

M4_HIDDEN GlobalConfig::ConfigData::ConfigData()
    : memory_limit_bytes(0),
      log_config(std::make_shared<ConsoleLogConfig>()),
      query_thread_count(1),
      query_thread_binding(false),
      invert_to_forward_scan_ratio(0.9f),
      brute_force_by_keys_ratio(0.1f),
      fts_brute_force_by_keys_ratio(0.05f),
      optimize_thread_count(1),
      optimize_thread_binding(false),
      jieba_dict_dir() {}

M4_HIDDEN std::string GlobalConfig::jieba_dict_dir() const { return {}; }

}  // namespace zvec

#undef M4_HIDDEN
