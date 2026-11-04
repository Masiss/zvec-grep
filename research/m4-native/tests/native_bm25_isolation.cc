// Research-only BM25 root-cause probe. Native expectations are derived from
// the checked-out indexer/reducer lifecycle, not from M4's statistics.
#include "m4/index.h"
#include "tokenizer.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <string>
#include <unistd.h>
#include <vector>

#include <zvec/c_api.h>

namespace fs = std::filesystem;
namespace {
constexpr float k1 = 1.2f;
constexpr float b = 0.75f;
constexpr float kTolerance = 2e-5f;
int checks = 0;
int failures = 0;

void Check(bool ok, const std::string& label) {
  ++checks;
  if (!ok) {
    ++failures;
    std::cerr << "FAIL: " << label << '\n';
  }
}

void CheckCode(zvec_error_code_t code, const std::string& label) {
  Check(code == ZVEC_OK, label + (code == ZVEC_OK ? "" : ": " +
      std::string(zvec_error_code_to_string(code))));
}

struct Doc {
  std::string pk;
  std::string text;
  bool live{true};
};

const std::vector<Doc> kInitial = {
    {"d1", "signal signal alpha"},
    {"d2", "signal beta"},
    {"d3", "gamma gamma gamma gamma"},
    {"d4", "delta epsilon"},
};
const Doc kUpdatedD2{"d2", "other other other"};
constexpr const char* kQuery = "signal";

std::vector<m4::Token> NativeTokens(const std::string& text) {
  return m4::Tokenize(text);  // optional adapter compiles native source directly
}

uint32_t TermFrequency(const Doc& doc, const std::string& term) {
  uint32_t tf = 0;
  for (const auto& token : NativeTokens(doc.text)) {
    if (token.term == term) ++tf;
  }
  return tf;
}
uint32_t DocumentLength(const Doc& doc) {
  return static_cast<uint32_t>(NativeTokens(doc.text).size());
}

struct Stats {
  uint64_t n{0};
  uint64_t total_tokens{0};
  uint64_t df{0};
  double avgdl{1.0};
};

Stats StatsFor(const std::vector<Doc>& docs, const std::string& term,
               bool include_dead) {
  Stats stats;
  for (const auto& doc : docs) {
    if (!include_dead && !doc.live) continue;
    ++stats.n;
    stats.total_tokens += DocumentLength(doc);
    if (TermFrequency(doc, term) != 0) ++stats.df;
  }
  stats.avgdl = stats.n == 0
      ? 1.0
      : static_cast<double>(stats.total_tokens) / stats.n;
  return stats;
}

struct Breakdown {
  double idf{0.0};
  double tf_norm{0.0};
  double contribution{0.0};
};

// Mirrors BM25Scorer::score in bm25_scorer.cc (float intermediates).
Breakdown ScoreBreakdown(const Stats& stats, uint32_t tf, uint32_t dl) {
  const float n = static_cast<float>(stats.n);
  const float df = static_cast<float>(stats.df);
  const float idf = stats.n == 0 ? 0.0f :
      std::log((n - df + 0.5f) / (df + 0.5f) + 1.0f);
  const float avgdl = static_cast<float>(stats.avgdl);
  const float tf_value = static_cast<float>(tf);
  const float dl_value = static_cast<float>(dl);
  const float norm = tf_value * (k1 + 1.0f) /
      (tf_value + k1 * (1.0f - b + b * dl_value / avgdl));
  return {idf, norm, idf * norm};
}

struct Hit { std::string pk; float score{0.0f}; };
struct NativeCollection {
  zvec_collection_t* handle{nullptr};
  ~NativeCollection() { if (handle) zvec_collection_close(handle); }
};

zvec_collection_t* CreateNative(const std::string& path) {
  auto* params = zvec_index_params_create(ZVEC_INDEX_TYPE_FTS);
  auto* filters = zvec_string_array_create(1);
  zvec_string_array_add(filters, 0, "lowercase");
  CheckCode(zvec_index_params_set_fts_params(params, "standard", filters, ""),
            "configure standard/lowercase analyzer");
  auto* field = zvec_field_schema_create("content", ZVEC_DATA_TYPE_STRING,
                                          false, 0);
  CheckCode(zvec_field_schema_set_index_params(field, params),
            "configure content FTS field");
  auto* schema = zvec_collection_schema_create("m4_p11_bm25");
  CheckCode(zvec_collection_schema_add_field(schema, field),
            "add content field");
  auto* options = zvec_collection_options_create();
  CheckCode(zvec_collection_options_set_enable_mmap(options, true),
            "enable mmap for isolated collection");
  zvec_collection_t* collection = nullptr;
  const auto code = zvec_collection_create_and_open(
      path.c_str(), schema, options, &collection);
  CheckCode(code, "create isolated native collection");
  zvec_collection_options_destroy(options);
  zvec_collection_schema_destroy(schema);
  zvec_field_schema_destroy(field);
  zvec_index_params_destroy(params);
  zvec_string_array_destroy(filters);
  return collection;
}

void NativeWrite(zvec_collection_t* collection, const std::vector<Doc>& docs,
                 bool upsert) {
  std::vector<zvec_doc_t*> owned;
  std::vector<const zvec_doc_t*> args;
  for (const auto& item : docs) {
    auto* doc = zvec_doc_create();
    zvec_doc_set_pk(doc, item.pk.c_str());
    CheckCode(zvec_doc_add_field_by_value(doc, "content", ZVEC_DATA_TYPE_STRING,
        item.text.data(), item.text.size()), "write native text " + item.pk);
    owned.push_back(doc);
    args.push_back(doc);
  }
  zvec_write_result_t* results = nullptr;
  size_t count = 0;
  const auto code = upsert
      ? zvec_collection_upsert_with_results(collection, args.data(), args.size(),
                                             &results, &count)
      : zvec_collection_insert_with_results(collection, args.data(), args.size(),
                                             &results, &count);
  CheckCode(code, upsert ? "native upsert" : "native insert");
  Check(count == docs.size(), "native write result count");
  for (size_t i = 0; i < count; ++i) {
    Check(results[i].code == ZVEC_OK, "native write item " +
          std::to_string(i));
  }
  zvec_write_results_free(results, count);
  for (auto* doc : owned) zvec_doc_destroy(doc);
}

std::vector<Hit> NativeSearch(zvec_collection_t* collection) {
  auto* query = zvec_vector_query_create();
  CheckCode(zvec_vector_query_set_field_name(query, "content"),
            "set native query field");
  CheckCode(zvec_vector_query_set_topk(query, 10), "set native top-k");
  auto* fts = zvec_fts_create();
  CheckCode(zvec_fts_set_query_string(fts, kQuery), "set native query string");
  CheckCode(zvec_vector_query_set_fts(query, fts), "attach native FTS clause");
  auto* query_params = zvec_query_params_fts_create("OR");
  CheckCode(zvec_vector_query_set_fts_params(query, query_params),
            "pin native default OR");
  zvec_doc_t** docs = nullptr;
  size_t count = 0;
  const auto code = zvec_collection_query(collection, query, &docs, &count);
  CheckCode(code, "native query signal");
  std::vector<Hit> hits;
  if (code == ZVEC_OK) {
    for (size_t i = 0; i < count; ++i) {
      const char* pk = zvec_doc_get_pk_pointer(docs[i]);
      hits.push_back({pk ? pk : "", zvec_doc_get_score(docs[i])});
    }
  }
  zvec_docs_free(docs, count);
  // zvec_vector_query_set_fts_params() takes ownership of query_params.
  zvec_fts_destroy(fts);
  zvec_vector_query_destroy(query);
  return hits;
}

std::vector<Hit> M4Search(const m4::Index& index) {
  m4::SearchResult result;
  const auto status = index.SearchFts(kQuery, 10, &result);
  Check(status.ok, "M4 query signal: " + status.message);
  std::vector<Hit> hits;
  for (const auto& hit : result.hits) hits.push_back({hit.pk, hit.score});
  return hits;
}

std::map<std::string, float> ByPk(const std::vector<Hit>& hits) {
  std::map<std::string, float> result;
  for (const auto& hit : hits) result[hit.pk] = hit.score;
  return result;
}

void PrintBreakdown(const char* engine, const char* stage,
                    const char* stats_scope, const Stats& stats,
                    const Doc& doc, float observed) {
  const uint32_t tf = TermFrequency(doc, kQuery);
  const uint32_t dl = DocumentLength(doc);
  const auto detail = ScoreBreakdown(stats, tf, dl);
  std::cout << std::setprecision(9)
            << "BM25 engine=" << engine << " stage=" << stage
            << " scope=" << stats_scope << " query=" << kQuery
            << " pk=" << doc.pk << " N=" << stats.n << " df=" << stats.df
            << " tf=" << tf << " dl=" << dl
            << " total_tokens=" << stats.total_tokens
            << " avgdl=" << stats.avgdl << " k1=" << k1 << " b=" << b
            << " idf=" << detail.idf << " tf_norm=" << detail.tf_norm
            << " term_contribution=" << detail.contribution
            << " final=" << detail.contribution << " observed=" << observed
            << " delta=" << (observed - detail.contribution) << '\n';
}

const Doc* FindLive(const std::vector<Doc>& docs, const std::string& pk) {
  for (auto it = docs.rbegin(); it != docs.rend(); ++it) {
    if (it->pk == pk && it->live) return &*it;
  }
  return nullptr;
}

void CompareStage(const char* stage, zvec_collection_t* native,
                  const m4::Index& m4_index,
                  const std::vector<Doc>& native_physical,
                  const std::vector<Doc>& m4_live,
                  bool expect_parity, bool expect_native_mismatch) {
  const auto native_hits = NativeSearch(native);
  const auto m4_hits = M4Search(m4_index);
  const auto native_by_pk = ByPk(native_hits);
  const auto m4_by_pk = ByPk(m4_hits);
  Check(native_by_pk.size() == m4_by_pk.size(),
        std::string(stage) + " exact hit count");
  for (const auto& [pk, score] : native_by_pk) {
    Check(m4_by_pk.count(pk) != 0, std::string(stage) + " hit PK " + pk);
    const Doc* source_doc = FindLive(native_physical, pk);
    Check(source_doc != nullptr, std::string(stage) + " native source PK " + pk);
    if (!source_doc) continue;

    // Native per-segment model: inserts increment its segment counters and
    // postings; delete filters candidates but does not rewrite term bitmaps.
    const Stats native_stats = StatsFor(native_physical, kQuery, true);
    PrintBreakdown("native-source-model", stage, "single-segment", native_stats,
                   *source_doc, score);
    const auto native_formula = ScoreBreakdown(
        native_stats, TermFrequency(*source_doc, kQuery),
        DocumentLength(*source_doc));
    Check(std::abs(score - native_formula.contribution) <= kTolerance,
          std::string(stage) + " native API vs source-derived BM25 " + pk);

    const auto index_stats = m4_index.GetStats();
    const Stats m4_stats = StatsFor(m4_live, kQuery, false);
    Check(index_stats.live_docs == m4_stats.n,
          std::string(stage) + " M4 live-doc accounting");
    Check(index_stats.live_tokens == m4_stats.total_tokens,
          std::string(stage) + " M4 live-token accounting");
    const Doc* m4_doc = FindLive(m4_live, pk);
    Check(m4_doc != nullptr, std::string(stage) + " M4 source PK " + pk);
    if (!m4_doc) continue;
    const auto m4_it = m4_by_pk.find(pk);
    if (m4_it == m4_by_pk.end()) continue;
    PrintBreakdown("M4-live-model", stage, "live-corpus", m4_stats,
                   *m4_doc, m4_it->second);
    const auto m4_formula = ScoreBreakdown(
        m4_stats, TermFrequency(*m4_doc, kQuery), DocumentLength(*m4_doc));
    Check(std::abs(m4_it->second - m4_formula.contribution) <= kTolerance,
          std::string(stage) + " M4 API vs live BM25 " + pk);
  }

  if (expect_parity) {
    for (const auto& [pk, native_score] : native_by_pk) {
      const auto it = m4_by_pk.find(pk);
      Check(it != m4_by_pk.end() &&
                std::abs(native_score - it->second) <= kTolerance,
            std::string(stage) + " native/M4 score parity " + pk);
    }
  }
  if (expect_native_mismatch) {
    bool saw_mismatch = false;
    for (const auto& [pk, native_score] : native_by_pk) {
      const auto it = m4_by_pk.find(pk);
      if (it != m4_by_pk.end() &&
          std::abs(native_score - it->second) > kTolerance) {
        saw_mismatch = true;
        std::cout << "MISMATCH category=statistics-lifecycle stage=" << stage
                  << " query=" << kQuery << " pk=" << pk
                  << " native=" << native_score << " M4=" << it->second
                  << '\n';
      }
    }
    Check(saw_mismatch, std::string(stage) + " expected lifecycle mismatch");
  }
}

}  // namespace

