// Research-only differential for zg's indexed file_id: Uint32 EQ/IN usage.
// Native and M4 result sets are compared by logical primary key, never DocId.
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <iostream>
#include <limits>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <unistd.h>
#include <vector>

#include "m4/index.h"
#include <zvec/c_api.h>

namespace fs = std::filesystem;
namespace {
int checks = 0;
int failures = 0;

void Check(bool ok, const std::string& label) {
  ++checks;
  if (!ok) {
    ++failures;
    std::cerr << "FAIL: " << label << '\n';
  }
}

bool CheckCode(zvec_error_code_t code, const std::string& label) {
  const bool ok = code == ZVEC_OK;
  Check(ok, label + (ok ? "" : ": " +
      std::string(zvec_error_code_to_string(code))));
  return ok;
}

struct NativeCollection {
  zvec_collection_t* handle{nullptr};
  ~NativeCollection() {
    if (handle) (void)zvec_collection_close(handle);
  }
};

zvec_collection_t* CreateNative(const std::string& path) {
  auto* invert = zvec_index_params_create(ZVEC_INDEX_TYPE_INVERT);
  auto* id_field = zvec_field_schema_create(
      "file_id", ZVEC_DATA_TYPE_UINT32, false, 0);
  auto* text_params = zvec_index_params_create(ZVEC_INDEX_TYPE_FTS);
  auto* filters = zvec_string_array_create(1);
  zvec_string_array_add(filters, 0, "lowercase");
  CheckCode(zvec_index_params_set_invert_params(invert, true, false),
            "configure Uint32 inverted index");
  CheckCode(zvec_field_schema_set_index_params(id_field, invert),
            "attach Uint32 inverted index");
  CheckCode(zvec_index_params_set_fts_params(text_params, "standard", filters,
                                             ""),
            "configure FTS fixture index");
  auto* text_field = zvec_field_schema_create(
      "content", ZVEC_DATA_TYPE_STRING, false, 0);
  CheckCode(zvec_field_schema_set_index_params(text_field, text_params),
            "attach FTS fixture index");
  auto* schema = zvec_collection_schema_create("m4_p21_uint32");
  CheckCode(zvec_collection_schema_add_field(schema, id_field),
            "add Uint32 field");
  CheckCode(zvec_collection_schema_add_field(schema, text_field),
            "add FTS field");
  zvec_collection_t* collection = nullptr;
  const auto code = zvec_collection_create_and_open(
      path.c_str(), schema, nullptr, &collection);
  CheckCode(code, "create native Uint32 collection");
  zvec_collection_schema_destroy(schema);
  zvec_field_schema_destroy(text_field);
  zvec_field_schema_destroy(id_field);
  zvec_index_params_destroy(text_params);
  zvec_index_params_destroy(invert);
  zvec_string_array_destroy(filters);
  return collection;
}

struct Row {
  std::string pk;
  std::optional<uint32_t> file_id;
  std::string text;
};

bool NativeUpsert(zvec_collection_t* collection, const Row& row,
                  const std::string& label, bool allow_missing_rejection = false) {
  auto* doc = zvec_doc_create();
  zvec_doc_set_pk(doc, row.pk.c_str());
  if (row.file_id) {
    CheckCode(zvec_doc_add_field_by_value(
                  doc, "file_id", ZVEC_DATA_TYPE_UINT32, &*row.file_id,
                  sizeof(*row.file_id)),
              label + " set file_id " + row.pk);
  }
  CheckCode(zvec_doc_add_field_by_value(
                doc, "content", ZVEC_DATA_TYPE_STRING, row.text.data(),
                row.text.size()),
            label + " set content " + row.pk);
  const zvec_doc_t* docs[] = {doc};
  zvec_write_result_t* results = nullptr;
  size_t count = 0;
  const auto code = zvec_collection_upsert_with_results(
      collection, docs, 1, &results, &count);
  const bool row_ok = code == ZVEC_OK && count == 1 && results &&
                      results[0].code == ZVEC_OK;
  if (allow_missing_rejection) {
    std::cout << "native_missing_field_write="
              << (row_ok ? "accepted" : "rejected") << '\n';
  } else {
    CheckCode(code, label + " native upsert " + row.pk);
    Check(row_ok, label + " native row status " + row.pk);
  }
  zvec_write_results_free(results, count);
  zvec_doc_destroy(doc);
  return row_ok;
}

bool NativeDelete(zvec_collection_t* collection, const std::string& pk) {
  const char* pks[] = {pk.c_str()};
  zvec_write_result_t* results = nullptr;
  size_t count = 0;
  const auto code = zvec_collection_delete_with_results(
      collection, pks, 1, &results, &count);
  bool ok = CheckCode(code, "native delete " + pk);
  ok = ok && count == 1 && results && results[0].code == ZVEC_OK;
  Check(ok, "native delete row status " + pk);
  zvec_write_results_free(results, count);
  return ok;
}

struct QueryResult {
  zvec_error_code_t code{ZVEC_OK};
  std::vector<std::string> pks;
};

QueryResult NativeQuery(zvec_collection_t* collection,
                        const std::string& filter, int top_k,
                        const std::string& fts_query = {}) {
  QueryResult result;
  auto* query = zvec_vector_query_create();
  CheckCode(zvec_vector_query_set_topk(query, top_k), "native query topk");
  CheckCode(zvec_vector_query_set_filter(query, filter.c_str()),
            "native query filter " + filter);
  zvec_fts_t* fts = nullptr;
  zvec_fts_query_params_t* params = nullptr;
  if (!fts_query.empty()) {
    CheckCode(zvec_vector_query_set_field_name(query, "content"),
              "native FTS query field");
    fts = zvec_fts_create();
    CheckCode(zvec_fts_set_query_string(fts, fts_query.c_str()),
              "native FTS query text");
    CheckCode(zvec_vector_query_set_fts(query, fts), "attach native FTS");
    params = zvec_query_params_fts_create("OR");
    const auto params_code = zvec_vector_query_set_fts_params(query, params);
    CheckCode(params_code, "attach native FTS params");
    if (params_code == ZVEC_OK) params = nullptr;  // setter takes ownership
  }
  zvec_doc_t** docs = nullptr;
  size_t count = 0;
  result.code = zvec_collection_query(collection, query, &docs, &count);
  if (result.code == ZVEC_OK) {
    for (size_t i = 0; i < count; ++i) {
      const char* pk = zvec_doc_get_pk_pointer(docs[i]);
      result.pks.emplace_back(pk ? pk : "");
    }
  }
  zvec_docs_free(docs, count);
  zvec_vector_query_destroy(query);
  if (params) zvec_query_params_fts_destroy(params);
  if (fts) zvec_fts_destroy(fts);
  return result;
}

std::set<std::string> AsSet(const std::vector<std::string>& pks) {
  return {pks.begin(), pks.end()};
}

std::string InFilter(const std::vector<uint32_t>& values) {
  std::string filter = "file_id IN (";
  for (size_t i = 0; i < values.size(); ++i) {
    if (i) filter += ", ";
    filter += std::to_string(values[i]);
  }
  return filter + ")";
}

std::set<std::string> Expected(
    const std::map<std::string, std::optional<uint32_t>>& rows,
    const std::vector<uint32_t>& values) {
  std::set<std::string> expected;
  for (const auto& [pk, stored] : rows) {
    if (stored && std::find(values.begin(), values.end(), *stored) != values.end()) {
      expected.insert(pk);
    }
  }
  return expected;
}

void CheckNativeFilter(zvec_collection_t* native,
                       const std::map<std::string, std::optional<uint32_t>>& rows,
                       const std::vector<uint32_t>& values,
                       const std::string& label) {
  const std::string filter = values.size() == 1
      ? "file_id = " + std::to_string(values.front())
      : InFilter(values);
  const auto got = NativeQuery(native, filter, 100);
  Check(got.code == ZVEC_OK, label + " native filter accepted");
  if (got.code == ZVEC_OK) {
    Check(AsSet(got.pks) == Expected(rows, values),
          label + " native result PK set");
    Check(AsSet(got.pks).size() == got.pks.size(),
          label + " native no duplicate results");
  }
}

void CheckM4Filter(const m4::Index& index,
                   const std::map<std::string, std::optional<uint32_t>>& rows,
                   const std::vector<uint32_t>& values,
                   const std::string& label) {
  m4::SearchResult result;
  const auto status = values.size() == 1
      ? index.SearchInvertUint32("file_id", values.front(), &result)
      : index.SearchInvertUint32In("file_id", values, &result);
  Check(status.ok, label + " M4 typed filter accepted");
  if (!status.ok) return;
  std::vector<std::string> pks;
  for (const auto& hit : result.hits) pks.push_back(hit.pk);
  Check(AsSet(pks) == Expected(rows, values), label + " M4 result PK set");
  Check(AsSet(pks).size() == pks.size(), label + " M4 no duplicate results");
}

void CompareValidFilters(zvec_collection_t* native, const m4::Index& index,
                         const std::map<std::string, std::optional<uint32_t>>& rows,
                         const std::string& phase) {
  const uint32_t max_value = std::numeric_limits<uint32_t>::max();
  const std::vector<std::vector<uint32_t>> filters = {
      {0u}, {1u}, {9u}, {10u}, {2147483648u}, {max_value},
      {0u, 1u, max_value, 42u}, {42u, 42u, max_value, 0u}};
  for (const auto& values : filters) {
    const std::string label = phase + " " + InFilter(values);
    CheckNativeFilter(native, rows, values, label);
    CheckM4Filter(index, rows, values, label);
    m4::SearchResult result;
    const auto status = index.SearchInvertUint32In("file_id", values, &result);
    if (status.ok) {
      const auto native_result = NativeQuery(native, InFilter(values), 100);
      if (native_result.code == ZVEC_OK) {
        Check(AsSet(native_result.pks) == AsSet([&] {
          std::vector<std::string> pks;
          for (const auto& hit : result.hits) pks.push_back(hit.pk);
          return pks;
        }()), label + " native/M4 differential");
      }
    }
  }
}

void CheckEmptyIn(zvec_collection_t* native, const m4::Index& index) {
  m4::SearchResult m4_result;
  const auto m4_status = index.SearchInvertUint32In("file_id", {}, &m4_result);
  Check(m4_status.ok && m4_result.hits.empty(), "M4 empty IN matches empty set");
  const auto native_result = NativeQuery(native, "file_id IN ()", 100);
  if (native_result.code == ZVEC_OK) {
    Check(native_result.pks.empty(), "native empty IN matches empty set");
    std::cout << "native_empty_in=accepted empty=" << native_result.pks.empty()
              << '\n';
  } else {
    std::cout << "native_empty_in=rejected code="
              << zvec_error_code_to_string(native_result.code)
              << "; M4_empty_in=accepted-empty\n";
  }
}

void CheckFilterBeforeTopK(zvec_collection_t* native, const m4::Index& index) {
  const auto native_result = NativeQuery(native, "file_id IN (0)", 1, "signal");
  Check(native_result.code == ZVEC_OK, "native FTS+Uint32 filter accepted");
  Check(AsSet(native_result.pks) == std::set<std::string>{"pk-zero"},
        "native scalar filter is applied before top-k");

  m4::SearchResult candidates;
  const auto filter_status =
      index.SearchInvertUint32In("file_id", {0u}, &candidates);
  Check(filter_status.ok, "M4 top-k fixture scalar candidate query");
  std::set<std::string> candidate_pks;
  for (const auto& hit : candidates.hits) candidate_pks.insert(hit.pk);
  m4::SearchResult all_hits;
  const auto all_status = index.SearchFts("signal", 100, &all_hits);
  Check(all_status.ok, "M4 top-k fixture full candidate retrieval");
  std::vector<std::string> filtered_before_topk;
  for (const auto& hit : all_hits.hits) {
    if (candidate_pks.count(hit.pk)) filtered_before_topk.push_back(hit.pk);
    if (filtered_before_topk.size() == 1) break;
  }
  Check(filtered_before_topk == std::vector<std::string>{"pk-zero"},
        "M4 scalar candidate filter precedes top-k cutoff");
  m4::SearchResult naive_top1;
  Check(index.SearchFts("signal", 1, &naive_top1).ok,
        "M4 unfiltered top-1 query");
  Check(!naive_top1.hits.empty() && naive_top1.hits.front().pk == "pk-one",
        "fixture puts a non-matching hit above the matching hit");
  Check(std::find(filtered_before_topk.begin(), filtered_before_topk.end(),
                  naive_top1.hits.empty() ? "" : naive_top1.hits.front().pk) ==
            filtered_before_topk.end(),
        "post-top-k filtering would incorrectly drop the matching hit");
}

}  // namespace

