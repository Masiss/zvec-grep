// Research-only paired native Zvec vs M4 FTS/INVERT workload.
// One arm per process; no vector index, native optimize, or M4 CompactNow.
#include <algorithm>
#include <array>
#include <chrono>
#include <cctype>
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <numeric>
#include <openssl/sha.h>
#include <set>
#include <sstream>
#include <string>
#include <unordered_map>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <utility>
#include <vector>

#include "m4/index.h"
#include "../src/tokenizer.h"
#include <zvec/c_api.h>

namespace fs = std::filesystem;
namespace {
using Clock = std::chrono::steady_clock;
int failures = 0;

struct Row {
  uint32_t file_id{0};
  std::string pk;
  std::string entity_id;
  std::string file_name;
  std::string text;
  std::string symbol_name;
  std::string symbol_type;
  std::vector<uint32_t> ancestors;
  uint32_t token_count{0};
  uint32_t oracle_token_mask{0};
};
struct File {
  uint32_t id{0};
  uint32_t generation{0};
  char kind{'j'};
  std::vector<Row> rows;
};
struct Hit { std::string pk; float score{0}; };
struct Query {
  std::string name;
  std::string text;
  std::string sql;
  m4::FilterExpr filter;
};
struct Counters { uint64_t read_bytes{0}; uint64_t write_bytes{0}; };

[[noreturn]] void Die(const std::string& message) {
  std::cerr << "FATAL\t" << message << '\n';
  std::exit(2);
}
void Require(bool ok, const std::string& message) {
  if (!ok) {
    ++failures;
    Die(message);
  }
}
std::string NativeError(zvec_error_code_t status, const std::string& context) {
  zvec_error_details_t details{};
  zvec_get_last_error_details(&details);
  return context + " code=" + zvec_error_code_to_string(status) +
         " message=" + (details.message ? details.message : "");
}
void NativeRequire(zvec_error_code_t status, const std::string& context) {
  Require(status == ZVEC_OK, NativeError(status, context));
}
uint64_t ReadProcIo(const char* key) {
  std::ifstream in("/proc/self/io");
  std::string line;
  while (std::getline(in, line)) {
    if (line.rfind(key, 0) == 0)
      return std::strtoull(line.c_str() + std::char_traits<char>::length(key), nullptr, 10);
  }
  return 0;
}
Counters Io() { return {ReadProcIo("read_bytes:"), ReadProcIo("write_bytes:")}; }
struct ProcMemorySnapshot {
  uint64_t rss_bytes{0};
  uint64_t pss_bytes{0};
  uint64_t private_dirty_bytes{0};
  uint64_t vmhwm_bytes{0};
};
ProcMemorySnapshot ReadProcMemorySnapshot() {
  ProcMemorySnapshot memory;
  bool have_rss = false;
  bool have_vmhwm = false;
  std::ifstream status("/proc/self/status");
  std::string line;
  while (std::getline(status, line)) {
    std::istringstream fields(line);
    std::string key;
    uint64_t kib = 0;
    if (!(fields >> key >> kib))
      continue;
    if (key == "VmRSS:") {
      memory.rss_bytes = kib * 1024;
      have_rss = true;
    } else if (key == "VmHWM:") {
      memory.vmhwm_bytes = kib * 1024;
      have_vmhwm = true;
    }
  }
  bool have_pss = false;
  bool have_private_dirty = false;
  std::ifstream smaps("/proc/self/smaps_rollup");
  while (std::getline(smaps, line)) {
    std::istringstream fields(line);
    std::string key;
    uint64_t kib = 0;
    if (!(fields >> key >> kib))
      continue;
    if (key == "Pss:") {
      memory.pss_bytes = kib * 1024;
      have_pss = true;
    } else if (key == "Private_Dirty:") {
      memory.private_dirty_bytes = kib * 1024;
      have_private_dirty = true;
    }
  }
  Require(have_rss && have_vmhwm && have_pss && have_private_dirty,
          "read process RSS/PSS/Private_Dirty from procfs");
  return memory;
}
double CpuMs() {
  timespec ts{};
  clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts);
  return static_cast<double>(ts.tv_sec) * 1000.0 + static_cast<double>(ts.tv_nsec) / 1e6;
}
uint64_t TextBytes(const std::vector<Row>& rows) {
  uint64_t bytes = 0;
  for (const auto& row : rows) bytes += row.text.size();
  return bytes;
}
int TraceFd() {
  static const int fd = [] {
    const char* value = std::getenv("M4_P41_TRACE_FD");
    if (value == nullptr || *value == '\0') return -1;
    char* end = nullptr;
    const long parsed = std::strtol(value, &end, 10);
    if (end == value || *end != '\0' || parsed < 3 ||
        parsed > std::numeric_limits<int>::max()) return -1;
    return static_cast<int>(parsed);
  }();
  return fd;
}
bool TraceEnabled() {
  const char* diagnostics = std::getenv("M4_P41_DIAGNOSTICS");
  return (diagnostics != nullptr && *diagnostics != '\0' &&
          std::string(diagnostics) != "0") || TraceFd() >= 3;
}
void TraceMarker(const std::string& event) {
  const int fd = TraceFd();
  if (fd < 3) return;
  const std::string marker = "P41|" + event + "\n";
  (void)::write(fd, marker.data(), marker.size());
}
void EmitMetric(const std::string& stage, const std::string& name, double value) {
  std::cout << "M\t" << stage << '\t' << name << '\t' << value << '\n';
}
void EmitLatency(const std::string& stage, const std::string& kind, double ms) {
  std::cout << "L\t" << stage << '\t' << kind << '\t' << ms << '\n';
}

template <typename Fn>
void Measure(const std::string& stage, Fn&& fn) {
  TraceMarker("PHASE_BEGIN|" + stage);
  const Counters before_io = Io();
  const double before_cpu = CpuMs();
  const auto start = Clock::now();
  fn();
  const auto end = Clock::now();
  const double cpu_ms = CpuMs() - before_cpu;
  const Counters after_io = Io();
  EmitMetric(stage, "wall_ms", std::chrono::duration<double, std::milli>(end - start).count());
  EmitMetric(stage, "cpu_ms", cpu_ms);
  EmitMetric(stage, "physical_write_bytes", static_cast<double>(after_io.write_bytes - before_io.write_bytes));
  TraceMarker("PHASE_END|" + stage);
}

