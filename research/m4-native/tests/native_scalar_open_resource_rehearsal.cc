// Bounded resource rehearsal or API-latency diagnostic for the exact M4 0–6
// scalar-open workload. Runs 7 scalar counts x HNSW/FLAT x 3 repetitions; each
// case creates, closes, reopens, closes, then removes one empty collection root.
// Latency mode emits per-call wall intervals, excludes close/schema/cleanup work,
// and is exploratory/non-qualifying; it is not an internal phase trace.
#include <zvec/c_api.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <sys/stat.h>

namespace fs = std::filesystem;

static void Check(zvec_error_code_t status, const char* label) {
  if (status != ZVEC_OK) {
    throw std::runtime_error(std::string(label) + ": " + zvec_error_code_to_string(status));
  }
}

static zvec_collection_schema_t* MakeSchema(unsigned scalar_count, zvec_index_type_t vector_type) {
  auto* schema = zvec_collection_schema_create("m4_scalar_open_resource_rehearsal");
  if (!schema) throw std::runtime_error("collection schema create failed");
  const std::array<const char*, 6> names = {
      "logical_pk", "generation", "file_id", "ancestor_directory_ids", "file_name", "symbol_type"};
  const std::array<zvec_data_type_t, 6> types = {
      ZVEC_DATA_TYPE_STRING, ZVEC_DATA_TYPE_UINT32, ZVEC_DATA_TYPE_UINT32,
      ZVEC_DATA_TYPE_ARRAY_UINT32, ZVEC_DATA_TYPE_STRING, ZVEC_DATA_TYPE_STRING};
  std::vector<zvec_field_schema_t*> fields;
  try {
    for (unsigned i = 0; i < scalar_count; ++i) {
      auto* field = zvec_field_schema_create(names[i], types[i], i == 5, 0);
      if (!field) throw std::runtime_error("scalar field schema create failed");
      auto* params = zvec_index_params_create(ZVEC_INDEX_TYPE_INVERT);
      if (!params) throw std::runtime_error("scalar index params create failed");
      const bool range = i == 1 || i == 2 || i == 3;
      const bool wildcard = i == 4;
      Check(zvec_index_params_set_invert_params(params, range, wildcard), "configure scalar index");
      Check(zvec_field_schema_set_index_params(field, params), "attach scalar index");
      zvec_index_params_destroy(params);
      Check(zvec_collection_schema_add_field(schema, field), "add scalar field");
      fields.push_back(field);
    }
    auto* embedding = zvec_field_schema_create("embedding", ZVEC_DATA_TYPE_VECTOR_FP32, false, 4);
    if (!embedding) throw std::runtime_error("vector field schema create failed");
    auto* vector_params = zvec_index_params_create(vector_type);
    if (!vector_params) throw std::runtime_error("vector index params create failed");
    Check(zvec_index_params_set_metric_type(vector_params, ZVEC_METRIC_TYPE_COSINE), "set vector metric");
    if (vector_type == ZVEC_INDEX_TYPE_HNSW)
      Check(zvec_index_params_set_hnsw_params(vector_params, 16, 100), "set HNSW params");
    Check(zvec_field_schema_set_index_params(embedding, vector_params), "attach vector index");
    Check(zvec_collection_schema_add_field(schema, embedding), "add vector field");
    zvec_index_params_destroy(vector_params);
    zvec_field_schema_destroy(embedding);
    for (auto* field : fields) zvec_field_schema_destroy(field);
    return schema;
  } catch (...) {
    for (auto* field : fields) zvec_field_schema_destroy(field);
    zvec_collection_schema_destroy(schema);
    throw;
  }
}

struct ApiSample {
  unsigned scalar_count;
  const char* vector_type;
  unsigned repetition;
  const char* operation;
  double latency_us;
};

static std::pair<uintmax_t, uintmax_t> TreeBytes(const fs::path& root) {
  uintmax_t apparent = 0, allocated = 0;
  for (const auto& entry : fs::recursive_directory_iterator(root)) {
    if (!entry.is_regular_file()) continue;
    const auto size = entry.file_size();
    apparent += size;
    struct stat st{};
    if (::stat(entry.path().c_str(), &st) != 0) throw std::runtime_error("stat collection file failed");
    allocated += static_cast<uintmax_t>(st.st_blocks) * 512;
  }
  return {apparent, allocated};
}

