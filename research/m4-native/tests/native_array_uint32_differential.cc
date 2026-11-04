// Research-only differential for zg's ancestor_directory_ids ArrayUint32.
// Native and M4 results are compared by logical primary key, never DocId.
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
  Check(ok, label + ": " + zvec_error_code_to_string(code));
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
  auto* field = zvec_field_schema_create(
      "ancestor_directory_ids", ZVEC_DATA_TYPE_ARRAY_UINT32, false, 0);
  CheckCode(zvec_index_params_set_invert_params(invert, true, false),
            "configure ArrayUint32 inverted index");
  CheckCode(zvec_field_schema_set_index_params(field, invert),
            "attach ArrayUint32 inverted index");
  auto* schema = zvec_collection_schema_create("m4_p22_array_uint32");
  CheckCode(zvec_collection_schema_add_field(schema, field),
            "add ArrayUint32 field");
  zvec_collection_t* collection = nullptr;
  const auto code = zvec_collection_create_and_open(
      path.c_str(), schema, nullptr, &collection);
  CheckCode(code, "create native ArrayUint32 collection");
  zvec_collection_schema_destroy(schema);
  zvec_field_schema_destroy(field);
  zvec_index_params_destroy(invert);
  return collection;
}

struct Row {
  std::string pk;
  std::optional<std::vector<uint32_t>> values;
};