uint64_t Hash(uint32_t file_id, uint32_t generation, uint32_t fragment) {
  uint64_t x = (static_cast<uint64_t>(file_id) << 32) ^
               (static_cast<uint64_t>(generation) << 16) ^ fragment ^ 0x9e3779b97f4a7c15ull;
  x ^= x >> 30; x *= 0xbf58476d1ce4e5b9ull;
  x ^= x >> 27; x *= 0x94d049bb133111ebull;
  return x ^ (x >> 31);
}
char KindFor(uint32_t file_id) {
  const uint32_t slot = file_id % 20;
  if (slot < 4) return 'j';       // 20% JSON
  if (slot < 8) return 'l';       // 20% logs
  if (slot < 15) return 's';      // 35% source code
  return 'm';                    // 25% Markdown / text
}
size_t BaseFragments(uint32_t id, uint32_t generation) {
  return 4 + (id * 7 + generation * 3) % 7;  // 4..10 fragments/file
}
std::string SymbolIndexValue(const std::string& name) {
  std::string input("symbol\0", 7);
  input += name;
  unsigned char digest[SHA256_DIGEST_LENGTH];
  SHA256(reinterpret_cast<const unsigned char*>(input.data()), input.size(), digest);
  static const char hex[] = "0123456789abcdef";
  std::string out(SHA256_DIGEST_LENGTH * 2, '0');
  for (size_t i = 0; i < SHA256_DIGEST_LENGTH; ++i) {
    out[i * 2] = hex[digest[i] >> 4];
    out[i * 2 + 1] = hex[digest[i] & 0x0f];
  }
  return out;
}
uint32_t OracleTokenBit(const std::string& term) {
  static const std::unordered_map<std::string, uint32_t> bits = {
      {"timeout", 1u << 0}, {"error", 1u << 1}, {"json", 1u << 2},
      {"parse", 1u << 3}, {"connection", 1u << 4}, {"refused", 1u << 5},
      {"cache", 1u << 6}, {"miss", 1u << 7}, {"retry", 1u << 8},
      {"request", 1u << 9}, {"symbol", 1u << 10}, {"resolver", 1u << 11},
      {"worker", 1u << 12}, {"status", 1u << 13}, {"database", 1u << 14}};
  const auto found = bits.find(term);
  return found == bits.end() ? 0u : found->second;
}
void AnalyzeRow(Row& row, const m4::Analyzer& analyzer) {
  row.token_count = 0;
  row.oracle_token_mask = 0;
  for (const auto& token : analyzer.Tokenize(row.text)) {
    ++row.token_count;
    row.oracle_token_mask |= OracleTokenBit(token.term);
  }
}
std::string Filename(uint32_t id, char kind) {
  const std::string stem = kind == 'j' ? "generated-" :
      (kind == 'l' ? "worker-" : (kind == 's' ? "service-" : "guide-"));
  const std::string ext = kind == 'j' ? ".json" :
      (kind == 'l' ? ".log" : (kind == 's' ? ".rs" : ".md"));
  return stem + std::to_string(id) + ext;
}
std::string MakeText(uint32_t file_id, uint32_t generation, uint32_t fragment,
                     char kind, const std::string& freshness = "") {
  static const std::array<const char*, 24> json_words = {
      "json", "parse", "payload", "request", "timeout", "status", "event", "cache",
      "error", "retry", "service", "worker", "directory", "token", "query", "result",
      "generated", "trace", "queue", "flush", "symbol", "index", "response", "database"};
  static const std::array<const char*, 24> log_words = {
      "log", "warning", "failed", "timeout", "request", "retry", "worker", "error",
      "connection", "refused", "cache", "miss", "segment", "flush", "queue", "token",
      "database", "status", "trace", "service", "directory", "parser", "response", "query"};
  static const std::array<const char*, 24> source_words = {
      "function", "resolver", "symbol", "query", "segment", "flush", "parser", "token",
      "index", "directory", "worker", "cache", "request", "database", "retry", "error",
      "timeout", "result", "service", "handler", "response", "json", "log", "status"};
  const auto& words = kind == 'j' ? json_words :
      (kind == 'l' ? log_words : source_words);
  std::string text;
  if (kind == 'j') text = "{\"event\":\"generated request\",\"payload\":\"";
  else if (kind == 'l') text = "2026-10-17T23:00:00Z INFO worker request: ";
  else if (kind == 's') text = "fn resolve_request_symbol() { let result = ";
  else text = "# Operations guide\n\nGenerated reference for ";
  if (!freshness.empty()) text += freshness + " ";
  for (uint32_t i = 0; i < 384; ++i) {  // ~2.8k characters, below zg's 3.6k chunk cap
    const uint64_t h = Hash(file_id + i, generation + fragment, i + fragment * 19);
    if ((h & 7) == 0) {
      text += "timeout error ";
    } else {
      text += words[(h >> 8) % words.size()];
      text.push_back(' ');
    }
  }
  if (kind == 'j') text += "\",\"status\":200}";
  else if (kind == 'l') text += " completed status=200";
  else if (kind == 's') text += "Ok(()) }";
  else text += "\n\nSee the request, timeout, cache, and retry sections.";
  return text;
}
File MakeFile(uint32_t id, uint32_t generation, char kind,
              const m4::Analyzer& analyzer, size_t forced_fragments = 0,
              const std::string& freshness = "") {
  File file{id, generation, kind, {}};
  const size_t count = forced_fragments ? forced_fragments : BaseFragments(id, generation);
  file.rows.reserve(count);
  const std::string filename = Filename(id, kind);
  for (size_t i = 0; i < count; ++i) {
    Row row;
    row.file_id = id;
    row.pk = "fragment-" + std::to_string(id) + "-" + std::to_string(i);
    row.entity_id = "entity-" + std::to_string(id) + "-" + std::to_string(i / 2);
    row.file_name = filename;
    row.text = MakeText(id, generation, static_cast<uint32_t>(i), kind,
                        i == 0 ? freshness : "");
    AnalyzeRow(row, analyzer);
    row.ancestors = {static_cast<uint32_t>(10 + id % 32),
                     static_cast<uint32_t>(1000 + id % 127)};
    if (kind == 's' && i % 2 == 0) {
      row.symbol_name = SymbolIndexValue("resolve_handler_" + std::to_string(i / 2));
      row.symbol_type = "function";
    }
    file.rows.push_back(std::move(row));
  }
  return file;
}

std::vector<Query> Queries() {
  using F = m4::FilterExpr;
  return {
      {"common_timeout", "timeout error", "", {}},
      {"json_request_directory", "json request", "ancestor_directory_ids CONTAIN_ANY (17)",
       F::ArrayContainsAny("ancestor_directory_ids", {17})},
      // Retained known native differential: report it per arm and stage rather
      // than excluding it or allowing it to masquerade as broad parity.
      {"known_json_parse_directory", "json parse",
       "ancestor_directory_ids CONTAIN_ANY (17)",
       F::ArrayContainsAny("ancestor_directory_ids", {17})},
      {"log_connection_suffix", "connection refused", "file_name LIKE '%.log'",
       F::StringSuffix("file_name", ".log")},
      {"generated_cache_prefix", "cache miss", "file_name LIKE 'generated-%'",
       F::StringPrefix("file_name", "generated-")},
      {"json_retry_directory", "retry request",
       "ancestor_directory_ids CONTAIN_ANY (21) AND file_name LIKE '%.json'",
       F::And({F::ArrayContainsAny("ancestor_directory_ids", {21}),
               F::StringSuffix("file_name", ".json")})},
      {"symbol_function", "symbol resolver", "symbol_type = 'function'",
       F::StringEq("symbol_type", "function")},
      {"directory_or_log", "worker status",
       "ancestor_directory_ids CONTAIN_ANY (24) OR file_name LIKE 'worker-%'",
       F::Or({F::ArrayContainsAny("ancestor_directory_ids", {24}),
              F::StringPrefix("file_name", "worker-")})},
      {"database_timeout", "database timeout", "", {}}
  };
}

zvec_collection_t* OpenNative(const std::string& path) {
  auto* schema = zvec_collection_schema_create("m4_p4_zg_fragments");
  struct FieldSpec { const char* name; zvec_data_type_t type; bool nullable; bool index; bool range; bool wildcard; };
  const FieldSpec fields[] = {
      {"file_id", ZVEC_DATA_TYPE_UINT32, false, true, true, false},
      {"entity_id", ZVEC_DATA_TYPE_STRING, false, true, true, false},
      {"ancestor_directory_ids", ZVEC_DATA_TYPE_ARRAY_UINT32, false, true, true, false},
      {"file_name", ZVEC_DATA_TYPE_STRING, false, true, false, true},
      {"symbol_name", ZVEC_DATA_TYPE_STRING, true, true, true, false},
      {"symbol_type", ZVEC_DATA_TYPE_STRING, true, true, true, false},
  };
  for (const auto& spec : fields) {
    auto* field = zvec_field_schema_create(spec.name, spec.type, spec.nullable, 0);
    if (spec.index) {
      auto* params = zvec_index_params_create(ZVEC_INDEX_TYPE_INVERT);
      NativeRequire(zvec_index_params_set_invert_params(params, spec.range, spec.wildcard),
                    std::string("set invert params ") + spec.name);
      NativeRequire(zvec_field_schema_set_index_params(field, params),
                    std::string("attach invert index ") + spec.name);
      zvec_index_params_destroy(params);
    }
    NativeRequire(zvec_collection_schema_add_field(schema, field),
                  std::string("add field ") + spec.name);
    zvec_field_schema_destroy(field);
  }
  auto* params = zvec_index_params_create(ZVEC_INDEX_TYPE_FTS);
  auto* filters = zvec_string_array_create(1);
  zvec_string_array_add(filters, 0, "lowercase");
  NativeRequire(zvec_index_params_set_fts_params(params, "jieba", filters, nullptr),
                "configure Jieba+lowercase FTS");
  auto* text = zvec_field_schema_create("text", ZVEC_DATA_TYPE_STRING, false, 0);
  NativeRequire(zvec_field_schema_set_index_params(text, params), "attach FTS index");
  NativeRequire(zvec_collection_schema_add_field(schema, text), "add text field");
  zvec_field_schema_destroy(text);
  zvec_index_params_destroy(params);
  zvec_string_array_destroy(filters);
  zvec_collection_t* collection = nullptr;
  NativeRequire(zvec_collection_create_and_open(path.c_str(), schema, nullptr, &collection),
                "create/open native collection");
  zvec_collection_schema_destroy(schema);
  return collection;
}

