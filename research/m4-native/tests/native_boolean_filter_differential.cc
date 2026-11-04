// Usage-backed Boolean filter differential for the M4 research prototype.
// Native and M4 results are compared by logical PK, never by DocId.
#include <cstdint>
#include <filesystem>
#include <iostream>
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
  auto* schema = zvec_collection_schema_create("m4_p23_boolean_filters");
  const struct FieldSpec {
    const char* name;
    zvec_data_type_t type;
    bool range_optimization;
    bool extended_wildcard;
  } fields[] = {
      {"ancestor_directory_ids", ZVEC_DATA_TYPE_ARRAY_UINT32, true, false},
      {"file_id", ZVEC_DATA_TYPE_UINT32, true, false},
      {"status", ZVEC_DATA_TYPE_STRING, true, false},
      {"file_name", ZVEC_DATA_TYPE_STRING, false, true},
  };
  for (const auto& spec : fields) {
    auto* invert = zvec_index_params_create(ZVEC_INDEX_TYPE_INVERT);
    auto* field = zvec_field_schema_create(spec.name, spec.type, true, 0);
    CheckCode(zvec_index_params_set_invert_params(
                  invert, spec.range_optimization, spec.extended_wildcard),
              std::string("configure ") + spec.name + " invert");
    CheckCode(zvec_field_schema_set_index_params(field, invert),
              std::string("set ") + spec.name + " invert");
    CheckCode(zvec_collection_schema_add_field(schema, field),
              std::string("add ") + spec.name);
    zvec_field_schema_destroy(field);
    zvec_index_params_destroy(invert);
  }
  auto* text_params = zvec_index_params_create(ZVEC_INDEX_TYPE_FTS);
  auto* filters = zvec_string_array_create(1);
  zvec_string_array_add(filters, 0, "lowercase");
  CheckCode(zvec_index_params_set_fts_params(text_params, "standard", filters,
                                             ""),
            "configure content FTS");
  auto* content = zvec_field_schema_create("content", ZVEC_DATA_TYPE_STRING,
                                            false, 0);
  CheckCode(zvec_field_schema_set_index_params(content, text_params),
            "set content FTS");
  CheckCode(zvec_collection_schema_add_field(schema, content),
            "add content field");
  zvec_field_schema_destroy(content);
  zvec_index_params_destroy(text_params);
  zvec_string_array_destroy(filters);

  zvec_collection_t* collection = nullptr;
  const auto code = zvec_collection_create_and_open(
      path.c_str(), schema, nullptr, &collection);
  CheckCode(code, "create native Boolean collection");
  zvec_collection_schema_destroy(schema);
  return collection;
}

struct Row {
  std::string pk;
  std::optional<uint32_t> file_id;
  std::optional<std::vector<uint32_t>> directories;
  std::optional<std::string> status;
  std::string text;
  std::optional<std::string> file_name{};
};
using Rows = std::map<std::string, Row>;

bool NativeUpsert(zvec_collection_t* collection, const Row& row) {
  auto* doc = zvec_doc_create();
  zvec_doc_set_pk(doc, row.pk.c_str());
  if (row.file_id) {
    CheckCode(zvec_doc_add_field_by_value(doc, "file_id", ZVEC_DATA_TYPE_UINT32,
              &*row.file_id, sizeof(*row.file_id)), "set file_id " + row.pk);
  }
  if (row.directories) {
    uint32_t empty_sentinel = 0;
    const void* data = row.directories->empty()
        ? static_cast<const void*>(&empty_sentinel)
        : static_cast<const void*>(row.directories->data());
    CheckCode(zvec_doc_add_field_by_value(doc, "ancestor_directory_ids",
              ZVEC_DATA_TYPE_ARRAY_UINT32, data,
              row.directories->size() * sizeof(uint32_t)),
              "set directories " + row.pk);
  }
  if (row.status) {
    CheckCode(zvec_doc_add_field_by_value(doc, "status", ZVEC_DATA_TYPE_STRING,
              row.status->data(), row.status->size()), "set status " + row.pk);
  }
  if (row.file_name) {
    CheckCode(zvec_doc_add_field_by_value(doc, "file_name", ZVEC_DATA_TYPE_STRING,
              row.file_name->data(), row.file_name->size()),
              "set file_name " + row.pk);
  }
  CheckCode(zvec_doc_add_field_by_value(doc, "content", ZVEC_DATA_TYPE_STRING,
            row.text.data(), row.text.size()), "set content " + row.pk);
  const zvec_doc_t* docs[] = {doc};
  zvec_write_result_t* results = nullptr;
  size_t count = 0;
  const auto code = zvec_collection_upsert_with_results(
      collection, docs, 1, &results, &count);
  const bool ok = code == ZVEC_OK && count == 1 && results &&
                  results[0].code == ZVEC_OK;
  Check(ok, "native upsert " + row.pk + (ok ? "" : ": " +
        std::string(zvec_error_code_to_string(code))));
  zvec_write_results_free(results, count);
  zvec_doc_destroy(doc);
  return ok;
}