bool NativeUpsert(zvec_collection_t* collection, const Row& row,
                  bool allow_missing_rejection = false) {
  auto* doc = zvec_doc_create();
  zvec_doc_set_pk(doc, row.pk.c_str());
  if (row.values) {
    uint32_t empty_sentinel = 0;
    const void* data = row.values->empty()
                           ? static_cast<const void*>(&empty_sentinel)
                           : static_cast<const void*>(row.values->data());
    CheckCode(zvec_doc_add_field_by_value(
                  doc, "ancestor_directory_ids", ZVEC_DATA_TYPE_ARRAY_UINT32,
                  data, row.values->size() * sizeof(uint32_t)),
              "set native array " + row.pk);
  }
  const zvec_doc_t* docs[] = {doc};
  zvec_write_result_t* results = nullptr;
  size_t count = 0;
  const auto code = zvec_collection_upsert_with_results(
      collection, docs, 1, &results, &count);
  const bool row_ok = code == ZVEC_OK && count == 1 && results &&
                      results[0].code == ZVEC_OK;
  if (allow_missing_rejection) {
    Check(!row_ok, "native non-nullable array rejects missing field");
    std::cout << "native_missing_array_write="
              << (row_ok ? "accepted" : "rejected") << '\n';
  } else {
    CheckCode(code, "native upsert " + row.pk);
    Check(row_ok, "native row status " + row.pk);
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
                        const std::string& filter) {
  QueryResult result;
  auto* query = zvec_vector_query_create();
  CheckCode(zvec_vector_query_set_topk(query, 1000), "native query topk");
  CheckCode(zvec_vector_query_set_filter(query, filter.c_str()),
            "native query filter " + filter);
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
  return result;
}

std::set<std::string> AsSet(const std::vector<std::string>& pks) {
  return {pks.begin(), pks.end()};
}

std::string Filter(bool negate, const std::vector<uint32_t>& values) {
  std::string filter = "ancestor_directory_ids ";
  if (negate) filter += "NOT ";
  filter += "CONTAIN_ANY (";
  for (size_t i = 0; i < values.size(); ++i) {
    if (i) filter += ", ";
    filter += std::to_string(values[i]);
  }
  return filter + ")";
}

using Rows = std::map<std::string, std::optional<std::vector<uint32_t>>>;

std::set<std::string> Expected(const Rows& rows,
                               const std::vector<uint32_t>& operands,
                               bool negate) {
  std::set<std::string> expected;
  for (const auto& [pk, values] : rows) {
    if (!values) continue;  // missing fields are not in the non-null universe
    bool overlaps = false;
    for (uint32_t operand : operands) {
      if (std::find(values->begin(), values->end(), operand) != values->end()) {
        overlaps = true;
        break;
      }
    }
    if (negate ? !overlaps : overlaps) expected.insert(pk);
  }
  return expected;
}

void Compare(zvec_collection_t* native, m4::Index& index, const Rows& rows,
             const std::string& phase) {
  const uint32_t max = std::numeric_limits<uint32_t>::max();
  const std::vector<std::vector<uint32_t>> queries = {
      {}, {17u}, {17u, 18u}, {17u, 17u, 18u}, {0u, max}, {max}, {999u}};
  for (bool negate : {false, true}) {
    for (const auto& operands : queries) {
      const std::string label = phase + (negate ? " NOT CONTAIN_ANY" :
                                                        " CONTAIN_ANY") +
                                " operands=" + std::to_string(operands.size());
      const auto expected = Expected(rows, operands, negate);
      const auto native_result = NativeQuery(native, Filter(negate, operands));
      Check(native_result.code == ZVEC_OK, label + " native query accepted");
      if (native_result.code == ZVEC_OK) {
        Check(AsSet(native_result.pks) == expected, label + " native PK set");
        Check(AsSet(native_result.pks).size() == native_result.pks.size(),
              label + " native no duplicates");
      }

      m4::SearchResult result;
      const auto status = negate
          ? index.SearchArrayUint32NotContainsAny("ancestor_directory_ids",
                                                   operands, &result)
          : index.SearchArrayUint32ContainsAny("ancestor_directory_ids",
                                                operands, &result);
      Check(status.ok, label + " M4 query accepted: " + status.message);
      std::vector<std::string> pks;
      for (const auto& hit : result.hits) pks.push_back(hit.pk);
      Check(AsSet(pks) == expected, label + " M4 PK set");
      Check(AsSet(pks).size() == pks.size(), label + " M4 no duplicates");
    }
  }
}

void ReportComplementMaterialization(m4::Index& index, const Rows& rows) {
  const std::vector<uint32_t> operands{17u};
  m4::SearchResult any;
  m4::SearchResult not_any;
  const auto any_status = index.SearchArrayUint32ContainsAny(
      "ancestor_directory_ids", operands, &any);
  const auto not_status = index.SearchArrayUint32NotContainsAny(
      "ancestor_directory_ids", operands, &not_any);
  Check(any_status.ok && not_status.ok, "complement resource probe status");
  size_t non_null_docs = 0;
  for (const auto& [pk, values] : rows) {
    (void)pk;
    if (values) ++non_null_docs;
  }
  const uint64_t logical_docid_bytes =
      static_cast<uint64_t>(non_null_docs + any.hits.size()) * sizeof(m4::DocId);
  std::cout << "not_contains_any_fixture_non_null_docs=" << non_null_docs
            << " matched_docs=" << any.hits.size()
            << " returned_docs=" << not_any.hits.size()
            << " universe_plus_match_docid_payload_bytes="
            << logical_docid_bytes << " examined=" << not_any.examined << '\n';
}

void CheckOperandLimit(zvec_collection_t* native, m4::Index& index) {
  std::vector<uint32_t> too_many(33, 17u);
  m4::SearchResult result;
  const auto status = index.SearchArrayUint32ContainsAny(
      "ancestor_directory_ids", too_many, &result);
  Check(!status.ok, "M4 rejects >32 ArrayUint32 operands");
  const auto native_result = NativeQuery(native, Filter(false, too_many));
  Check(native_result.code != ZVEC_OK,
        "native validates >32 ArrayUint32 operands");
  std::cout << "operand_limit_33=native_"
            << (native_result.code == ZVEC_OK ? "accepted" : "rejected")
            << ",m4_" << (status.ok ? "accepted" : "rejected") << '\n';
}

}  // namespace