zvec_doc_t* NativeDoc(const Row& row) {
  auto* doc = zvec_doc_create();
  zvec_doc_set_pk(doc, row.pk.c_str());
  auto add_string = [&](const char* key, const std::string& value) {
    NativeRequire(zvec_doc_add_field_by_value(doc, key, ZVEC_DATA_TYPE_STRING,
                                               value.data(), value.size()),
                  std::string("set ") + key);
  };
  add_string("entity_id", row.entity_id);
  add_string("file_name", row.file_name);
  add_string("text", row.text);
  if (!row.symbol_name.empty()) add_string("symbol_name", row.symbol_name);
  if (!row.symbol_type.empty()) add_string("symbol_type", row.symbol_type);
  NativeRequire(zvec_doc_add_field_by_value(doc, "file_id", ZVEC_DATA_TYPE_UINT32,
                                             &row.file_id, sizeof(row.file_id)), "set file_id");
  NativeRequire(zvec_doc_add_field_by_value(doc, "ancestor_directory_ids",
                                             ZVEC_DATA_TYPE_ARRAY_UINT32,
                                             row.ancestors.data(),
                                             row.ancestors.size() * sizeof(uint32_t)),
                "set ancestor_directory_ids");
  return doc;
}
void NativeWrite(zvec_collection_t* collection, const std::vector<Row>& rows, bool upsert) {
  constexpr size_t kBatch = 512;  // zg-engine's production fragment write chunk
  for (size_t start = 0; start < rows.size(); start += kBatch) {
    const size_t end = std::min(rows.size(), start + kBatch);
    std::vector<zvec_doc_t*> docs;
    std::vector<const zvec_doc_t*> refs;
    docs.reserve(end - start); refs.reserve(end - start);
    for (size_t i = start; i < end; ++i) {
      docs.push_back(NativeDoc(rows[i]));
      refs.push_back(docs.back());
    }
    zvec_write_result_t* results = nullptr;
    size_t count = 0;
    TraceMarker("API_BEGIN|native_" + std::string(upsert ? "upsert" : "insert") +
                "_batch|docs=" + std::to_string(refs.size()));
    const auto status = upsert
        ? zvec_collection_upsert_with_results(collection, refs.data(), refs.size(), &results, &count)
        : zvec_collection_insert_with_results(collection, refs.data(), refs.size(), &results, &count);
    NativeRequire(status, upsert ? "native batch upsert" : "native batch insert");
    Require(count == refs.size() && results != nullptr, "native batch write result count");
    for (size_t i = 0; i < count; ++i)
      Require(results[i].code == ZVEC_OK, "native row write failed");
    zvec_write_results_free(results, count);
    TraceMarker("API_END|native_" + std::string(upsert ? "upsert" : "insert") +
                "_batch|docs=" + std::to_string(refs.size()));
    for (auto* doc : docs) zvec_doc_destroy(doc);
  }
}
void NativeDeleteFile(zvec_collection_t* collection, uint32_t id) {
  const std::string filter = "file_id = " + std::to_string(id);
  TraceMarker("API_BEGIN|native_delete_filter|file=" + std::to_string(id));
  NativeRequire(zvec_collection_delete_by_filter(collection, filter.c_str()),
                "delete native file_id");
  TraceMarker("API_END|native_delete_filter|file=" + std::to_string(id));
}
void NativeDeleteFiles(zvec_collection_t* collection, const std::vector<uint32_t>& ids) {
  if (ids.empty()) return;
  std::string filter;
  for (uint32_t id : ids) {
    if (!filter.empty()) filter += " OR ";
    filter += "file_id = " + std::to_string(id);
  }
  TraceMarker("API_BEGIN|native_delete_filter|files=" + std::to_string(ids.size()));
  NativeRequire(zvec_collection_delete_by_filter(collection, filter.c_str()),
                "delete native file batch");
  TraceMarker("API_END|native_delete_filter|files=" + std::to_string(ids.size()));
}
m4::Document M4Document(const Row& row) {
  m4::Document doc;
  doc.text = row.text;
  doc.values.emplace_back(m4::ScalarValue::Unsigned32("file_id", row.file_id));
  doc.values.push_back({"entity_id", row.entity_id});
  doc.values.push_back({"file_name", row.file_name});
  if (!row.symbol_name.empty()) doc.values.push_back({"symbol_name", row.symbol_name});
  if (!row.symbol_type.empty()) doc.values.push_back({"symbol_type", row.symbol_type});
  doc.array_uint32_values.push_back({"ancestor_directory_ids", row.ancestors});
  return doc;
}
void ApplyM4Mutations(m4::Index& index, const std::vector<m4::Mutation>& mutations,
                      bool batch_mode, const std::string& operation) {
  const bool trace_latency = std::getenv("M4_P42_LATENCY") != nullptr;
  if (batch_mode) {
    size_t batch_limit = 256;
    if (const char* value = std::getenv("M4_MUTATION_BATCH_DOCS")) {
      char* end = nullptr;
      const unsigned long parsed = std::strtoul(value, &end, 10);
      Require(end != value && *end == '\0' && parsed > 0 &&
                  parsed <= std::numeric_limits<uint32_t>::max(),
              "M4_MUTATION_BATCH_DOCS must be a positive uint32");
      batch_limit = static_cast<size_t>(parsed);
    }
    for (size_t start = 0; start < mutations.size(); start += batch_limit) {
      const size_t count = std::min(batch_limit, mutations.size() - start);
      std::vector<m4::Mutation> chunk(mutations.begin() + start,
                                      mutations.begin() + start + count);
      const auto begin = Clock::now();
      const auto status = index.ApplyBatch(chunk);
      const auto end = Clock::now();
      Require(status.ok, "M4 batch " + operation + ": " + status.message);
      if (trace_latency) EmitLatency("mutation_calls", "batch_" + operation + "_ms",
          std::chrono::duration<double, std::milli>(end - begin).count());
      TraceMarker("BATCH|m4_" + operation + "|operations=" + std::to_string(count));
    }
    return;
  }
  for (const auto& mutation : mutations) {
    const auto begin = Clock::now();
    const auto status = mutation.kind == m4::MutationKind::UPSERT
        ? index.Upsert(mutation.pk, mutation.document) : index.Delete(mutation.pk);
    const auto end = Clock::now();
    Require(status.ok, "M4 " + operation + " " + mutation.pk + ": " + status.message);
    if (trace_latency) EmitLatency("mutation_calls", "single_" + operation + "_ms",
        std::chrono::duration<double, std::milli>(end - begin).count());
  }
}
void M4Write(m4::Index& index, const std::vector<Row>& rows, bool batch_mode) {
  if (!batch_mode) {
    const bool trace_latency = std::getenv("M4_P42_LATENCY") != nullptr;
    for (const auto& row : rows) {
      const auto begin = Clock::now();
      const auto status = index.Upsert(row.pk, M4Document(row));
      const auto end = Clock::now();
      Require(status.ok, "M4 upsert " + row.pk + ": " + status.message);
      if (trace_latency) EmitLatency("mutation_calls", "single_upsert_ms",
          std::chrono::duration<double, std::milli>(end - begin).count());
    }
    return;
  }
  std::vector<m4::Mutation> mutations;
  mutations.reserve(rows.size());
  for (const auto& row : rows)
    mutations.emplace_back(m4::MutationKind::UPSERT, row.pk, M4Document(row));
  ApplyM4Mutations(index, mutations, true, "upsert");
}
struct CacheResidency {
  uint64_t file_bytes{0};
  uint64_t file_count{0};
  uint64_t total_pages{0};
  uint64_t resident_before{0};
  uint64_t resident_after{0};
};

std::pair<uint64_t, uint64_t> AdviseFileColdAndCountPages(const fs::path& path) {
  const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC);
  Require(fd >= 0, "open replay file for cache advice: " + path.string());
  struct stat st {};
  Require(::fstat(fd, &st) == 0, "stat replay file for cache advice: " + path.string());
  if (st.st_size <= 0) {
    ::close(fd);
    return {0, 0};
  }
  const long page_size = ::sysconf(_SC_PAGESIZE);
  Require(page_size > 0, "get system page size for replay cache check");
  const size_t length = static_cast<size_t>(st.st_size);
  const size_t page_count = (length + static_cast<size_t>(page_size) - 1) /
      static_cast<size_t>(page_size);
  void* mapping = ::mmap(nullptr, length, PROT_READ, MAP_SHARED, fd, 0);
  Require(mapping != MAP_FAILED, "map replay file for mincore: " + path.string());
  std::vector<unsigned char> residency(page_count);
  auto count_resident = [&]() {
    Require(::mincore(mapping, length, residency.data()) == 0,
            "mincore replay file: " + path.string());
    return static_cast<uint64_t>(std::count_if(
        residency.begin(), residency.end(),
        [](unsigned char page) { return page != 0; }));
  };
  const uint64_t before = count_resident();
  const int advice = ::posix_fadvise(fd, 0, st.st_size, POSIX_FADV_DONTNEED);
  Require(advice == 0, "POSIX_FADV_DONTNEED replay file failed: " + path.string() +
                            " code=" + std::to_string(advice));
  const uint64_t after = count_resident();
  ::munmap(mapping, length);
  ::close(fd);
  return {before, after};
}

CacheResidency AdviseIndexFilesCold(const fs::path& index_dir) {
  CacheResidency result;
  for (const auto& entry : fs::recursive_directory_iterator(index_dir)) {
    if (!entry.is_regular_file()) continue;
    std::error_code ec;
    const uint64_t bytes = entry.file_size(ec);
    Require(!ec, "stat index file before cold replay: " + entry.path().string());
    const auto [before, after] = AdviseFileColdAndCountPages(entry.path());
    result.file_bytes += bytes;
    ++result.file_count;
    const long page_size = ::sysconf(_SC_PAGESIZE);
    result.total_pages += (bytes + static_cast<uint64_t>(page_size) - 1) /
        static_cast<uint64_t>(page_size);
    result.resident_before += before;
    result.resident_after += after;
  }
  Require(result.file_count > 0 && result.file_bytes > 0,
          "WAL replay probe has no regular index files for cold-cache replay");
  return result;
}