bool NativeDelete(zvec_collection_t* collection, const std::string& pk) {
  const char* pks[] = {pk.c_str()};
  zvec_write_result_t* results = nullptr;
  size_t count = 0;
  const auto code = zvec_collection_delete_with_results(
      collection, pks, 1, &results, &count);
  const bool ok = code == ZVEC_OK && count == 1 && results &&
                  results[0].code == ZVEC_OK;
  Check(ok, "native delete " + pk);
  zvec_write_results_free(results, count);
  return ok;
}

std::string QuoteSqlString(const std::string& value) {
  std::string quoted;
  for (char ch : value) {
    if (ch == '\'') quoted.push_back('\\');
    quoted.push_back(ch);
  }
  return quoted;
}

std::string EscapeLikeLiteral(const std::string& value) {
  std::string escaped;
  for (char ch : value) {
    if (ch == '\\' || ch == '%' || ch == '_') escaped.push_back('\\');
    escaped.push_back(ch);
  }
  return escaped;
}

std::string BuildFilter(const m4::FilterExpr& expr, bool negated = false) {
  using m4::FilterKind;
  if (expr.op == FilterKind::NOT) {
    return BuildFilter(expr.children.at(0), !negated);
  }
  if (expr.op == FilterKind::ALL || expr.op == FilterKind::NONE) {
    const bool all = (expr.op == FilterKind::ALL) != negated;
    return all ? "(file_id IS NULL OR file_id IS NOT NULL)"
               : "(file_id IS NULL AND file_id IS NOT NULL)";
  }
  if (expr.op == FilterKind::AND || expr.op == FilterKind::OR) {
    const bool is_and = (expr.op == FilterKind::AND) != negated;
    if (expr.children.empty()) {
      return is_and ? "(file_id IS NULL OR file_id IS NOT NULL)"
                    : "(file_id IS NULL AND file_id IS NOT NULL)";
    }
    std::string result = "(";
    for (size_t i = 0; i < expr.children.size(); ++i) {
      if (i) result += is_and ? " AND " : " OR ";
      result += BuildFilter(expr.children[i], negated);
    }
    return result + ")";
  }
  if (expr.op == FilterKind::ARRAY_UINT32_CONTAINS_ANY ||
      expr.op == FilterKind::ARRAY_UINT32_NOT_CONTAINS_ANY) {
    if (negated && expr.uint32_values.empty()) {
      if (expr.op == FilterKind::ARRAY_UINT32_CONTAINS_ANY) {
        return "(file_id IS NULL OR file_id IS NOT NULL)";
      }
      return "ancestor_directory_ids IS NULL";
    }
    const bool negative = (expr.op == FilterKind::ARRAY_UINT32_NOT_CONTAINS_ANY)
                          != negated;
    std::string result = "ancestor_directory_ids ";
    if (negative) result += "NOT ";
    result += "CONTAIN_ANY (";
    for (size_t i = 0; i < expr.uint32_values.size(); ++i) {
      if (i) result += ", ";
      result += std::to_string(expr.uint32_values[i]);
    }
    return result + ")";
  }
  if (expr.op == FilterKind::STRING_PREFIX ||
      expr.op == FilterKind::STRING_SUFFIX) {
    std::string pattern = EscapeLikeLiteral(expr.string_value);
    if (expr.op == FilterKind::STRING_PREFIX) pattern.push_back('%');
    else pattern.insert(pattern.begin(), '%');
    return "file_name " + std::string(negated ? "NOT LIKE '" : "LIKE '") +
           QuoteSqlString(pattern) + "'";
  }
  if (expr.op == FilterKind::STRING_EQ) {
    const std::string value = expr.string_value;
    std::string escaped;
    for (char ch : value) {
      if (ch == '\\' || ch == '\'') escaped.push_back('\\');
      escaped.push_back(ch);
    }
    return expr.field + " " + std::string(negated ? "!= " : "= ") + "'" +
           escaped + "'";
  }
  if (expr.op == FilterKind::UINT32_EQ) {
    return "file_id " + std::string(negated ? "!= " : "= ") +
           std::to_string(expr.uint32_value);
  }
  if (expr.op == FilterKind::UINT32_IN) {
    std::string result = "file_id " + std::string(negated ? "NOT IN (" : "IN (");
    for (size_t i = 0; i < expr.uint32_values.size(); ++i) {
      if (i) result += ", ";
      result += std::to_string(expr.uint32_values[i]);
    }
    return result + ")";
  }
  return "(file_id IS NULL AND file_id IS NOT NULL)";
}