int main() {
  const fs::path root = fs::temp_directory_path() /
      ("m4-p22-native-array-u32-" + std::to_string(::getpid()));
  fs::remove_all(root);
  fs::create_directories(root);
  const std::string native_path = (root / "native").string();
  const std::string m4_path = (root / "m4").string();
  const uint32_t max = std::numeric_limits<uint32_t>::max();
  Rows rows;
  const std::vector<Row> initial = {
      {"root-empty", std::vector<uint32_t>{}},
      {"src-child", std::vector<uint32_t>{17u, 1u, 17u}},
      {"src-deep", std::vector<uint32_t>{17u, 18u, 18u}},
      {"other", std::vector<uint32_t>{42u, 0u}},
      {"boundary", std::vector<uint32_t>{max, 0u}},
      {"empty-other", std::vector<uint32_t>{}},
      {"missing", std::nullopt},
  };
  NativeCollection native{CreateNative(native_path)};
  if (!native.handle) return 2;
  m4::Options options;
  options.fsync_wal = false;
  std::unique_ptr<m4::Index> index;
  auto status = m4::Index::Open(m4_path, options, &index);
  Check(status.ok, "M4 open: " + status.message);
  if (!status.ok) return 2;

  for (const auto& row : initial) {
    NativeUpsert(native.handle, row, !row.values.has_value());
    m4::Document doc;
    if (row.values) {
      doc.array_uint32_values.push_back({"ancestor_directory_ids", *row.values});
    }
    rows[row.pk] = row.values;
    Check(index->Upsert(row.pk, doc).ok, "M4 initial upsert " + row.pk);
  }
  Compare(native.handle, *index, rows, "initial-before-flush");
  CheckCode(zvec_collection_flush(native.handle), "native initial flush");
  Check(index->Flush().ok, "M4 initial flush");
  Compare(native.handle, *index, rows, "after-flush");
  ReportComplementMaterialization(*index, rows);
  CheckOperandLimit(native.handle, *index);

  // WAL records contain normalized marker/element postings, so an unflushed
  // close/reopen must preserve empty arrays and typed values.
  index.reset();
  status = m4::Index::Open(m4_path, options, &index);
  Check(status.ok, "M4 WAL reopen: " + status.message);
  if (status.ok && index) Compare(native.handle, *index, rows, "after-WAL-replay");

  const Row updated{"src-deep", std::vector<uint32_t>{42u, max, 42u}};
  NativeUpsert(native.handle, updated);
  m4::Document updated_doc;
  updated_doc.array_uint32_values.push_back(
      {"ancestor_directory_ids", *updated.values});
  Check(index->Upsert(updated.pk, updated_doc).ok, "M4 array update");
  rows[updated.pk] = updated.values;
  Check(NativeDelete(native.handle, "other"), "native delete other");
  Check(index->Delete("other").ok, "M4 delete other");
  rows.erase("other");
  Compare(native.handle, *index, rows, "after-update-delete-before-flush");

  // Reinsert the deleted logical key; old postings remain tombstoned.
  const Row reinserted{"other", std::vector<uint32_t>{17u}};
  NativeUpsert(native.handle, reinserted);
  m4::Document reinserted_doc;
  reinserted_doc.array_uint32_values.push_back(
      {"ancestor_directory_ids", *reinserted.values});
  Check(index->Upsert(reinserted.pk, reinserted_doc).ok, "M4 array reinsert");
  rows[reinserted.pk] = reinserted.values;
  Compare(native.handle, *index, rows, "after-reinsert");

  CheckCode(zvec_collection_flush(native.handle), "native mutation flush");
  Check(index->Flush().ok, "M4 mutation flush");
  Compare(native.handle, *index, rows, "after-mutation-flush");
  CheckCode(zvec_collection_optimize(native.handle), "native optimize");
  Check(index->CompactNow().ok, "M4 compact");
  Compare(native.handle, *index, rows, "after-compact");

  CheckCode(zvec_collection_close(native.handle), "native close before reopen");
  native.handle = nullptr;
  CheckCode(zvec_collection_open(native_path.c_str(), nullptr, &native.handle),
            "native reopen");
  index.reset();
  status = m4::Index::Open(m4_path, options, &index);
  Check(status.ok, "M4 reopen: " + status.message);
  if (status.ok && native.handle) Compare(native.handle, *index, rows, "after-reopen");

  std::cout << "checks=" << checks << " failures=" << failures << '\n';
  index.reset();
  if (native.handle) {
    (void)zvec_collection_close(native.handle);
    native.handle = nullptr;
  }
  fs::remove_all(root);
  return failures == 0 ? 0 : 1;
}