void RunWalReplayProbe(const fs::path &main_path, const std::vector<Row> &rows,
                       const m4::Options &base_options) {
  const char *enabled = std::getenv("M4_WAL_REPLAY_PROBE");
  if (enabled == nullptr || std::string(enabled) == "0" ||
      std::string(enabled).empty())
    return;
  Require(std::string(enabled) == "1", "M4_WAL_REPLAY_PROBE must be 0 or 1");
  bool mixed_frames = false;
  if (const char *value = std::getenv("M4_WAL_REPLAY_MIXED_FRAMES")) {
    Require(std::string(value) == "0" || std::string(value) == "1",
            "M4_WAL_REPLAY_MIXED_FRAMES must be 0 or 1");
    mixed_frames = std::string(value) == "1";
  }
  const fs::path probe_dir =
      main_path.parent_path() /
      (main_path.filename().string() + "-wal-replay-probe");
  Require(!fs::exists(probe_dir),
          "WAL replay probe path must be fresh: " + probe_dir.string());
  fs::create_directory(probe_dir);

  m4::Options write_options = base_options;
  write_options.wal_enabled = true;
  write_options.fsync_wal = true;
  write_options.coalesce_stats_events = false;
  if (mixed_frames)
    write_options.wal_compression_lz4 = false;
  write_options.stats_log_threshold = 1ull << 40;
  write_options.max_delta_docs = static_cast<uint32_t>(
      std::min<size_t>(rows.size() + 1, std::numeric_limits<uint32_t>::max()));
  write_options.max_delta_bytes = 1ull << 30;
  std::unique_ptr<m4::Index> writer;
  auto status = m4::Index::Open(probe_dir.string(), write_options, &writer);
  Require(status.ok, "open WAL replay probe writer: " + status.message);
  size_t batch_limit = 256;
  if (const char *value = std::getenv("M4_MUTATION_BATCH_DOCS")) {
    char *end = nullptr;
    const unsigned long parsed = std::strtoul(value, &end, 10);
    Require(end != value && *end == '\0' && parsed > 0 &&
                parsed <= std::numeric_limits<uint32_t>::max(),
            "M4_MUTATION_BATCH_DOCS must be a positive uint32");
    batch_limit = static_cast<size_t>(parsed);
  }
  if (const char *value = std::getenv("M4_WAL_REPLAY_BATCH_DOCS")) {
    char *end = nullptr;
    const unsigned long parsed = std::strtoul(value, &end, 10);
    Require(end != value && *end == '\0' && parsed > 0 && parsed <= 256,
            "M4_WAL_REPLAY_BATCH_DOCS must be in 1..256");
    batch_limit = static_cast<size_t>(parsed);
  }
  std::vector<m4::Mutation> mutations;
  mutations.reserve(rows.size());
  for (const auto &row : rows)
    mutations.emplace_back(m4::MutationKind::UPSERT, row.pk, M4Document(row));
  EmitMetric("wal_replay_probe_write", "logical_input_text_bytes",
             static_cast<double>(TextBytes(rows)));
  const double write_cpu_before_ms = CpuMs();
  Measure("wal_replay_probe_write", [&] {
    bool switched_to_compressed_writer = !mixed_frames;
    const size_t mixed_switch_at = (mutations.size() + 1) / 2;
    for (size_t start = 0; start < mutations.size(); start += batch_limit) {
      if (!switched_to_compressed_writer && start >= mixed_switch_at) {
        writer.reset();
        write_options.wal_compression_lz4 = true;
        status = m4::Index::Open(probe_dir.string(), write_options, &writer);
        Require(status.ok,
                "reopen mixed WAL probe with LZ4 writer: " + status.message);
        switched_to_compressed_writer = true;
      }
      const size_t count = std::min(batch_limit, mutations.size() - start);
      const auto begin = Clock::now();
      status = writer->ApplyBatch(std::vector<m4::Mutation>(
          mutations.begin() + start, mutations.begin() + start + count));
      const auto end = Clock::now();
      Require(status.ok,
              "WAL replay probe durable ApplyBatch: " + status.message);
      EmitLatency(
          "wal_replay_probe", "durable_apply_batch_ack_ms",
          std::chrono::duration<double, std::milli>(end - begin).count());
    }
  });
  EmitMetric("wal_replay_probe_write", "process_cpu_ms",
             CpuMs() - write_cpu_before_ms);
  const fs::path wal_path = probe_dir / "wal.log";
  writer.reset(); // Deliberately no Flush: the child state exists only in the
                  // WAL.
  uint64_t simulated_torn_tail_bytes = 0;
  if (const char *value = std::getenv("M4_WAL_REPLAY_TORN_TAIL")) {
    Require(std::string(value) == "0" || std::string(value) == "1",
            "M4_WAL_REPLAY_TORN_TAIL must be 0 or 1");
    if (std::string(value) == "1") {
      const char torn_tail[] = {'M', '4', 'W', 'Z', 1, 0, 0};
      std::ofstream wal(wal_path, std::ios::binary | std::ios::app);
      Require(wal.is_open(), "open WAL to append simulated torn batch tail");
      wal.write(torn_tail, sizeof(torn_tail));
      wal.flush();
      Require(static_cast<bool>(wal), "append simulated torn batch tail");
      simulated_torn_tail_bytes = sizeof(torn_tail);
    }
  }
  std::error_code ec;
  const uint64_t wal_bytes = fs::file_size(wal_path, ec);
  Require(!ec && wal_bytes > 0, "WAL replay probe produced a non-empty WAL");
  EmitMetric("wal_replay_probe", "wal_bytes_before_replay",
             static_cast<double>(wal_bytes));
  EmitMetric("wal_replay_probe", "wal_document_count",
             static_cast<double>(mutations.size()));
  EmitMetric("wal_replay_probe", "simulated_torn_tail_bytes",
             static_cast<double>(simulated_torn_tail_bytes));
  EmitMetric("wal_replay_probe", "mixed_legacy_lz4_frames",
             mixed_frames ? 1.0 : 0.0);

  // Drop only this fresh probe index's file pages; do not evict system-wide
  // caches.
  const CacheResidency cache = AdviseIndexFilesCold(probe_dir);
  EmitMetric("wal_replay_probe_cold_cache", "index_file_bytes",
             static_cast<double>(cache.file_bytes));
  EmitMetric("wal_replay_probe_cold_cache", "index_file_count",
             static_cast<double>(cache.file_count));
  EmitMetric("wal_replay_probe_cold_cache", "total_pages",
             static_cast<double>(cache.total_pages));
  EmitMetric("wal_replay_probe_cold_cache", "resident_pages_before_advice",
             static_cast<double>(cache.resident_before));
  EmitMetric("wal_replay_probe_cold_cache", "resident_pages_after_advice",
             static_cast<double>(cache.resident_after));
  EmitMetric("wal_replay_probe_cold_cache", "resident_fraction_after_advice",
             cache.total_pages == 0
                 ? 0.0
                 : static_cast<double>(cache.resident_after) /
                       cache.total_pages);

  m4::Options replay_options = write_options;
  replay_options.wal_compression_lz4 = false;
  std::unique_ptr<m4::Index> recovered;
  m4::OpenDiagnostics replay_diagnostics;
  const ProcMemorySnapshot memory_before = ReadProcMemorySnapshot();
  const Counters replay_io_before = Io();
  const double replay_cpu_before_ms = CpuMs();
  Measure("wal_replay_probe_open", [&] {
    status = m4::Index::Open(probe_dir.string(), replay_options, &recovered,
                             &replay_diagnostics);
    Require(status.ok,
            "fresh-index cold WAL replay probe open: " + status.message);
  });
  const double replay_cpu_ms = CpuMs() - replay_cpu_before_ms;
  const Counters replay_io_after = Io();
  const ProcMemorySnapshot memory_after = ReadProcMemorySnapshot();
  EmitMetric("wal_replay_probe_open", "process_cpu_ms", replay_cpu_ms);
  EmitMetric("wal_replay_probe_open", "wal_replay_stage_wall_ms",
             replay_diagnostics.wal_replay_ms);
  EmitMetric("wal_replay_probe_open", "wal_replay_stage_cpu_ms",
             replay_diagnostics.wal_replay_cpu_ms);
  EmitMetric("wal_replay_probe_open", "process_read_bytes",
             static_cast<double>(replay_io_after.read_bytes -
                                 replay_io_before.read_bytes));
  EmitMetric("wal_replay_probe_open", "rss_before_bytes",
             static_cast<double>(memory_before.rss_bytes));
  EmitMetric("wal_replay_probe_open", "rss_after_bytes",
             static_cast<double>(memory_after.rss_bytes));
  EmitMetric("wal_replay_probe_open", "pss_before_bytes",
             static_cast<double>(memory_before.pss_bytes));
  EmitMetric("wal_replay_probe_open", "pss_after_bytes",
             static_cast<double>(memory_after.pss_bytes));
  EmitMetric("wal_replay_probe_open", "private_dirty_before_bytes",
             static_cast<double>(memory_before.private_dirty_bytes));
  EmitMetric("wal_replay_probe_open", "private_dirty_after_bytes",
             static_cast<double>(memory_after.private_dirty_bytes));
  EmitMetric("wal_replay_probe_open", "vmhwm_before_bytes",
             static_cast<double>(memory_before.vmhwm_bytes));
  EmitMetric("wal_replay_probe_open", "vmhwm_after_bytes",
             static_cast<double>(memory_after.vmhwm_bytes));
  EmitMetric(
      "wal_replay_probe_open", "wal_encoded_file_bytes",
      static_cast<double>(replay_diagnostics.wal_replay_encoded_file_bytes));
  EmitMetric("wal_replay_probe_open", "wal_encoded_buffer_capacity_bytes",
             static_cast<double>(
                 replay_diagnostics.wal_replay_encoded_buffer_capacity_bytes));
  EmitMetric("wal_replay_probe_open", "wal_decoded_record_count",
             static_cast<double>(replay_diagnostics.wal_replay_record_count));
  EmitMetric(
      "wal_replay_probe_open", "wal_decoded_payload_bytes",
      static_cast<double>(replay_diagnostics.wal_replay_decoded_payload_bytes));
  EmitMetric("wal_replay_probe_open", "wal_decoded_payload_capacity_bytes",
             static_cast<double>(
                 replay_diagnostics.wal_replay_decoded_payload_capacity_bytes));
  EmitMetric("wal_replay_probe_open", "wal_record_vector_capacity_bytes",
             static_cast<double>(
                 replay_diagnostics.wal_replay_record_vector_capacity_bytes));
  EmitMetric("wal_replay_probe_open", "wal_replay_peak_tracked_live_bytes",
             static_cast<double>(
                 replay_diagnostics.wal_replay_peak_tracked_live_bytes));
  EmitMetric(
      "wal_replay_probe_open", "wal_replay_record_vector_growths",
      static_cast<double>(replay_diagnostics.wal_replay_record_vector_growths));
  const uint64_t encoded_payload_bytes =
      replay_diagnostics.wal_replay_encoded_file_bytes +
      replay_diagnostics.wal_replay_decoded_payload_bytes;
  EmitMetric("wal_replay_probe_open",
             "tracked_capacity_overhead_upper_bound_bytes",
             static_cast<double>(
                 replay_diagnostics.wal_replay_peak_tracked_live_bytes >
                         encoded_payload_bytes
                     ? replay_diagnostics.wal_replay_peak_tracked_live_bytes -
                           encoded_payload_bytes
                     : 0));
  Require(recovered && recovered->GetStats().live_docs == mutations.size(),
          "WAL replay probe recovered every durable document");
  m4::SearchResult result;
  status = recovered->SearchFts("request", 10, &result);
  Require(status.ok && !result.hits.empty(),
          "WAL replay probe recovered searchable postings");
  EmitMetric("wal_replay_probe_open", "replayed_live_docs",
             static_cast<double>(recovered->GetStats().live_docs));
}
void M4DeleteFile(m4::Index& index, uint32_t id, bool batch_mode) {
  m4::SearchResult matches;
  const auto status = index.SearchFilter(m4::FilterExpr::Uint32Eq("file_id", id), &matches);
  Require(status.ok, "M4 enumerate file_id: " + status.message);
  if (!batch_mode) {
    const bool trace_latency = std::getenv("M4_P42_LATENCY") != nullptr;
    for (const auto& hit : matches.hits) {
      const auto begin = Clock::now();
      const auto deleted = index.Delete(hit.pk);
      const auto end = Clock::now();
      Require(deleted.ok, "M4 delete " + hit.pk + ": " + deleted.message);
      if (trace_latency) EmitLatency("mutation_calls", "single_delete_ms",
          std::chrono::duration<double, std::milli>(end - begin).count());
    }
    return;
  }
  std::vector<m4::Mutation> mutations;
  mutations.reserve(matches.hits.size());
  for (const auto& hit : matches.hits)
    mutations.emplace_back(m4::MutationKind::DELETE, hit.pk);
  ApplyM4Mutations(index, mutations, true, "delete");
}
void M4DeleteFiles(m4::Index& index, const std::vector<uint32_t>& ids,
                   bool batch_mode) {
  if (ids.empty()) return;
  std::vector<m4::FilterExpr> clauses;
  clauses.reserve(ids.size());
  for (uint32_t id : ids) clauses.push_back(m4::FilterExpr::Uint32Eq("file_id", id));
  m4::SearchResult matches;
  const auto status = index.SearchFilter(m4::FilterExpr::Or(std::move(clauses)), &matches);
  Require(status.ok, "M4 enumerate file batch: " + status.message);
  if (!batch_mode) {
    const bool trace_latency = std::getenv("M4_P42_LATENCY") != nullptr;
    for (const auto& hit : matches.hits) {
      const auto begin = Clock::now();
      const auto deleted = index.Delete(hit.pk);
      const auto end = Clock::now();
      Require(deleted.ok, "M4 delete " + hit.pk + ": " + deleted.message);
      if (trace_latency) EmitLatency("mutation_calls", "single_delete_ms",
          std::chrono::duration<double, std::milli>(end - begin).count());
    }
    return;
  }
  std::vector<m4::Mutation> mutations;
  mutations.reserve(matches.hits.size());
  for (const auto& hit : matches.hits)
    mutations.emplace_back(m4::MutationKind::DELETE, hit.pk);
  ApplyM4Mutations(index, mutations, true, "delete");
}

