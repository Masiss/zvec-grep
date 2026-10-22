// Pluggable tokenizer boundary for the M4 research prototype.
//
// Standalone mode uses the small built-in tokenizer. The optional
// M4_USE_ZVEC_NATIVE_TOKENIZER research build compiles the checked-out native
// standard/Jieba tokenizer and lowercase-filter sources for compatibility;
// no production Zvec source, library, ABI, or index format is modified.

#pragma once

#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>
#ifndef M4_USE_ZVEC_NATIVE_TOKENIZER
#include <cctype>
#endif

#include "m4/index.h"

namespace m4 {

struct Token {
  std::string term;
  uint32_t pos{0};  // analyzer output-sequence position
  uint32_t offset{0};  // byte offset where available; not persisted in segments
};

struct AnalyzerDictionaryFileSnapshot {
  std::string filename;
  std::string contents;
};

struct AnalyzerCacheStats {
  uint64_t lookups{0};
  uint64_t lookup_hits{0};
  uint64_t lookup_misses{0};
  uint64_t expired_weak_entries{0};
  uint64_t initialization_attempts{0};
  uint64_t successful_initializations{0};
  uint64_t late_race_reuses{0};
  uint64_t cache_waits{0};
  uint64_t cache_wait_reuses{0};
  uint64_t cache_bypasses{0};
};

struct AnalyzerInitDiagnostics {
  bool cache_lookup{false};
  bool cache_miss{false};
  bool initial_cache_hit{false};
  bool expired_weak_entry{false};
  bool late_race_reuse{false};
  bool cache_waited{false};
  bool cache_wait_reuse{false};
  bool dictionary_snapshot_used{false};
  bool initialization_attempted{false};
  bool initialization_succeeded{false};
};

namespace analyzer_test {
void SetAfterCacheMissHook(std::function<void()> hook);
void SetBeforeNativeInitializationHook(
    std::function<void(const std::string&)> hook);
void SetAfterInitializationHook(std::function<void()> hook);
}  // namespace analyzer_test

class Analyzer {
 public:
  Analyzer();
  ~Analyzer();
  Analyzer(const Analyzer&) = delete;
  Analyzer& operator=(const Analyzer&) = delete;

  Status Init(const std::string& tokenizer_name,
              const std::string& jieba_dict_dir,
              const std::string& cache_identity = {},
              bool* cache_hit = nullptr,
              AnalyzerInitDiagnostics* diagnostics = nullptr,
              const std::vector<AnalyzerDictionaryFileSnapshot>* dictionary_snapshot = nullptr,
              const std::string& dictionary_snapshot_identity = {});
  std::vector<Token> Tokenize(const std::string& text) const;

  static AnalyzerCacheStats CacheStats();

 private:
  friend void analyzer_test::SetAfterCacheMissHook(std::function<void()> hook);
  friend void analyzer_test::SetBeforeNativeInitializationHook(
      std::function<void(const std::string&)> hook);
  friend void analyzer_test::SetAfterInitializationHook(std::function<void()> hook);
  struct Impl;
  struct CacheRegistry;
  static CacheRegistry& Registry();
  std::shared_ptr<Impl> impl_;
};

// Deterministic scheduling hooks for the research-only concurrent cache tests.
// Hooks run outside the cache mutex and must be cleared after worker joins.
#ifdef M4_USE_ZVEC_NATIVE_TOKENIZER
std::vector<Token> TokenizeWithNativeZvec(const std::string& text);

inline std::vector<Token> Tokenize(const std::string& text) {
  return TokenizeWithNativeZvec(text);
}
#else
inline std::vector<Token> Tokenize(const std::string& text) {
  std::vector<Token> out;
  std::string cur;
  uint32_t pos = 0;
  uint32_t start_offset = 0;
  auto flush = [&]() {
    if (cur.empty()) return;
    out.push_back(Token{cur, pos, start_offset});
    ++pos;
    cur.clear();
  };
  for (size_t i = 0; i < text.size(); ++i) {
    const unsigned char c = static_cast<unsigned char>(text[i]);
    if (c < 0x80) {
      if (std::isalnum(c) || c == '_') {
        if (cur.empty()) start_offset = static_cast<uint32_t>(i);
        cur.push_back(static_cast<char>(std::tolower(c)));
      } else {
        flush();
      }
    } else {
      if (cur.empty()) start_offset = static_cast<uint32_t>(i);
      // Keep UTF-8 bytes as part of the token; the prototype does no case
      // folding or segmentation for non-ASCII text.
      cur.push_back(static_cast<char>(c));
    }
  }
  flush();
  return out;
}
#endif

}  // namespace m4