int main(int argc, char** argv) {
  if (argc != 3) return 2;
  const std::string mode(argv[2]);
  const bool latency_mode = mode == "--m4-scalar-open-latency-diagnostic";
  const bool resource_mode = mode == "--m4-scalar-open-resource-rehearsal";
  if (!latency_mode && !resource_mode) return 2;
  const fs::path root(argv[1]);
  if (fs::exists(root)) throw std::runtime_error("rehearsal root already exists");
  fs::create_directories(root);
  Check(zvec_initialize(nullptr), "zvec initialize");
  const auto started = std::chrono::steady_clock::now();
  uint64_t opens = 0;
  uintmax_t max_apparent = 0, max_allocated = 0;
  std::vector<ApiSample> samples;
  samples.reserve(84);
  try {
    for (unsigned count = 0; count <= 6; ++count) {
      for (auto type : {ZVEC_INDEX_TYPE_HNSW, ZVEC_INDEX_TYPE_FLAT}) {
        const char* type_name = type == ZVEC_INDEX_TYPE_HNSW ? "hnsw" : "flat";
        for (unsigned rep = 0; rep < 3; ++rep) {
          const fs::path collection_path = root / (std::to_string(count) + "-" + type_name + "-" + std::to_string(rep));
          auto* schema = MakeSchema(count, type);
          zvec_collection_t* collection = nullptr;
          const auto create_started = std::chrono::steady_clock::now();
          const auto create_status = zvec_collection_create_and_open(
              collection_path.c_str(), schema, nullptr, &collection);
          const auto create_finished = std::chrono::steady_clock::now();
          Check(create_status, "create and open collection");
          ++opens;
          if (latency_mode) {
            samples.push_back({count, type_name, rep, "create_and_open",
                std::chrono::duration<double, std::micro>(create_finished - create_started).count()});
          }
          zvec_collection_schema_destroy(schema);
          Check(zvec_collection_close(collection), "close created collection");
          collection = nullptr;
          const auto reopen_started = std::chrono::steady_clock::now();
          const auto reopen_status = zvec_collection_open(collection_path.c_str(), nullptr, &collection);
          const auto reopen_finished = std::chrono::steady_clock::now();
          Check(reopen_status, "reopen collection");
          ++opens;
          if (latency_mode) {
            samples.push_back({count, type_name, rep, "reopen",
                std::chrono::duration<double, std::micro>(reopen_finished - reopen_started).count()});
          }
          Check(zvec_collection_close(collection), "close reopened collection");
          if (resource_mode) {
            const auto sizes = TreeBytes(collection_path);
            max_apparent = std::max(max_apparent, sizes.first);
            max_allocated = std::max(max_allocated, sizes.second);
          }
          fs::remove_all(collection_path);
        }
      }
    }
    Check(zvec_shutdown(), "zvec shutdown");
  } catch (...) {
    (void)zvec_shutdown();
    throw;
  }
  const auto elapsed_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
  fs::remove_all(root);
  if (latency_mode) {
    std::cout << std::fixed << std::setprecision(3);
    for (const auto& sample : samples) {
      std::cout << "{\"row_kind\":\"api_latency\",\"scalar_count\":" << sample.scalar_count
                << ",\"vector_type\":\"" << sample.vector_type << "\",\"repetition\":" << sample.repetition
                << ",\"operation\":\"" << sample.operation << "\",\"latency_us\":" << sample.latency_us << "}\n";
    }
    std::cout << "{\"row_kind\":\"summary\",\"workload\":\"m4-scalar-open-latency-diagnostic\",\"scalar_counts\":[0,1,2,3,4,5,6],\"vector_types\":[\"HNSW\",\"FLAT\"],\"repetitions\":3,\"api_opens\":" << opens
              << ",\"api_latency_samples\":" << samples.size()
              << ",\"collection_roots_at_once\":1,\"documents\":0,\"queries\":0,\"elapsed_ms\":" << elapsed_ms << "}\n";
    return opens == 84 && samples.size() == 84 ? 0 : 1;
  }
  std::cout << "{\"workload\":\"m4-scalar-open-resource-rehearsal\",\"scalar_counts\":[0,1,2,3,4,5,6],\"vector_types\":[\"HNSW\",\"FLAT\"],\"repetitions\":3,\"api_opens\":" << opens
            << ",\"collection_roots_at_once\":1,\"documents\":0,\"queries\":0,\"elapsed_ms\":" << elapsed_ms
            << ",\"max_collection_apparent_bytes\":" << max_apparent
            << ",\"max_collection_allocated_bytes\":" << max_allocated << "}\n";
  return opens == 84 ? 0 : 1;
}