std::vector<Hit> NativeSearch(zvec_collection_t* collection, const Query& query, uint32_t topk) {
  auto* q = zvec_vector_query_create();
  NativeRequire(zvec_vector_query_set_field_name(q, "text"), "set FTS field");
  NativeRequire(zvec_vector_query_set_topk(q, static_cast<int>(topk)), "set FTS topk");
  if (!query.sql.empty()) NativeRequire(zvec_vector_query_set_filter(q, query.sql.c_str()), "set filter");
  auto* fts = zvec_fts_create();
  NativeRequire(zvec_fts_set_match_string(fts, query.text.c_str()), "set match string");
  NativeRequire(zvec_vector_query_set_fts(q, fts), "attach FTS query");
  auto* params = zvec_query_params_fts_create("OR");
  const auto ps = zvec_vector_query_set_fts_params(q, params);
  if (ps == ZVEC_OK) params = nullptr;
  else NativeRequire(ps, "attach OR query params");
  zvec_doc_t** docs = nullptr;
  size_t count = 0;
  const auto result = zvec_collection_query(collection, q, &docs, &count);
  if (result != ZVEC_OK) NativeRequire(result, "native search " + query.name);
  std::vector<Hit> hits;
  hits.reserve(count);
  std::set<std::string> unique;
  for (size_t i = 0; i < count; ++i) {
    const char* pk = zvec_doc_get_pk_pointer(docs[i]);
    Require(pk != nullptr && unique.insert(pk).second, "native duplicate/null primary key");
    hits.push_back({pk, zvec_doc_get_score(docs[i])});
  }
  std::stable_sort(hits.begin(), hits.end(), [](const Hit& a, const Hit& b) {
    return a.score != b.score ? a.score > b.score : a.pk < b.pk;
  });
  zvec_docs_free(docs, count);
  zvec_vector_query_destroy(q);
  if (params) zvec_query_params_fts_destroy(params);
  zvec_fts_destroy(fts);
  return hits;
}
std::vector<Hit> M4Search(const m4::Index& index, const Query& query, uint32_t topk,
                          m4::QueryDiagnostics* diagnostics = nullptr) {
  m4::SearchResult result;
  const auto status = query.sql.empty()
      ? index.SearchFts(query.text, topk, &result, diagnostics)
      : index.SearchFtsFiltered(query.text, topk, query.filter, &result, diagnostics);
  Require(status.ok, "M4 search " + query.name + ": " + status.message);
  std::vector<Hit> hits;
  hits.reserve(result.hits.size());
  std::set<std::string> unique;
  for (const auto& hit : result.hits) {
    Require(unique.insert(hit.pk).second, "M4 duplicate primary key");
    hits.push_back({hit.pk, hit.score});
  }
  std::stable_sort(hits.begin(), hits.end(), [](const Hit& a, const Hit& b) {
    return a.score != b.score ? a.score > b.score : a.pk < b.pk;
  });
  return hits;
}

