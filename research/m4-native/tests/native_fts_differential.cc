// Correctness-first, research-only differential harness. Native access uses
// the existing C API; M4 has an independent directory and index format.

#include <algorithm>
#include <cmath>
#include <cctype>
#include <cstdio>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <vector>

#include "m4/index.h"
#include "tokenizer.h"
#include "db/index/column/fts_column/tokenizer/standard_tokenizer.h"
#include "db/index/column/fts_column/tokenizer/token_filter.h"
#include <zvec/c_api.h>

namespace fs = std::filesystem;
namespace {
constexpr double kScoreAbsTolerance = 1e-4;
constexpr double kScoreRelTolerance = 1e-4;
int failures = 0;
int checks = 0;

void Check(bool ok, const std::string& label) {
  ++checks;
  if (!ok) {
    ++failures;
    std::cerr << "FAIL: " << label << '\n';
  }
}
void CheckCode(zvec_error_code_t code, const std::string& label) {
  Check(code == ZVEC_OK, label + (code == ZVEC_OK ? "" :
      ": " + std::string(zvec_error_code_to_string(code))));
}

struct FixtureDoc { std::string pk; std::string text; };
const std::vector<FixtureDoc> kInitialDocs = {
    {"pk-ascii", "The QUICK brown fox jumps over quick."},
    {"pk-unicode", "Café naïve 中文测试 — Straße"},
    {"pk-punctuation", "email foo-bar rock'n'roll foo_bar 3.14"},
    {"pk-repeated", "echo echo echo"},
    {"pk-empty", ""},
    {"pk-short", "alpha"},
    {"pk-long", "alpha beta gamma delta epsilon zeta eta theta iota kappa"},
    {"pk-mixed", "quick alpha echo 中文"},
    {"pk-tie-a", "shared tie"},
    {"pk-tie-b", "shared tie"},
};
const std::map<std::string, std::vector<uint32_t>> kExpectedNativeOffsets = {
    {"pk-ascii", {0, 4, 10, 16, 20, 26, 31}},
    {"pk-unicode", {0, 6, 13, 16, 19, 22, 30}},
    {"pk-punctuation", {0, 6, 10, 14, 26, 34}},
    {"pk-repeated", {0, 5, 10}},
    {"pk-empty", {}},
    {"pk-short", {0}},
    {"pk-long", {0, 6, 11, 17, 23, 31, 36, 40, 46, 51}},
    {"pk-mixed", {0, 6, 12, 17, 20}},
    {"pk-tie-a", {0, 7}},
    {"pk-tie-b", {0, 7}},
};

std::vector<m4::Token> LegacyM4Tokens(const std::string& text) {
  std::vector<m4::Token> out;
  std::string cur;
  uint32_t pos = 0;
  auto flush = [&]() {
    if (!cur.empty()) { out.push_back({cur, pos++}); cur.clear(); }
  };
  for (unsigned char c : text) {
    if (c < 0x80) {
      if (std::isalnum(c) || c == '_') cur.push_back(static_cast<char>(std::tolower(c)));
      else flush();
    } else {
      cur.push_back(static_cast<char>(c));
    }
  }
  flush();
  return out;
}

std::vector<zvec::fts::Token> NativeTokensWithOffsets(const std::string& text) {
  static const zvec::fts::StandardTokenizer tokenizer = [] {
    zvec::fts::StandardTokenizer instance;
    const zvec::ailego::JsonObject empty_config;
    const auto status = instance.init(empty_config);
    if (!status.ok()) throw std::runtime_error(status.message());
    return instance;
  }();
  static const zvec::fts::LowercaseTokenFilter lowercase;
  return lowercase.filter(tokenizer.tokenize(text));
}

std::string FormatNativeTokens(const std::vector<zvec::fts::Token>& tokens) {
  std::string out = "[";
  for (size_t i = 0; i < tokens.size(); ++i) {
    if (i) out += ", ";
    out += "{term=\"" + tokens[i].text + "\",pos=" +
           std::to_string(tokens[i].position) + ",offset=" +
           std::to_string(tokens[i].offset) + "}";
  }
  return out + "]";
}

std::string FormatTokens(const std::vector<m4::Token>& tokens) {
  std::string out = "[";
  for (size_t i = 0; i < tokens.size(); ++i) {
    if (i) out += ", ";
    out += "{term=\"" + tokens[i].term + "\",pos=" +
           std::to_string(tokens[i].pos) + "}";
  }
  return out + "]";
}

struct NativeCollection {
  zvec_collection_t* handle{nullptr};
  ~NativeCollection() { if (handle) zvec_collection_close(handle); }
};

zvec_collection_t* CreateNative(const std::string& path) {
  auto* params = zvec_index_params_create(ZVEC_INDEX_TYPE_FTS);
  auto* filters = zvec_string_array_create(1);
  zvec_string_array_add(filters, 0, "lowercase");
  CheckCode(zvec_index_params_set_fts_params(params, "standard", filters, ""),
            "set native FTS params");
  auto* field = zvec_field_schema_create("content", ZVEC_DATA_TYPE_STRING,
                                          false, 0);
  CheckCode(zvec_field_schema_set_index_params(field, params),
            "set native field FTS index");
  auto* schema = zvec_collection_schema_create("m4_p1_fts");
  CheckCode(zvec_collection_schema_add_field(schema, field),
            "add native FTS field");
  auto* options = zvec_collection_options_create();
  CheckCode(zvec_collection_options_set_enable_mmap(options, true),
            "enable native mmap in isolated fixture");
  zvec_collection_t* collection = nullptr;
  const auto code = zvec_collection_create_and_open(
      path.c_str(), schema, options, &collection);
  CheckCode(code, "native collection create/open");
  zvec_collection_options_destroy(options);
  zvec_collection_schema_destroy(schema);
  zvec_field_schema_destroy(field);
  zvec_index_params_destroy(params);
  zvec_string_array_destroy(filters);
  return collection;
}

void NativeWrite(zvec_collection_t* collection,
                 const std::vector<FixtureDoc>& docs, bool upsert,
                 const std::string& label) {
  std::vector<zvec_doc_t*> owned;
  std::vector<const zvec_doc_t*> docs_arg;
  for (const auto& item : docs) {
    auto* doc = zvec_doc_create();
    zvec_doc_set_pk(doc, item.pk.c_str());
    CheckCode(zvec_doc_add_field_by_value(doc, "content", ZVEC_DATA_TYPE_STRING,
                                           item.text.data(), item.text.size()),
              label + " set content " + item.pk);
    owned.push_back(doc);
    docs_arg.push_back(doc);
  }
  zvec_write_result_t* results = nullptr;
  size_t result_count = 0;
  const auto code = upsert
      ? zvec_collection_upsert_with_results(collection, docs_arg.data(),
                                            docs_arg.size(), &results,
                                            &result_count)
      : zvec_collection_insert_with_results(collection, docs_arg.data(),
                                            docs_arg.size(), &results,
                                            &result_count);
  CheckCode(code, label + " native write");
  Check(result_count == docs.size(), label + " per-document result count");
  for (size_t i = 0; i < result_count; ++i) {
    Check(results[i].code == ZVEC_OK,
          label + " native item " + std::to_string(i) + ": " +
              (results[i].message ? results[i].message : ""));
  }
  zvec_write_results_free(results, result_count);
  for (auto* doc : owned) zvec_doc_destroy(doc);
}

struct Hit { std::string pk; float score{0}; };
std::vector<Hit> NativeSearch(zvec_collection_t* collection,
                              const std::string& query_text, int top_k) {
  auto* query = zvec_vector_query_create();
  CheckCode(zvec_vector_query_set_field_name(query, "content"),
            "native query field");
  CheckCode(zvec_vector_query_set_topk(query, top_k), "native query topk");
  auto* fts = zvec_fts_create();
  CheckCode(zvec_fts_set_query_string(fts, query_text.c_str()),
            "native query string");
  CheckCode(zvec_vector_query_set_fts(query, fts), "attach native FTS query");
  auto* params = zvec_query_params_fts_create("OR");
  CheckCode(zvec_vector_query_set_fts_params(query, params),
            "native default operator OR");

  zvec_doc_t** docs = nullptr;
  size_t count = 0;
  const auto code = zvec_collection_query(collection, query, &docs, &count);
  CheckCode(code, "native query '" + query_text + "'");
  std::vector<Hit> hits;
  if (code == ZVEC_OK) {
    for (size_t i = 0; i < count; ++i) {
      const char* pk = zvec_doc_get_pk_pointer(docs[i]);
      hits.push_back({pk ? pk : "", zvec_doc_get_score(docs[i])});
    }
  }
  zvec_docs_free(docs, count);
  zvec_fts_destroy(fts);
  zvec_vector_query_destroy(query);
  return hits;
}

std::vector<Hit> M4Search(const m4::Index& index, const std::string& query,
                          uint32_t top_k) {
  m4::SearchResult result;
  const auto status = index.SearchFts(query, top_k, &result);
  Check(status.ok, "M4 query '" + query + "': " + status.message);
  std::vector<Hit> hits;
  for (const auto& hit : result.hits) hits.push_back({hit.pk, hit.score});
  return hits;
}

bool ScoresClose(float a, float b) {
  return std::abs(static_cast<double>(a) - b) <= kScoreAbsTolerance +
         kScoreRelTolerance * std::max(std::abs(a), std::abs(b));
}

struct DiagnosticStats {
  size_t n{0};
  size_t total_tokens{0};
  size_t df{0};
  double avgdl{1.0};
};

std::vector<FixtureDoc> DiagnosticNativeHistory() {
  auto docs = kInitialDocs;
  docs.push_back({"pk-ascii", "quick red fox quick"});
  docs.push_back({"pk-new", "quick brown alpha"});
  return docs;
}

std::vector<FixtureDoc> DiagnosticM4LiveDocs() {
  std::vector<FixtureDoc> docs;
  for (const auto& doc : kInitialDocs) {
    if (doc.pk != "pk-ascii" && doc.pk != "pk-punctuation") docs.push_back(doc);
  }
  docs.push_back({"pk-ascii", "quick red fox quick"});
  docs.push_back({"pk-new", "quick brown alpha"});
  return docs;
}

uint32_t DiagnosticTf(const FixtureDoc& doc, const std::string& term) {
  uint32_t tf = 0;
  for (const auto& token : NativeTokensWithOffsets(doc.text)) {
    if (token.text == term) ++tf;
  }
  return tf;
}

uint32_t DiagnosticDl(const FixtureDoc& doc) {
  return static_cast<uint32_t>(NativeTokensWithOffsets(doc.text).size());
}

DiagnosticStats DiagnosticCorpusStats(const std::vector<FixtureDoc>& docs,
                                      const std::string& term) {
  DiagnosticStats stats;
  for (const auto& doc : docs) {
    ++stats.n;
    stats.total_tokens += DiagnosticDl(doc);
    if (DiagnosticTf(doc, term) != 0) ++stats.df;
  }
  stats.avgdl = stats.n == 0 ? 1.0 :
      static_cast<double>(stats.total_tokens) / stats.n;
  return stats;
}

const FixtureDoc* DiagnosticDoc(const std::vector<FixtureDoc>& docs,
                                const std::string& pk) {
  for (auto it = docs.rbegin(); it != docs.rend(); ++it) {
    if (it->pk == pk) return &*it;
  }
  return nullptr;
}

double DiagnosticContribution(const DiagnosticStats& stats, uint32_t tf,
                              uint32_t dl, bool native_float) {
  if (native_float) {
    const float n = static_cast<float>(stats.n);
    const float df = static_cast<float>(stats.df);
    const float avgdl = static_cast<float>(stats.avgdl);
    const float idf = std::log((n - df + 0.5f) / (df + 0.5f) + 1.0f);
    const float tf_value = static_cast<float>(tf);
    const float dl_value = static_cast<float>(dl);
    const float norm = tf_value * 2.2f /
        (tf_value + 1.2f * (0.25f + 0.75f * dl_value / avgdl));
    return idf * norm;
  }
  const double n = static_cast<double>(stats.n);
  const double df = static_cast<double>(stats.df);
  const double idf = std::log((n - df + 0.5) / (df + 0.5) + 1.0);
  const double tf_value = static_cast<double>(tf);
  const double dl_value = static_cast<double>(dl);
  return idf * (tf_value * 2.2 /
      (tf_value + 1.2 * (0.25 + 0.75 * dl_value / stats.avgdl)));
}

void PrintMismatchBreakdown(const std::string& stage,
                            const std::string& query, const FixtureDoc& doc,
                            float native_score, float m4_score) {
  // This source-derived model applies to all post-mutation stages in this
  // fixture: 12 physical inserts / 50 tokens; only 2/12 rows are tombstoned,
  // below native's 30% purge threshold, so Optimize retains those statistics.
  static const auto native_history = DiagnosticNativeHistory();
  static const auto m4_live = DiagnosticM4LiveDocs();
  std::vector<std::string> terms;
  for (const auto& token : NativeTokensWithOffsets(query)) {
    if (token.text == "and" || token.text == "or") continue;
    if (std::find(terms.begin(), terms.end(), token.text) == terms.end()) {
      terms.push_back(token.text);
    }
  }
  double native_final = 0.0;
  double m4_final = 0.0;
  for (const auto& term : terms) {
    const uint32_t tf = DiagnosticTf(doc, term);
    if (tf == 0) continue;
    const uint32_t dl = DiagnosticDl(doc);
    const auto native_stats = DiagnosticCorpusStats(native_history, term);
    const auto m4_stats = DiagnosticCorpusStats(m4_live, term);
    const double native_part = DiagnosticContribution(native_stats, tf, dl, true);
    const double m4_part = DiagnosticContribution(m4_stats, tf, dl, false);
    native_final += native_part;
    m4_final += m4_part;
    std::cerr << std::setprecision(9)
              << "BM25_DETAIL engine=native-source-model stage=" << stage
              << " query='" << query << "' pk=" << doc.pk << " term=" << term
              << " N=" << native_stats.n << " df=" << native_stats.df
              << " tf=" << tf << " dl=" << dl
              << " total_tokens=" << native_stats.total_tokens
              << " avgdl=" << native_stats.avgdl
              << " k1=1.2 b=0.75 idf="
              << std::log((native_stats.n - native_stats.df + 0.5) /
                          (native_stats.df + 0.5) + 1.0)
              << " contribution=" << native_part << '\n'
              << "BM25_DETAIL engine=M4-live-model stage=" << stage
              << " query='" << query << "' pk=" << doc.pk << " term=" << term
              << " N=" << m4_stats.n << " df=" << m4_stats.df
              << " tf=" << tf << " dl=" << dl
              << " total_tokens=" << m4_stats.total_tokens
              << " avgdl=" << m4_stats.avgdl
              << " k1=1.2 b=0.75 idf="
              << std::log((m4_stats.n - m4_stats.df + 0.5) /
                          (m4_stats.df + 0.5) + 1.0)
              << " contribution=" << m4_part << '\n';
  }
  std::cerr << std::setprecision(9)
            << "BM25_DETAIL_FINAL stage=" << stage << " query='" << query
            << "' pk=" << doc.pk << " native=" << native_score
            << " source_formula=" << native_final << " M4=" << m4_score
            << " live_formula=" << m4_final << '\n';
}

void CompareQuery(const std::string& stage, zvec_collection_t* native,
                  const m4::Index& index, const std::string& query,
                  uint32_t top_k) {
  const auto expected = NativeSearch(native, query, static_cast<int>(top_k));
  const auto actual = M4Search(index, query, top_k);
  const std::string label = stage + " query='" + query + "' top_k=" +
                            std::to_string(top_k);
  std::vector<std::string> exp_order, got_order;
  for (const auto& hit : expected) exp_order.push_back(hit.pk);
  for (const auto& hit : actual) got_order.push_back(hit.pk);
  auto exp_set = exp_order;
  auto got_set = got_order;
  std::sort(exp_set.begin(), exp_set.end());
  std::sort(got_set.begin(), got_set.end());
  if (exp_set != got_set) {
    std::cerr << "MISMATCH category=matching fixture=" << stage << " query='"
              << query << "' expected(native)=[";
    for (const auto& pk : exp_set) std::cerr << pk << ' ';
    std::cerr << "] actual(M4)=[";
    for (const auto& pk : got_set) std::cerr << pk << ' ';
    std::cerr << "]\n";
  }
  Check(exp_set == got_set, label + " exact logical-PK hit set");
  std::map<std::string, float> expected_score_by_pk, actual_score_by_pk;
  std::map<std::string, size_t> expected_rank_by_pk, actual_rank_by_pk;
  for (size_t i = 0; i < expected.size(); ++i) {
    expected_score_by_pk[expected[i].pk] = expected[i].score;
    expected_rank_by_pk[expected[i].pk] = i;
  }
  for (size_t i = 0; i < actual.size(); ++i) {
    actual_score_by_pk[actual[i].pk] = actual[i].score;
    actual_rank_by_pk[actual[i].pk] = i;
  }
  bool rank_order_matches = exp_order == got_order;
  if (!rank_order_matches && exp_set == got_set) {
    rank_order_matches = true;
    for (size_t i = 0; i < exp_order.size() && rank_order_matches; ++i) {
      for (size_t j = i + 1; j < exp_order.size(); ++j) {
        const auto& first = exp_order[i];
        const auto& second = exp_order[j];
        const float native_first = expected_score_by_pk.at(first);
        const float native_second = expected_score_by_pk.at(second);
        const float m4_first = actual_score_by_pk.at(first);
        const float m4_second = actual_score_by_pk.at(second);
        const bool native_tie = ScoresClose(native_first, native_second);
        const bool m4_tie = ScoresClose(m4_first, m4_second);
        if (native_tie && m4_tie) continue;
        const bool native_first_ahead =
            expected_rank_by_pk.at(first) < expected_rank_by_pk.at(second);
        const bool m4_first_ahead =
            actual_rank_by_pk.at(first) < actual_rank_by_pk.at(second);
        if (native_first_ahead != m4_first_ahead) {
          rank_order_matches = false;
          break;
        }
      }
    }
  }
  if (exp_order != got_order) {
    std::cerr << "MISMATCH category=ordering"
              << (rank_order_matches ? " kind=tied-order-unspecified" :
                                        " kind=rank-order")
              << " fixture=" << label << " expected(native)=[";
    for (const auto& pk : exp_order) std::cerr << pk << ' ';
    std::cerr << "] actual(M4)=[";
    for (const auto& pk : got_order) std::cerr << pk << ' ';
    std::cerr << "]\n";
  }
  Check(rank_order_matches,
        label + " ordering outside equivalent-score ties");
  for (const auto& [pk, expected_score] : expected_score_by_pk) {
    const auto actual_it = actual_score_by_pk.find(pk);
    if (actual_it == actual_score_by_pk.end()) continue;
    if (!ScoresClose(expected_score, actual_it->second)) {
      static const auto diagnostic_m4_live = DiagnosticM4LiveDocs();
      const auto* diagnostic_doc = DiagnosticDoc(diagnostic_m4_live, pk);
      if (diagnostic_doc) {
        PrintMismatchBreakdown(stage, query, *diagnostic_doc,
                               expected_score, actual_it->second);
      }
      std::cerr << "MISMATCH category=score/stats fixture=" << stage
                << " query='" << query << "' pk=" << pk
                << " expected(native)=" << expected_score
                << " actual(M4)=" << actual_it->second << "\n";
    }
    Check(ScoresClose(expected_score, actual_it->second),
          label + " BM25 score pk=" + pk);
  }
}

void CompareTokenization() {
  size_t legacy_mismatches = 0;
  for (const auto& doc : kInitialDocs) {
    const auto legacy = LegacyM4Tokens(doc.text);
    const auto native = m4::TokenizeWithNativeZvec(doc.text);
    const auto native_detailed = NativeTokensWithOffsets(doc.text);
    const auto adapted = m4::Tokenize(doc.text);
    std::vector<uint32_t> actual_offsets;
    for (const auto& token : native_detailed) actual_offsets.push_back(token.offset);
    Check(actual_offsets == kExpectedNativeOffsets.at(doc.pk),
          "native standard-tokenizer byte offsets " + doc.pk);
    std::cout << "native_tokens fixture=" << doc.pk << " "
              << FormatNativeTokens(native_detailed) << '\n';
    if (legacy.size() != native.size() ||
        !std::equal(legacy.begin(), legacy.end(), native.begin(),
                    [](const auto& a, const auto& b) {
                      return a.term == b.term && a.pos == b.pos;
                    })) {
      ++legacy_mismatches;
      std::cerr << "MISMATCH category=tokenizer fixture=" << doc.pk
                << " input=\"" << doc.text << "\" expected(legacy M4)="
                << FormatTokens(legacy)
                << " actual(native standard+lowercase)="
                << FormatNativeTokens(native_detailed) << '\n';
    }
    Check(adapted.size() == native.size() &&
              std::equal(adapted.begin(), adapted.end(), native.begin(),
                         [](const auto& a, const auto& b) {
                           return a.term == b.term && a.pos == b.pos;
                         }),
          "adapter token/position parity " + doc.pk);
  }
  std::cout << "baseline_legacy_tokenizer_mismatches=" << legacy_mismatches
            << '/' << kInitialDocs.size() << '\n';
}

void WriteBoth(zvec_collection_t* native, m4::Index* m4_index,
               const std::vector<FixtureDoc>& docs, bool upsert,
               const std::string& label) {
  NativeWrite(native, docs, upsert, label);
  for (const auto& item : docs) {
    const auto status = m4_index->Upsert(item.pk, m4::Document{item.text, {}});
    Check(status.ok, label + " M4 upsert " + item.pk + ": " + status.message);
  }
  CheckCode(zvec_collection_flush(native), label + " native flush");
  const auto status = m4_index->Flush();
  Check(status.ok, label + " M4 flush: " + status.message);
}

}  // namespace

