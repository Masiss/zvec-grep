#include <zvec/c_api.h>
#include <chrono>
#include <filesystem>
#include <iostream>
#include <string>
#include <time.h>
#include <vector>

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

static double ThreadCpuMs() {
  timespec ts{};
  if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts) != 0) return -1;
  return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

static void Check(zvec_error_code_t code, const char* what) {
  if (code != ZVEC_OK) throw std::runtime_error(what);
}

static zvec_field_schema_t* Field(std::vector<zvec_field_schema_t*>& owned,
                                  const char* name, zvec_data_type_t type,
                                  bool nullable, uint32_t dim = 0) {
  auto* field = zvec_field_schema_create(name, type, nullable, dim);
  if (!field) throw std::runtime_error("field create");
  owned.push_back(field);
  return field;
}

static void Invert(zvec_field_schema_t* field, bool range, bool wildcard) {
  auto* params = zvec_index_params_create(ZVEC_INDEX_TYPE_INVERT);
  Check(zvec_index_params_set_invert_params(params, range, wildcard), "invert params");
  Check(zvec_field_schema_set_index_params(field, params), "attach invert params");
  zvec_index_params_destroy(params);
}

static zvec_collection_schema_t* Schema(uint32_t invert_mask, zvec_index_type_t vector_type) {
  auto* schema = zvec_collection_schema_create("m4_open_probe");
  if (!schema) throw std::runtime_error("schema create");
  std::vector<zvec_field_schema_t*> owned;
  auto* logical = Field(owned, "logical_pk", ZVEC_DATA_TYPE_STRING, false);
  auto* generation = Field(owned, "generation", ZVEC_DATA_TYPE_UINT32, false);
  auto* file_id = Field(owned, "file_id", ZVEC_DATA_TYPE_UINT32, false);
  auto* dirs = Field(owned, "ancestor_directory_ids", ZVEC_DATA_TYPE_ARRAY_UINT32, false);
  auto* name = Field(owned, "file_name", ZVEC_DATA_TYPE_STRING, false);
  auto* symbol = Field(owned, "symbol_type", ZVEC_DATA_TYPE_STRING, true);
  if (invert_mask & (1U << 0)) Invert(logical, false, false);
  if (invert_mask & (1U << 1)) Invert(generation, true, false);
  if (invert_mask & (1U << 2)) Invert(file_id, true, false);
  if (invert_mask & (1U << 3)) Invert(dirs, true, false);
  if (invert_mask & (1U << 4)) Invert(name, false, true);
  if (invert_mask & (1U << 5)) Invert(symbol, false, false);
  auto* embedding = Field(owned, "embedding", ZVEC_DATA_TYPE_VECTOR_FP32, false, 4);
  auto* params = zvec_index_params_create(vector_type);
  if (!params) throw std::runtime_error("vector params create");
  Check(zvec_index_params_set_metric_type(params, ZVEC_METRIC_TYPE_COSINE), "metric");
  if (vector_type == ZVEC_INDEX_TYPE_HNSW)
    Check(zvec_index_params_set_hnsw_params(params, 16, 100), "hnsw params");
  Check(zvec_field_schema_set_index_params(embedding, params), "attach vector params");
  zvec_index_params_destroy(params);
  for (auto* field : owned) Check(zvec_collection_schema_add_field(schema, field), "add field");
  for (auto* field : owned) zvec_field_schema_destroy(field);
  return schema;
}

int main(int argc, char** argv) {
  if (argc != 3 && argc != 4) return 2;
  const fs::path root(argv[1]);
  const int repeats = std::stoi(argv[2]);
  const std::string case_filter = argc == 4 ? argv[3] : "";
  fs::create_directories(root);
  Check(zvec_initialize(nullptr), "initialize");
  struct Case { const char* label; uint32_t invert_mask; zvec_index_type_t vector; };
  const Case cases[] = {
      {"exact-hnsw", 0x3f, ZVEC_INDEX_TYPE_HNSW},
      {"exact-flat", 0x3f, ZVEC_INDEX_TYPE_FLAT},
      {"no-scalar-hnsw", 0, ZVEC_INDEX_TYPE_HNSW},
      {"logical-pk-only", 1U << 0, ZVEC_INDEX_TYPE_HNSW},
      {"generation-only", 1U << 1, ZVEC_INDEX_TYPE_HNSW},
      {"file-id-only", 1U << 2, ZVEC_INDEX_TYPE_HNSW},
      {"ancestor-directories-only", 1U << 3, ZVEC_INDEX_TYPE_HNSW},
      {"file-name-only", 1U << 4, ZVEC_INDEX_TYPE_HNSW},
      {"symbol-type-only", 1U << 5, ZVEC_INDEX_TYPE_HNSW},
  };
  for (const auto& c : cases) {
    if (!case_filter.empty() && case_filter != c.label) continue;
    for (int i = 0; i < repeats; ++i) {
      const fs::path path = root / (std::string(c.label) + "-" + std::to_string(i));
      const auto schema_wall0 = Clock::now();
      const double schema_cpu0 = ThreadCpuMs();
      auto* schema = Schema(c.invert_mask, c.vector);
      const double schema_cpu = ThreadCpuMs() - schema_cpu0;
      const double schema_wall = std::chrono::duration<double, std::milli>(Clock::now() - schema_wall0).count();
      const auto wall0 = Clock::now();
      const double cpu0 = ThreadCpuMs();
      zvec_collection_t* collection = nullptr;
      const auto status = zvec_collection_create_and_open(path.c_str(), schema, nullptr, &collection);
      const double cpu = ThreadCpuMs() - cpu0;
      const double wall = std::chrono::duration<double, std::milli>(Clock::now() - wall0).count();
      zvec_collection_schema_destroy(schema);
      Check(status, "create_and_open");
      Check(zvec_collection_close(collection), "close");
      std::cout << "{\"case\":\"" << c.label << "\",\"iteration\":" << i
                << ",\"schema_wall_ms\":" << schema_wall << ",\"schema_thread_cpu_ms\":" << schema_cpu
                << ",\"open_wall_ms\":" << wall << ",\"open_thread_cpu_ms\":" << cpu
                << "}\n";
      fs::remove_all(path);
    }
  }
  Check(zvec_shutdown(), "shutdown");
  fs::remove_all(root);
  return 0;
}