bool OracleFilterMatches(const Query& query, const Row& row) {
  const bool in_dir_17 = std::find(row.ancestors.begin(), row.ancestors.end(), 17) != row.ancestors.end();
  const bool in_dir_21 = std::find(row.ancestors.begin(), row.ancestors.end(), 21) != row.ancestors.end();
  const bool in_dir_24 = std::find(row.ancestors.begin(), row.ancestors.end(), 24) != row.ancestors.end();
  if (query.name == "json_request_directory" ||
      query.name == "known_json_parse_directory") return in_dir_17;
  if (query.name == "log_connection_suffix") return row.file_name.size() >= 4 && row.file_name.compare(row.file_name.size() - 4, 4, ".log") == 0;
  if (query.name == "generated_cache_prefix") return row.file_name.rfind("generated-", 0) == 0;
  if (query.name == "json_retry_directory") return in_dir_21 && row.file_name.size() >= 5 && row.file_name.compare(row.file_name.size() - 5, 5, ".json") == 0;
  if (query.name == "symbol_function") return row.symbol_type == "function";
  if (query.name == "directory_or_log") return in_dir_24 || row.file_name.rfind("worker-", 0) == 0;
  return true;
}
uint32_t OracleQueryMask(const Query& query, const m4::Analyzer& analyzer) {
  uint32_t mask = 0;
  for (const auto& token : analyzer.Tokenize(query.text)) mask |= OracleTokenBit(token.term);
  return mask;
}
bool OracleTextMatches(const Query& query, const Row& row, uint32_t query_mask) {
  return (row.oracle_token_mask & query_mask) != 0;
}
void EmitExpected(const std::string& stage, const std::map<uint32_t, File>& files,
                  const m4::Analyzer& analyzer, const std::vector<Query>& queries) {
  for (const auto& query : queries) {
    const uint32_t query_mask = OracleQueryMask(query, analyzer);
    for (const auto& [file_id, file] : files) {
      (void)file_id;
      for (const auto& row : file.rows) {
        if (OracleFilterMatches(query, row) && OracleTextMatches(query, row, query_mask))
          std::cout << "E\t" << stage << '\t' << query.name << '\t' << row.pk << '\n';
      }
    }
  }
}

uint64_t NativeLiveDocs(zvec_collection_t* collection) {
  zvec_collection_stats_t* stats = nullptr;
  NativeRequire(zvec_collection_get_stats(collection, &stats), "get native stats");
  const uint64_t count = zvec_collection_stats_get_doc_count(stats);
  zvec_collection_stats_destroy(stats);
  return count;
}
size_t DirectoryBytes(const fs::path& path) {
  size_t total = 0;
  std::error_code ec;
  for (const auto& entry : fs::recursive_directory_iterator(path, ec))
    if (entry.is_regular_file(ec)) total += entry.file_size(ec);
  return total;
}
size_t NativeSegments(const fs::path& path) {
  size_t count = 0;
  std::error_code ec;
  for (const auto& entry : fs::directory_iterator(path, ec)) {
    if (!entry.is_directory(ec)) continue;
    const std::string name = entry.path().filename().string();
    if (!name.empty() && std::all_of(name.begin(), name.end(), [](unsigned char c) { return std::isdigit(c); })) ++count;
  }
  return count;
}
uint64_t ExpectedTokens(const std::map<uint32_t, File>& files) {
  uint64_t total = 0;
  for (const auto& [id, file] : files) {
    (void)id;
    for (const auto& row : file.rows) total += row.token_count;
  }
  return total;
}
void Snapshot(const std::string& stage, const fs::path& path,
              const std::map<uint32_t, File>& files, const m4::Analyzer& analyzer,
              zvec_collection_t* native, const std::unique_ptr<m4::Index>& m4index) {
  const uint64_t docs = native ? NativeLiveDocs(native) : m4index->GetStats().live_docs;
  const uint64_t tokens = ExpectedTokens(files);
  const size_t runs = native ? NativeSegments(path) : m4index->GetStats().runs;
  const size_t bytes = DirectoryBytes(path);
  const uint64_t expected_docs = std::accumulate(files.begin(), files.end(), uint64_t{0},
      [](uint64_t sum, const auto& pair) { return sum + pair.second.rows.size(); });
  Require(docs == expected_docs, "live-doc count differs from logical corpus at " + stage);
  if (m4index) {
    const auto stats = m4index->GetStats();
    if (std::getenv("M4_P42_LATENCY")) {
      EmitMetric("snapshot_" + stage, "wal_bytes_appended", stats.wal_bytes_appended);
      EmitMetric("snapshot_" + stage, "wal_fsyncs", stats.wal_fsyncs);
      EmitMetric("snapshot_" + stage, "m4_runs", stats.runs);
      EmitMetric("snapshot_" + stage, "m4_dead_docs", stats.dead_docs);
      EmitMetric("snapshot_" + stage, "m4_compactions", stats.compactions);
      EmitMetric("snapshot_" + stage, "m4_compaction_merges", stats.compaction_merges);
    }
    EmitMetric("snapshot_" + stage, "wal_lz4_attempts", stats.wal_lz4_attempts);
    EmitMetric("snapshot_" + stage, "wal_lz4_attempted_input_bytes",
               stats.wal_lz4_attempted_input_bytes);
    EmitMetric("snapshot_" + stage, "wal_lz4_compressed_frames",
               stats.wal_lz4_compressed_frames);
    EmitMetric("snapshot_" + stage, "wal_lz4_input_bytes", stats.wal_lz4_input_bytes);
    EmitMetric("snapshot_" + stage, "wal_lz4_output_bytes", stats.wal_lz4_output_bytes);
    EmitMetric("snapshot_" + stage, "wal_lz4_compress_cpu_ms",
               stats.wal_lz4_compress_cpu_ms);
    Require(stats.live_tokens == tokens,
            "M4 token count differs from Jieba oracle at " + stage);
    if (TraceEnabled()) {
      std::cout << "W\t" << stage << "\tm4"
                << "\twal_bytes=" << stats.wal_bytes
                << "\twal_bytes_appended=" << stats.wal_bytes_appended
                << "\twal_lz4_attempts=" << stats.wal_lz4_attempts
                << "\twal_lz4_attempted_input_bytes=" << stats.wal_lz4_attempted_input_bytes
                << "\twal_lz4_compressed_frames=" << stats.wal_lz4_compressed_frames
                << "\twal_lz4_input_bytes=" << stats.wal_lz4_input_bytes
                << "\twal_lz4_output_bytes=" << stats.wal_lz4_output_bytes
                << "\twal_lz4_compress_cpu_ms=" << stats.wal_lz4_compress_cpu_ms
                << "\tsegment_bytes=" << stats.segments_bytes
                << "\ttombstone_bytes=" << stats.tombstone_bytes
                << "\tstats_log_bytes=" << stats.stats_log_bytes
                << "\treported_bytes_written_ex_wal=" << stats.bytes_written
                << "\tsegment_bytes_written=" << stats.segment_bytes_written
                << "\twal_fsyncs=" << stats.wal_fsyncs
                << "\tstats_log_fsync_counter=" << stats.fsyncs
                << "\tcompactions=" << stats.compactions
                << "\tcompaction_merges=" << stats.compaction_merges
                << "\tdead_docs=" << stats.dead_docs;
      for (size_t level = 0; level < stats.runs_per_level.size(); ++level) {
        std::cout << "\truns_level_" << level << "=" << stats.runs_per_level[level];
      }
      std::cout << '\n';
    }
  }
  std::cout << "S\t" << stage << '\t' << docs << '\t' << tokens << '\t'
            << files.size() << '\t' << runs << '\t' << bytes << '\n';
}