int main() {
  const fs::path root = fs::temp_directory_path() /
      ("m4-p11-bm25-" + std::to_string(::getpid()));
  fs::create_directories(root);
  const std::string native_path = (root / "native").string();
  const std::string m4_path = (root / "m4").string();

  // Assert the exact tokenization/length fixture before using it as the source
  // of independently derived native and M4 statistics.
  for (const auto& doc : kInitial) {
    Check(DocumentLength(doc) == (doc.pk == "d1" ? 3U : doc.pk == "d2" ? 2U :
          doc.pk == "d3" ? 4U : 2U), "native-tokenizer fixture length " + doc.pk);
  }
  Check(DocumentLength(kUpdatedD2) == 3,
        "native-tokenizer updated document length");

  std::cout << "config=standard filters=[lowercase] extra_params={}"
            << " query_default=OR k1=1.2 b=0.75 tolerance=2e-5\n"
            << "native_model=one_segment insert_counters_and_posting_bitmap_"
               "cardinality; deletes_filter_hits_until_reducer_compaction\n";

  NativeCollection native{CreateNative(native_path)};
  if (!native.handle) return 2;
  m4::Options options;
  options.fts_field = "content";
  options.bm25_k1 = k1;
  options.bm25_b = b;
  std::unique_ptr<m4::Index> m4_index;
  auto status = m4::Index::Open(m4_path, options, &m4_index);
  Check(status.ok, "M4 open: " + status.message);
  if (!status.ok) return 2;

  std::vector<Doc> native_physical = kInitial;
  std::vector<Doc> m4_live = kInitial;
  NativeWrite(native.handle, kInitial, false);
  for (const auto& doc : kInitial) {
    status = m4_index->Upsert(doc.pk, m4::Document{doc.text, {}});
    Check(status.ok, "M4 initial upsert " + doc.pk + ": " + status.message);
  }
  CheckCode(zvec_collection_flush(native.handle), "native initial flush");
  status = m4_index->Flush();
  Check(status.ok, "M4 initial flush: " + status.message);
  CompareStage("initial", native.handle, *m4_index, native_physical, m4_live,
               true, false);

  NativeWrite(native.handle, {kUpdatedD2}, true);
  for (auto& doc : native_physical) {
    if (doc.pk == "d2" && doc.live) doc.live = false;
  }
  native_physical.push_back(kUpdatedD2);
  for (auto& doc : m4_live) {
    if (doc.pk == "d2") doc.live = false;
  }
  m4_live.push_back(kUpdatedD2);
  status = m4_index->Upsert(kUpdatedD2.pk, m4::Document{kUpdatedD2.text, {}});
  Check(status.ok, "M4 update d2: " + status.message);
  CheckCode(zvec_collection_flush(native.handle), "native flush after update");
  status = m4_index->Flush();
  Check(status.ok, "M4 flush after update: " + status.message);
  CompareStage("after-update", native.handle, *m4_index, native_physical,
               m4_live, false, true);

  const char* delete_pk[] = {"d3"};
  zvec_write_result_t* delete_results = nullptr;
  size_t delete_count = 0;
  const auto delete_code = zvec_collection_delete_with_results(
      native.handle, delete_pk, 1, &delete_results, &delete_count);
  CheckCode(delete_code, "native delete d3");
  Check(delete_count == 1 && delete_results != nullptr &&
            delete_results[0].code == ZVEC_OK,
        "native delete d3 result");
  zvec_write_results_free(delete_results, delete_count);
  for (auto& doc : native_physical) {
    if (doc.pk == "d3" && doc.live) doc.live = false;
  }
  for (auto& doc : m4_live) {
    if (doc.pk == "d3") doc.live = false;
  }
  status = m4_index->Delete("d3");
  Check(status.ok, "M4 delete d3: " + status.message);
  if (std::getenv("M4_BM25_SKIP_FLUSH_BEFORE_OPTIMIZE") == nullptr) {
    CheckCode(zvec_collection_flush(native.handle), "native flush after delete");
  }
  status = m4_index->Flush();
  Check(status.ok, "M4 flush after delete: " + status.message);
  CompareStage("after-delete", native.handle, *m4_index, native_physical,
               m4_live, false, true);

  CheckCode(zvec_collection_optimize(native.handle), "native optimize/purge");
  status = m4_index->CompactNow();
  Check(status.ok, "M4 CompactNow: " + status.message);
  native_physical.erase(std::remove_if(native_physical.begin(),
      native_physical.end(), [](const Doc& doc) { return !doc.live; }),
      native_physical.end());
  CompareStage("after-optimize", native.handle, *m4_index, native_physical,
               m4_live, true, false);

  CheckCode(zvec_collection_close(native.handle), "native close before reopen");
  native.handle = nullptr;
  CheckCode(zvec_collection_open(native_path.c_str(), nullptr, &native.handle),
            "native reopen");
  m4_index.reset();
  status = m4::Index::Open(m4_path, options, &m4_index);
  Check(status.ok, "M4 reopen: " + status.message);
  if (status.ok) {
    CompareStage("after-reopen", native.handle, *m4_index, native_physical,
                 m4_live, true, false);
  }

  std::cout << "checks=" << checks << " failures=" << failures << '\n';
  if (native.handle) {
    (void)zvec_collection_close(native.handle);
    native.handle = nullptr;
  }
  m4_index.reset();
  fs::remove_all(root);
  return failures == 0 ? 0 : 1;
}