std::set<std::string> NativeQuery(zvec_collection_t* collection,
                                  const std::string& filter,
                                  zvec_error_code_t* code_out = nullptr) {
  auto* query = zvec_vector_query_create();
  CheckCode(zvec_vector_query_set_topk(query, 1000), "native top-k");
  CheckCode(zvec_vector_query_set_filter(query, filter.c_str()),
            "native filter setter " + filter);
  zvec_doc_t** docs = nullptr;
  size_t count = 0;
  const auto code = zvec_collection_query(collection, query, &docs, &count);
  if (code_out) *code_out = code;
  std::set<std::string> pks;
  if (code == ZVEC_OK) {
    for (size_t i = 0; i < count; ++i) {
      const char* pk = zvec_doc_get_pk_pointer(docs[i]);
      pks.insert(pk ? pk : "");
    }
  }
  zvec_docs_free(docs, count);
  zvec_vector_query_destroy(query);
  return pks;
}

std::set<std::string> NativeFtsQuery(zvec_collection_t* collection,
                                    const std::string& filter, int top_k) {
  auto* query = zvec_vector_query_create();
  CheckCode(zvec_vector_query_set_topk(query, top_k), "native FTS top-k");
  CheckCode(zvec_vector_query_set_filter(query, filter.c_str()),
            "native FTS filter");
  CheckCode(zvec_vector_query_set_field_name(query, "content"),
            "native FTS field");
  auto* fts = zvec_fts_create();
  CheckCode(zvec_fts_set_query_string(fts, "needle"), "native FTS query text");
  CheckCode(zvec_vector_query_set_fts(query, fts), "attach native FTS query");
  auto* params = zvec_query_params_fts_create("OR");
  const auto params_code = zvec_vector_query_set_fts_params(query, params);
  CheckCode(params_code, "attach native FTS params");
  if (params_code == ZVEC_OK) params = nullptr;
  zvec_doc_t** docs = nullptr;
  size_t count = 0;
  const auto code = zvec_collection_query(collection, query, &docs, &count);
  CheckCode(code, "native filtered FTS query");
  std::set<std::string> pks;
  if (code == ZVEC_OK) {
    for (size_t i = 0; i < count; ++i) {
      const char* pk = zvec_doc_get_pk_pointer(docs[i]);
      pks.insert(pk ? pk : "");
    }
  }
  zvec_docs_free(docs, count);
  zvec_vector_query_destroy(query);
  if (params) zvec_query_params_fts_destroy(params);
  if (fts) zvec_fts_destroy(fts);
  return pks;
}

std::set<std::string> M4Query(m4::Index& index,
                              const m4::FilterExpr& expr,
                              bool* accepted = nullptr) {
  m4::SearchResult result;
  const auto status = index.SearchFilter(expr, &result);
  if (accepted) *accepted = status.ok;
  Check(status.ok, "M4 Boolean query: " + status.message);
  std::set<std::string> pks;
  for (const auto& hit : result.hits) pks.insert(hit.pk);
  Check(pks.size() == result.hits.size(), "M4 Boolean query unique PKs");
  return pks;
}