void EmitOracle(const std::string& stage, zvec_collection_t* native,
                const m4::Index* m4index, const std::vector<Query>& queries,
                uint64_t live_docs) {
  for (const auto& query : queries) {
    const uint32_t topk = static_cast<uint32_t>(std::min<uint64_t>(100000, live_docs + 1));
    const auto hits = native ? NativeSearch(native, query, topk)
                             : M4Search(*m4index, query, topk);
    for (const auto& hit : hits)
      std::cout << "H\t" << stage << '\t' << query.name << '\t' << hit.pk << '\t'
                << hit.score << '\n';
  }
}
void RunTimedQueries(const std::string& stage, zvec_collection_t* native,
                     const m4::Index* m4index, const std::vector<Query>& queries) {
  // Warm every query once outside the timed samples so arm order does not make
  // one role pay the initial cold-page/cache cost inside its p95 distribution.
  for (const auto& query : queries) {
    const auto hits = native ? NativeSearch(native, query, 20)
                             : M4Search(*m4index, query, 20);
    Require(hits.size() <= 20, "query warmup top-k exceeded 20");
  }
  std::vector<double> latencies;
  Measure(stage + "_search", [&] {
    for (int repeat = 0; repeat < 5; ++repeat) {
      for (const auto& query : queries) {
        const auto start = Clock::now();
        const auto hits = native ? NativeSearch(native, query, 20)
                                 : M4Search(*m4index, query, 20);
        const auto end = Clock::now();
        latencies.push_back(std::chrono::duration<double, std::milli>(end - start).count());
        Require(hits.size() <= 20, "top-k exceeded 20");
      }
    }
  });
  for (double ms : latencies) EmitLatency(stage, "query_ms", ms);
  if (m4index) {
    // Diagnostic counters are gathered after timed samples so counter/timer hooks
    // cannot contaminate the reader-latency distribution above.
    m4::QueryDiagnostics totals;
    for (const auto& query : queries) {
      m4::QueryDiagnostics diag;
      const auto hits = M4Search(*m4index, query, 20, &diag);
      Require(hits.size() <= 20, "diagnostic top-k exceeded 20");
      totals.runs_read += diag.runs_read;
      totals.run_term_parts += diag.run_term_parts;
      totals.postings_scanned += diag.postings_scanned;
      totals.cursor_next_calls += diag.cursor_next_calls;
      totals.score_evaluations += diag.score_evaluations;
      totals.setup_ms += diag.setup_ms;
      totals.execution_ms += diag.execution_ms;
    }
    EmitMetric(stage, "m4_diag_query_count", queries.size());
    EmitMetric(stage, "m4_diag_runs_read_sum", totals.runs_read);
    EmitMetric(stage, "m4_diag_run_term_parts_sum", totals.run_term_parts);
    EmitMetric(stage, "m4_diag_postings_scanned_sum", totals.postings_scanned);
    EmitMetric(stage, "m4_diag_cursor_next_calls_sum", totals.cursor_next_calls);
    EmitMetric(stage, "m4_diag_score_evaluations_sum", totals.score_evaluations);
    EmitMetric(stage, "m4_diag_setup_ms_sum", totals.setup_ms);
    EmitMetric(stage, "m4_diag_execution_ms_sum", totals.execution_ms);
  }
}

void NativeFlush(zvec_collection_t* collection) {
  NativeRequire(zvec_collection_flush(collection), "flush native collection");
}