int main() {
  const fs::path root = fs::temp_directory_path() /
      ("m4-p1-native-fts-" + std::to_string(::getpid()));
  fs::create_directories(root);
  const std::string native_path = (root / "native_collection").string();
  const std::string m4_path = (root / "m4_index").string();

  std::cout << "analyzer=standard filters=[lowercase] extra_params={}"
            << " bm25=(k1=1.2,b=0.75) score_tolerance=abs(1e-4)+rel(1e-4)\n";
  CompareTokenization();

  NativeCollection native{CreateNative(native_path)};
  if (!native.handle) return 2;
  m4::Options options;
  options.fts_field = "content";
  options.bm25_k1 = 1.2;
  options.bm25_b = 0.75;
  std::unique_ptr<m4::Index> m4_index;
  auto status = m4::Index::Open(m4_path, options, &m4_index);
  Check(status.ok, "M4 open: " + status.message);
  if (!status.ok) return 2;

  WriteBoth(native.handle, m4_index.get(), kInitialDocs, false, "initial");
  const std::vector<std::pair<std::string, uint32_t>> queries = {
      {"quick", 50}, {"\"quick brown\"", 50},
      {"quick AND brown", 50}, {"quick OR alpha", 50},
      {"echo", 50}, {"中文", 50}, {"alpha", 2}, {"tie", 50},
  };
  for (const auto& [query, top_k] : queries) {
    CompareQuery("initial", native.handle, *m4_index, query, top_k);
  }

  WriteBoth(native.handle, m4_index.get(),
            {{"pk-ascii", "quick red fox quick"},
             {"pk-new", "quick brown alpha"}}, true, "update/add");
  const char* deleted_pk[] = {"pk-punctuation"};
  zvec_write_result_t* delete_results = nullptr;
  size_t delete_count = 0;
  const auto delete_code = zvec_collection_delete_with_results(
      native.handle, deleted_pk, 1, &delete_results, &delete_count);
  CheckCode(delete_code, "native delete");
  Check(delete_count == 1 && delete_results != nullptr &&
            delete_results[0].code == ZVEC_OK,
        "native delete per-document result");
  zvec_write_results_free(delete_results, delete_count);
  status = m4_index->Delete("pk-punctuation");
  Check(status.ok, "M4 delete: " + status.message);
  CheckCode(zvec_collection_flush(native.handle), "native flush after delete");
  status = m4_index->Flush();
  Check(status.ok, "M4 flush after delete: " + status.message);
  for (const auto& [query, top_k] : queries) {
    CompareQuery("mutated-before-reopen", native.handle, *m4_index, query,
                 top_k);
  }

  CheckCode(zvec_collection_optimize(native.handle), "native optimize after mutations");
  status = m4_index->CompactNow();
  Check(status.ok, "M4 compact after mutations: " + status.message);
  for (const auto& [query, top_k] : queries) {
    CompareQuery("after-maintenance", native.handle, *m4_index, query,
                 top_k);
  }

  CheckCode(zvec_collection_close(native.handle), "native close before reopen");
  native.handle = nullptr;
  CheckCode(zvec_collection_open(native_path.c_str(), nullptr, &native.handle),
            "native reopen");
  m4_index.reset();
  status = m4::Index::Open(m4_path, options, &m4_index);
  Check(status.ok, "M4 reopen: " + status.message);
  if (status.ok) {
    for (const auto& [query, top_k] : queries) {
      CompareQuery("after-reopen", native.handle, *m4_index, query, top_k);
    }
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