void Compare(zvec_collection_t* native, m4::Index& index,
             const std::string& phase) {
  using F = m4::FilterExpr;
  const F array = F::ArrayContainsAny("ancestor_directory_ids", {17u});
  const F uid1 = F::Uint32Eq("file_id", 1u);
  const F status = F::StringEq("status", "active");
  const std::vector<std::pair<std::string, F>> cases = {
      {"filename prefix", F::StringPrefix("file_name", "main")},
      {"filename case variant", F::StringPrefix("file_name", "Main")},
      {"filename suffix", F::StringSuffix("file_name", ".rs")},
      {"filename suffix case variant", F::StringSuffix("file_name", ".RS")},
      {"filename literal underscore", F::StringPrefix("file_name", "under_")},
      {"filename literal percent", F::StringSuffix("file_name", "%.rs")},
      {"filename escaped backslash", F::StringPrefix("file_name", "back\\")},
      {"filename unicode prefix", F::StringPrefix("file_name", "果园")},
      {"filename unicode suffix", F::StringSuffix("file_name", "园.rs")},
      {"filename quote escape", F::StringPrefix("file_name", "quote'")},
      {"filename empty prefix", F::StringPrefix("file_name", "")},
      {"filename empty suffix", F::StringSuffix("file_name", "")},
      {"filename exact empty", F::StringEq("file_name", "")},
      {"filename not exact empty", F::Not(F::StringEq("file_name", ""))},
      {"filename Boolean composition", F::And({
          F::StringPrefix("file_name", "main"),
          F::StringSuffix("file_name", ".rs")})},
      {"overlapping prefix/suffix composition", F::And({
          F::StringPrefix("file_name", "main"),
          F::StringSuffix("file_name", "main")})},
      {"nested AND OR", F::And({array, F::Or({uid1, F::Not(status)})})},
      {"NOT AND", F::Not(F::And({array, uid1}))},
      {"NOT OR", F::Not(F::Or({array, status}))},
      {"double NOT", F::Not(F::Not(F::Or({uid1, status})))},
      {"Uint32 IN with array NOT", F::And({
          F::Uint32In("file_id", {1u, 2u}),
          F::ArrayNotContainsAny("ancestor_directory_ids", {17u})})},
      {"empty Uint32 IN", F::Uint32In("file_id", {})},
      {"generic NOT array", F::Not(array)},
      {"direct NOT CONTAIN_ANY", F::ArrayNotContainsAny(
          "ancestor_directory_ids", {17u})},
      {"empty CONTAIN_ANY", F::ArrayContainsAny(
          "ancestor_directory_ids", {})},
      {"generic NOT empty CONTAIN_ANY", F::Not(
          F::ArrayContainsAny("ancestor_directory_ids", {}))},
      {"direct NOT CONTAIN_ANY empty", F::ArrayNotContainsAny(
          "ancestor_directory_ids", {})},
      {"generic NOT of empty NOT CONTAIN_ANY", F::Not(
          F::ArrayNotContainsAny("ancestor_directory_ids", {}))},
      {"empty AND", F::And({})},
      {"empty OR", F::Or({})},
      {"NOT empty AND", F::Not(F::And({}))},
  };
  for (const auto& [label, expr] : cases) {
    if (label == "empty Uint32 IN" && phase != "initial") continue;
    const std::string filter = BuildFilter(expr);
    zvec_error_code_t native_code = ZVEC_OK;
    const auto native_pks = NativeQuery(native, filter, &native_code);
    const auto m4_pks = M4Query(index, expr);
    const bool native_ok = native_code == ZVEC_OK;
    const bool expected_native_ok = label != "empty Uint32 IN";
    Check(native_ok == expected_native_ok,
          phase + " " + label + " native support state");
    if (native_ok) {
      Check(native_pks == m4_pks,
            phase + " " + label + " native/M4 logical-PK parity");
    } else if (label == "empty Uint32 IN") {
      Check(m4_pks.empty(), phase + " empty Uint32 IN M4 result remains empty");
    }
  }
  if (phase == "initial") {
    const auto not_like = F::Not(F::StringPrefix("file_name", "main"));
    zvec_error_code_t native_not_like_code = ZVEC_OK;
    (void)NativeQuery(native, BuildFilter(not_like), &native_not_like_code);
    m4::SearchResult rejected;
    const auto m4_not_like = index.SearchFilter(not_like, &rejected);
    Check(native_not_like_code == ZVEC_ERROR_INVALID_ARGUMENT &&
              !m4_not_like.ok,
          "NOT LIKE returns INVALID_ARGUMENT natively and is rejected by M4");
  }
}

void PutBoth(zvec_collection_t* native, m4::Index& index,
             Rows* rows, Row row) {
  NativeUpsert(native, row);
  m4::Document doc;
  doc.text = row.text;
  if (row.file_id) {
    doc.values.push_back(m4::ScalarValue::Unsigned32("file_id", *row.file_id));
  }
  if (row.status) doc.values.push_back({"status", *row.status});
  if (row.file_name) doc.values.push_back({"file_name", *row.file_name});
  if (row.directories) {
    doc.array_uint32_values.push_back(
        {"ancestor_directory_ids", *row.directories});
  }
  Check(index.Upsert(row.pk, doc).ok, "M4 upsert " + row.pk);
  (*rows)[row.pk] = std::move(row);
}
}  // namespace