int main() {
  const fs::path root = fs::temp_directory_path() /
      ("m4-p21-native-u32-" + std::to_string(::getpid()));
  fs::create_directories(root);
  const std::string native_path = (root / "native").string();
  const std::string m4_path = (root / "m4").string();
  std::map<std::string, std::optional<uint32_t>> rows;
  const std::vector<Row> initial = {
      {"pk-zero", 0u, "signal"},
      {"pk-one", 1u, "signal signal signal signal signal signal"},
      {"pk-nine", 9u, "filler"},
      {"pk-ten", 10u, "filler"},
      {"pk-half", 2147483648u, "filler"},
      {"pk-max", std::numeric_limits<uint32_t>::max(), "filler"},
      {"pk-dup-a", 42u, "filler"},
      {"pk-dup-b", 42u, "filler"},
      {"pk-missing", std::nullopt, "filler"},
  };
  NativeCollection native{CreateNative(native_path)};
  if (!native.handle) return 2;
  m4::Options options;
  options.fts_field = "content";
  options.fsync_wal = false;
  std::unique_ptr<m4::Index> index;
  auto status = m4::Index::Open(m4_path, options, &index);
  Check(status.ok, "M4 open: " + status.message);
  if (!status.ok) return 2;

  for (const auto& row : initial) {
    NativeUpsert(native.handle, row, "initial", !row.file_id.has_value());
    m4::Document doc;
    doc.text = row.text;
    if (row.file_id) {
      doc.values.push_back(m4::ScalarValue::Unsigned32("file_id", *row.file_id));
      rows[row.pk] = row.file_id;
    } else {
      rows[row.pk] = std::nullopt;
    }
    Check(index->Upsert(row.pk, doc).ok, "M4 initial upsert " + row.pk);
  }
  CheckCode(zvec_collection_flush(native.handle), "native initial flush");
  Check(index->Flush().ok, "M4 initial flush");
  CompareValidFilters(native.handle, *index, rows, "initial");
  CheckEmptyIn(native.handle, *index);
  CheckFilterBeforeTopK(native.handle, *index);

  const Row updated{"pk-one", std::numeric_limits<uint32_t>::max(),
                    "signal signal signal signal signal signal"};
  NativeUpsert(native.handle, updated, "update");
  m4::Document updated_doc;
  updated_doc.text = updated.text;
  updated_doc.values.push_back(
      m4::ScalarValue::Unsigned32("file_id", *updated.file_id));
  Check(index->Upsert(updated.pk, updated_doc).ok, "M4 update file_id");
  rows[updated.pk] = updated.file_id;
  Check(NativeDelete(native.handle, "pk-nine"), "native delete status");
  Check(index->Delete("pk-nine").ok, "M4 delete file_id row");
  rows.erase("pk-nine");
  CompareValidFilters(native.handle, *index, rows, "after-update-delete-before-flush");

  CheckCode(zvec_collection_flush(native.handle), "native mutation flush");
  Check(index->Flush().ok, "M4 mutation flush");
  CompareValidFilters(native.handle, *index, rows, "after-flush");
  CheckCode(zvec_collection_optimize(native.handle), "native optimize");
  Check(index->CompactNow().ok, "M4 compact");
  CompareValidFilters(native.handle, *index, rows, "after-compact");

  CheckCode(zvec_collection_close(native.handle), "native close before reopen");
  native.handle = nullptr;
  CheckCode(zvec_collection_open(native_path.c_str(), nullptr, &native.handle),
            "native reopen");
  index.reset();
  status = m4::Index::Open(m4_path, options, &index);
  Check(status.ok, "M4 reopen: " + status.message);
  if (status.ok && native.handle) {
    CompareValidFilters(native.handle, *index, rows, "after-reopen");
    CheckEmptyIn(native.handle, *index);
  }

  std::cout << "checks=" << checks << " failures=" << failures << '\n';
  index.reset();
  if (native.handle) {
    (void)zvec_collection_close(native.handle);
    native.handle = nullptr;
  }
  fs::remove_all(root);
  return failures == 0 ? 0 : 1;
}