int Run(const std::string& arm, const fs::path& path, uint32_t base_files,
        const std::string& dictionary_dir) {
  Require(arm == "native" || arm == "m4" || arm == "m4-batch",
          "arm must be native, m4, or m4-batch");
  const bool batch_mode = arm == "m4-batch";
  fs::create_directories(path.parent_path());
  Require(!fs::exists(path) || fs::is_empty(path),
          "index directory must be fresh: " + path.string());
  m4::Analyzer analyzer;
  const auto analyzer_status = analyzer.Init("jieba", dictionary_dir);
  Require(analyzer_status.ok, "init shared Jieba analyzer: " + analyzer_status.message);

  zvec_collection_t* native = nullptr;
  std::unique_ptr<m4::Index> m4index;
  m4::Options m4_options;
  bool enable_debt_policy_after_base = false;
  if (arm == "native") {
    zvec_set_default_jieba_dict_dir(dictionary_dir.c_str());
    NativeRequire(zvec_initialize(nullptr), "initialize native Zvec");
    native = OpenNative(path.string());
  } else {
    m4_options.fts_field = "text";
    m4_options.fts_tokenizer_name = "jieba";
    m4_options.jieba_dict_dir = dictionary_dir;
    m4_options.max_delta_docs = 512;
    m4_options.max_total_runs = 12;
    if (const char* value = std::getenv("M4_MAX_TOTAL_RUNS")) {
      char* end = nullptr;
      const unsigned long parsed = std::strtoul(value, &end, 10);
      Require(end != value && *end == '\0' && parsed > 0 &&
                  parsed <= std::numeric_limits<uint32_t>::max(),
              "M4_MAX_TOTAL_RUNS must be a positive uint32");
      m4_options.max_total_runs = static_cast<uint32_t>(parsed);
    }
    if (const char* value = std::getenv("M4_MAX_RUNS_PER_LEVEL")) {
      char* end = nullptr;
      const unsigned long parsed = std::strtoul(value, &end, 10);
      Require(end != value && *end == '\0' && parsed > 0 &&
                  parsed <= std::numeric_limits<uint32_t>::max(),
              "M4_MAX_RUNS_PER_LEVEL must be a positive uint32");
      m4_options.max_runs_per_level = static_cast<uint32_t>(parsed);
    }
    if (const char* value = std::getenv("M4_MAX_DELTA_DOCS")) {
      char* end = nullptr;
      const unsigned long parsed = std::strtoul(value, &end, 10);
      Require(end != value && *end == '\0' && parsed > 0 &&
                  parsed <= std::numeric_limits<uint32_t>::max(),
              "M4_MAX_DELTA_DOCS must be a positive uint32");
      m4_options.max_delta_docs = static_cast<uint32_t>(parsed);
    }
    if (const char *value = std::getenv("M4_STATS_LOG_THRESHOLD")) {
      char *end = nullptr;
      const unsigned long long parsed = std::strtoull(value, &end, 10);
      Require(end != value && *end == '\0' && parsed > 0,
              "M4_STATS_LOG_THRESHOLD must be a positive uint64");
      m4_options.stats_log_threshold = static_cast<uint64_t>(parsed);
    }
    if (const char *value = std::getenv("M4_COALESCE_STATS")) {
      Require(std::string(value) == "0" || std::string(value) == "1",
              "M4_COALESCE_STATS must be 0 or 1");
      m4_options.coalesce_stats_events = std::string(value) == "1";
    }
    if (const char *value = std::getenv("M4_WAL_COMPRESSION_LZ4")) {
      Require(std::string(value) == "0" || std::string(value) == "1",
              "M4_WAL_COMPRESSION_LZ4 must be 0 or 1");
      m4_options.wal_compression_lz4 = std::string(value) == "1";
    }
    if (const char *value = std::getenv("M4_WAL_LZ4_ACCELERATION")) {
      char *end = nullptr;
      errno = 0;
      const unsigned long parsed = std::strtoul(value, &end, 10);
      Require(errno == 0 && end != value && *end == '\0' && parsed > 0 &&
                  parsed <= static_cast<unsigned long>(std::numeric_limits<int>::max()),
              "M4_WAL_LZ4_ACCELERATION must be a positive int");
      m4_options.wal_lz4_acceleration = static_cast<uint32_t>(parsed);
    }
    m4_options.fsync_wal = true;
    const char *debt_policy = std::getenv("M4_DEBT_POLICY");
    enable_debt_policy_after_base = debt_policy != nullptr &&
                                    std::string(debt_policy) != "0" &&
                                    std::string(debt_policy) != "";
    if (const char *ratio = std::getenv("M4_DEBT_RATIO"))
      m4_options.debt_compact_ratio = std::strtod(ratio, nullptr);
    if (const char *min_runs = std::getenv("M4_DEBT_MIN_RUNS"))
      m4_options.debt_min_runs = static_cast<uint32_t>(std::strtoul(min_runs, nullptr, 10));
    if (const char* max_docs = std::getenv("M4_DEBT_CAP"))
      m4_options.debt_max_merge_docs = std::strtoull(max_docs, nullptr, 10);
    // Match the accepted S5 protocol: the base index is built with policy OFF;
    // enable the opt-in policy only after the initial bulk flush/reopen.
    m4_options.debt_policy_enabled = false;
    const auto opened = m4::Index::Open(path.string(), m4_options, &m4index);
    Require(opened.ok, "open M4 index: " + opened.message);
  }

  std::map<uint32_t, File> files;
  std::vector<File> initial;
  initial.reserve(base_files);
  for (uint32_t id = 1; id <= base_files; ++id)
    initial.push_back(MakeFile(id, 0, KindFor(id), analyzer));
  std::vector<Row> initial_rows;
  for (const auto& file : initial) initial_rows.insert(initial_rows.end(), file.rows.begin(), file.rows.end());
  for (auto& file : initial) files.emplace(file.id, std::move(file));
  if (m4index) RunWalReplayProbe(path, initial_rows, m4_options);

  EmitMetric("initial_indexing", "logical_input_text_bytes",
             static_cast<double>(TextBytes(initial_rows)));
  Measure("initial_indexing", [&] {
    if (native) NativeWrite(native, initial_rows, true);
    else M4Write(*m4index, initial_rows, batch_mode);
  });
  Measure("initial_flush", [&] {
    if (native) {
      NativeFlush(native);
    } else {
      const auto status = m4index->Flush();
      Require(status.ok, "M4 initial flush: " + status.message);
    }
  });
  if (m4index && enable_debt_policy_after_base) {
    m4index.reset();
    m4_options.debt_policy_enabled = true;
    const auto reopened = m4::Index::Open(path.string(), m4_options, &m4index);
    Require(reopened.ok, "reopen M4 with churn-only debt policy: " + reopened.message);
  }
  auto queries = Queries();
  Measure("initial_snapshot_validation", [&] {
    Snapshot("initial", path, files, analyzer, native, m4index);
  });
  Measure("initial_expected_oracle_validation", [&] {
    EmitExpected("initial", files, analyzer, queries);
  });
  Measure("initial_oracle_validation", [&] {
    EmitOracle("initial", native, m4index.get(), queries,
               native ? NativeLiveDocs(native) : m4index->GetStats().live_docs);
  });
  RunTimedQueries("initial", native, m4index.get(), queries);

  uint32_t next_file = base_files + 1;
  for (uint32_t cycle = 0; cycle < 3; ++cycle) {
    const std::string stage = "burst" + std::to_string(cycle + 1);
    std::vector<uint32_t> update_ids;
    std::vector<uint32_t> delete_ids;
    for (uint32_t n = 0; n < 16; ++n) {
      const uint32_t id = 1 + ((cycle * 97 + n * 53) % base_files);
      if (files.count(id)) update_ids.push_back(id);
    }
    for (uint32_t n = 0; n < 8; ++n) {
      const uint32_t id = 1 + ((cycle * 131 + 19 + n * 71) % base_files);
      if (files.count(id) && std::find(update_ids.begin(), update_ids.end(), id) == update_ids.end()) delete_ids.push_back(id);
    }

    // Generate synthetic source payloads outside the measured storage calls.
    std::vector<File> replacements;
    replacements.reserve(update_ids.size());
    for (uint32_t id : update_ids)
      replacements.push_back(MakeFile(id, files.at(id).generation + 1, files.at(id).kind, analyzer));
    std::vector<File> new_files;
    for (uint32_t n = 0; n < 8; ++n) new_files.push_back(MakeFile(next_file++, 0, 'j', analyzer, 16));
    for (uint32_t n = 0; n < 8; ++n) new_files.push_back(MakeFile(next_file++, 0, 'l', analyzer, 12));
    for (uint32_t n = 0; n < 4; ++n) new_files.push_back(MakeFile(next_file++, 0, 's', analyzer, 8));
    const std::string freshness = "freshsignalcycle" + std::to_string(cycle);
    new_files.front().rows.front().text = MakeText(new_files.front().id, 0, 0, 'j', freshness);
    AnalyzeRow(new_files.front().rows.front(), analyzer);
    std::vector<Row> burst_rows;
    uint64_t burst_docs = 0;
    for (const auto& file : new_files) {
      burst_docs += file.rows.size();
      burst_rows.insert(burst_rows.end(), file.rows.begin(), file.rows.end());
    }

    uint64_t mutation_text_bytes = TextBytes(burst_rows);
    for (const auto& file : replacements) mutation_text_bytes += TextBytes(file.rows);
    EmitMetric(stage + "_mutations", "logical_input_text_bytes",
               static_cast<double>(mutation_text_bytes));
    Measure(stage + "_mutations", [&] {
      if (!native && batch_mode) {
        std::vector<m4::Mutation> update_mutations;
        for (const auto& replacement : replacements) {
          m4::SearchResult matches;
          const auto status = m4index->SearchFilter(
              m4::FilterExpr::Uint32Eq("file_id", replacement.id), &matches);
          Require(status.ok, "M4 enumerate replacement: " + status.message);
          for (const auto& hit : matches.hits)
            update_mutations.emplace_back(m4::MutationKind::DELETE, hit.pk);
          for (const auto& row : replacement.rows)
            update_mutations.emplace_back(m4::MutationKind::UPSERT, row.pk,
                                          M4Document(row));
        }
        ApplyM4Mutations(*m4index, update_mutations, true, "update");
      } else {
        for (auto& replacement : replacements) {
          const uint32_t id = replacement.id;
          const auto start = Clock::now();
          if (native) { NativeDeleteFile(native, id); NativeWrite(native, replacement.rows, true); }
          else { M4DeleteFile(*m4index, id, false); M4Write(*m4index, replacement.rows, false); }
          const auto end = Clock::now();
          EmitLatency(stage, "file_update_ms", std::chrono::duration<double, std::milli>(end - start).count());
          files[id] = std::move(replacement);
        }
      }
      if (!delete_ids.empty()) {
        const auto start = Clock::now();
        if (native) NativeDeleteFiles(native, delete_ids);
        else if (batch_mode) {
          std::vector<m4::FilterExpr> clauses;
          clauses.reserve(delete_ids.size());
          for (uint32_t id : delete_ids)
            clauses.push_back(m4::FilterExpr::Uint32Eq("file_id", id));
          m4::SearchResult matches;
          const auto status = m4index->SearchFilter(
              m4::FilterExpr::Or(std::move(clauses)), &matches);
          Require(status.ok, "M4 enumerate batched deletes: " + status.message);
          std::vector<m4::Mutation> deletes;
          deletes.reserve(matches.hits.size());
          for (const auto& hit : matches.hits)
            deletes.emplace_back(m4::MutationKind::DELETE, hit.pk);
          ApplyM4Mutations(*m4index, deletes, true, "delete");
        } else {
          M4DeleteFiles(*m4index, delete_ids, false);
        }
        const auto end = Clock::now();
        EmitLatency(stage, "file_delete_batch_ms", std::chrono::duration<double, std::milli>(end - start).count());
        for (uint32_t id : delete_ids) files.erase(id);
      }
      if (native) NativeWrite(native, burst_rows, true);
      else M4Write(*m4index, burst_rows, batch_mode);
      for (auto& replacement : replacements)
        if (batch_mode) files[replacement.id] = std::move(replacement);
    });
    for (auto& file : new_files) files.emplace(file.id, std::move(file));
    EmitMetric(stage, "burst_new_docs", static_cast<double>(burst_docs));

    // Freshness is measured from completion of the storage write through the
    // first successful search, before an explicit flush/maintenance boundary.
    const std::string freshness_query = "freshsignalcycle" + std::to_string(cycle);
    const auto fresh_start = Clock::now();
    const Query freshness_probe{"freshness", freshness_query, "", {}};
    const auto fresh_hits = native ? NativeSearch(native, freshness_probe, 5)
                                   : M4Search(*m4index, freshness_probe, 5);
    const auto fresh_end = Clock::now();
    Require(!fresh_hits.empty(), "burst sentinel was not searchable immediately after write");
    EmitLatency(stage, "freshness_lag_ms", std::chrono::duration<double, std::milli>(fresh_end - fresh_start).count());

    Measure(stage + "_flush_maintenance", [&] {
      if (native) {
        NativeFlush(native);
      } else {
        const auto status = m4index->Flush();
        Require(status.ok, "M4 burst flush: " + status.message);
      }
    });
    Measure(stage + "_snapshot_validation", [&] {
      Snapshot(stage, path, files, analyzer, native, m4index);
    });
    Measure(stage + "_expected_oracle_validation", [&] {
      EmitExpected(stage, files, analyzer, queries);
    });
    Measure(stage + "_oracle_validation", [&] {
      EmitOracle(stage, native, m4index.get(), queries,
                 native ? NativeLiveDocs(native) : m4index->GetStats().live_docs);
    });
    RunTimedQueries(stage, native, m4index.get(), queries);
  }

  if (m4index) {
    Measure("final_close", [&] { m4index.reset(); });
    Measure("final_reopen", [&] {
      const auto reopened = m4::Index::Open(path.string(), m4_options, &m4index);
      Require(reopened.ok, "reopen M4 after final durable flush: " + reopened.message);
    });
    Measure("reopen_snapshot_validation", [&] {
      Snapshot("reopen", path, files, analyzer, native, m4index);
    });
    Measure("reopen_expected_oracle_validation", [&] {
      EmitExpected("reopen", files, analyzer, queries);
    });
    Measure("reopen_oracle_validation", [&] {
      EmitOracle("reopen", native, m4index.get(), queries,
                 m4index->GetStats().live_docs);
    });
    RunTimedQueries("reopen", native, m4index.get(), queries);
  }

  if (native) {
    NativeRequire(zvec_collection_close(native), "close native collection");
    NativeRequire(zvec_shutdown(), "shutdown native Zvec");
  } else {
    m4index.reset();
  }
  EmitMetric("whole_workload", "cpu_ms", CpuMs());
  EmitMetric("whole_workload", "physical_read_bytes", static_cast<double>(ReadProcIo("read_bytes:")));
  EmitMetric("whole_workload", "physical_write_bytes", static_cast<double>(ReadProcIo("write_bytes:")));
  return failures ? 1 : 0;
}
}  // namespace

int main(int argc, char** argv) {
  if (argc != 5) {
    std::cerr << "usage: native_m4_paired_benchmark <native|m4|m4-batch> <fresh-index-dir> <base-files> <jieba-dict-dir>\n";
    return 2;
  }
  try {
    return Run(argv[1], argv[2], static_cast<uint32_t>(std::stoul(argv[3])), argv[4]);
  } catch (const std::exception& e) {
    std::cerr << "FATAL\tuncaught exception: " << e.what() << '\n';
    return 3;
  }
}