int main() {
  const fs::path root = fs::temp_directory_path() /
      ("m4-p23-native-boolean-" + std::to_string(::getpid()));
  fs::remove_all(root);
  fs::create_directories(root);
  const auto native_path = (root / "native").string();
  const auto m4_path = (root / "m4").string();
  NativeCollection native{CreateNative(native_path)};
  if (!native.handle) return 2;
  m4::Options options;
  options.fsync_wal = false;
  std::unique_ptr<m4::Index> index;
  const auto open_status = m4::Index::Open(m4_path, options, &index);
  Check(open_status.ok, "M4 Boolean collection open: " + open_status.message);
  if (!open_status.ok) return 2;

  Rows rows;
  const std::vector<Row> initial = {
      {"array-hit", 1u, std::vector<uint32_t>{17u}, "active", "needle", "main.rs"},
      {"bare-main", 1u, std::vector<uint32_t>{}, "active", "needle", "main"},
      {"empty-array", 1u, std::vector<uint32_t>{}, "inactive", "needle", "Main.rs"},
      {"missing-array", 2u, std::nullopt, "active", "needle", "main.RS"},
      {"other-array", 2u, std::vector<uint32_t>{42u}, std::nullopt, "needle", "under_.rs"},
      {"missing-scalars", std::nullopt, std::nullopt, std::nullopt, "needle"},
      {"high-excluded", 2u, std::vector<uint32_t>{42u}, "inactive",
       "needle needle needle needle", "literal%.rs"},
      {"literal-percent-decoy", 3u, std::vector<uint32_t>{}, "active", "needle", "literalX.rs"},
      {"literal-underscore-decoy", 3u, std::vector<uint32_t>{}, "active", "needle", "underX.rs"},
      {"unicode-rs", 4u, std::vector<uint32_t>{}, "active", "needle", "果园.rs"},
      {"unicode-txt", 4u, std::vector<uint32_t>{}, "active", "needle", "果园.txt"},
      {"empty-filename", 5u, std::vector<uint32_t>{}, "active", "needle", ""},
      {"backslash-name", 5u, std::vector<uint32_t>{}, "active", "needle", "back\\.rs"},
      {"quote-name", 5u, std::vector<uint32_t>{}, "active", "needle", "quote'.rs"},
  };
  for (const auto& row : initial) PutBoth(native.handle, *index, &rows, row);
  Compare(native.handle, *index, "initial");
  const auto native_filtered_top1 =
      NativeFtsQuery(native.handle, "file_id = 1", 1);
  m4::SearchResult m4_filtered_top1, m4_unfiltered_top1;
  Check(index->SearchFts("needle", 1, &m4_unfiltered_top1).ok,
        "M4 unfiltered top-1");
  Check(index->SearchFtsFiltered("needle", 1,
        m4::FilterExpr::Uint32Eq("file_id", 1u), &m4_filtered_top1).ok,
        "M4 pre-top-k filter");
  Check(native_filtered_top1.size() == 1 &&
            rows.at(*native_filtered_top1.begin()).file_id == 1u,
        "native filter applied before top-k");
  Check(!m4_unfiltered_top1.hits.empty() &&
            m4_unfiltered_top1.hits.front().pk == "high-excluded",
        "M4 unfiltered top-1 is the excluded document");
  const auto native_filename_top1 =
      NativeFtsQuery(native.handle, "file_name LIKE 'main%'", 1);
  m4::QueryDiagnostics filename_diagnostics;
  m4::SearchResult m4_filename_top1;
  Check(index->SearchFtsFiltered("needle", 1,
            m4::FilterExpr::StringPrefix("file_name", "main"),
            &m4_filename_top1, &filename_diagnostics).ok,
        "M4 filename filter before FTS top-k");
  Check(native_filename_top1.size() == 1 &&
            rows.at(*native_filename_top1.begin()).file_name->rfind("main", 0) == 0,
        "native filename predicate is applied before FTS top-k");
  Check(m4_filename_top1.hits.size() == 1 &&
            rows.at(m4_filename_top1.hits.front().pk).file_name->rfind("main", 0) == 0,
        "M4 filename predicate admits a live matching FTS candidate");
  Check(filename_diagnostics.filter_like_strings_scanned > 0 &&
            filename_diagnostics.filter_candidates == 3 &&
            filename_diagnostics.filter_like_candidate_capacity_bytes >=
                3 * sizeof(m4::DocId) &&
            filename_diagnostics.filter_docid_temp_payload_upper_bound_bytes >=
                6 * sizeof(m4::DocId) * index->GetStats().live_docs,
        "M4 LIKE scan telemetry counts dictionary strings and candidate capacity");
  Check(m4_filtered_top1.hits.size() == 1 &&
            rows.at(m4_filtered_top1.hits.front().pk).file_id == 1u,
        "M4 filtered top-1 refills eligible candidates");
  m4::QueryDiagnostics not_diagnostics;
  m4::SearchResult not_hits;
  const auto not_array = m4::FilterExpr::Not(
      m4::FilterExpr::ArrayContainsAny("ancestor_directory_ids", {17u}));
  Check(index->SearchFtsFiltered("needle", 1, not_array, &not_hits,
                                  &not_diagnostics).ok,
        "M4 NOT candidate materialization");
  Check(not_diagnostics.filter_candidates + 3 == index->GetStats().live_docs &&
            not_diagnostics.filter_candidate_bytes >=
                not_diagnostics.filter_candidates * sizeof(m4::DocId),
        "M4 NOT reports final candidate payload lower bound");
  std::cout << "filename_filter_candidates="
            << filename_diagnostics.filter_candidates
            << " strings_scanned="
            << filename_diagnostics.filter_like_strings_scanned
            << " like_candidate_capacity_bytes="
            << filename_diagnostics.filter_like_candidate_capacity_bytes
            << " docid_temp_payload_upper_bound_bytes="
            << filename_diagnostics.filter_docid_temp_payload_upper_bound_bytes << '\n';
  std::cout << "not_live_docs=" << index->GetStats().live_docs
            << " not_candidates=" << not_diagnostics.filter_candidates
            << " final_candidate_docid_capacity_bytes="
            << not_diagnostics.filter_candidate_bytes << '\n';

  CheckCode(zvec_collection_flush(native.handle), "native Boolean flush");
  Check(index->Flush().ok, "M4 Boolean flush");
  Compare(native.handle, *index, "flush");

  PutBoth(native.handle, *index, &rows,
          {"array-hit", 2u, std::vector<uint32_t>{17u, 17u}, "active", "needle",
           "main.changed.rs"});
  Check(NativeDelete(native.handle, "other-array"), "native delete other-array");
  Check(index->Delete("other-array").ok, "M4 delete other-array");
  rows.erase("other-array");
  Compare(native.handle, *index, "update-delete");

  PutBoth(native.handle, *index, &rows,
          {"other-array", 1u, std::vector<uint32_t>{17u}, "inactive", "needle",
           "under_.rs"});
  Compare(native.handle, *index, "reinsert");
  CheckCode(zvec_collection_flush(native.handle), "native mutation flush");
  Check(index->Flush().ok, "M4 mutation flush");
  Compare(native.handle, *index, "mutation-flush");
  CheckCode(zvec_collection_optimize(native.handle), "native optimize");
  Check(index->CompactNow().ok, "M4 Boolean compact");
  Compare(native.handle, *index, "compact");

  // Keep one update only in the WAL on both engines, then compare recovery.
  PutBoth(native.handle, *index, &rows,
          {"wal-replay", 3u, std::vector<uint32_t>{17u}, "active", "needle",
           "果园.wal.rs"});
  CheckCode(zvec_collection_close(native.handle), "native close before reopen");
  native.handle = nullptr;
  CheckCode(zvec_collection_open(native_path.c_str(), nullptr, &native.handle),
            "native reopen");
  index.reset();
  const auto reopen_status = m4::Index::Open(m4_path, options, &index);
  Check(reopen_status.ok, "M4 Boolean reopen: " + reopen_status.message);
  if (reopen_status.ok && native.handle) Compare(native.handle, *index, "reopen");

  // Native parser/engine evidence: NOT CONTAIN ANY operates on non-null
  // arrays, including empty arrays; missing nullable arrays are excluded.
  const auto native_not_any = NativeQuery(native.handle,
      "ancestor_directory_ids NOT CONTAIN_ANY (17)");
  Check(native_not_any.count("empty-array") == 1 &&
            native_not_any.count("missing-array") == 0 &&
            native_not_any.count("missing-scalars") == 0,
        "native NOT CONTAIN_ANY uses the non-null field universe");

  std::cout << "checks=" << checks << " failures=" << failures << '\n';
  index.reset();
  if (native.handle) {
    (void)zvec_collection_close(native.handle);
    native.handle = nullptr;
  }
  fs::remove_all(root);
  return failures == 0 ? 0 : 1;
}