// Research-only differential for one native collection and one M4 index with
// independent FTS fields/analyzers, including mutation, compaction and WAL
// recovery.
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <tuple>
#include <unistd.h>
#include <vector>

#include "db/index/column/fts_column/tokenizer/jieba_tokenizer.h"
#include "db/index/column/fts_column/tokenizer/standard_tokenizer.h"
#include "db/index/column/fts_column/tokenizer/token_filter.h"
#include "m4/index.h"
#include "tokenizer.h"
#include <zvec/ailego/encoding/json/mod_json_plus.h>
#include <zvec/c_api.h>

#ifndef M4_ZVEC_JIEBA_DICT_DIR
#error "CMake must provide the native cppjieba dictionary path"
#endif
#ifndef M4_TEST_TMPDIR
#error "CMake must provide the bounded M4 test temporary directory"
#endif

namespace fs = std::filesystem;
namespace {
int checks = 0;
int failures = 0;
using TokenSignature = std::tuple<std::string, uint32_t, uint32_t>;

void Check(bool ok, const std::string &label) {
  ++checks;
  if (!ok) {
    ++failures;
    std::cerr << "FAIL: " << label << '\n';
  }
}

bool CheckCode(zvec_error_code_t code, const std::string &label) {
  if (code == ZVEC_OK)
    return true;
  zvec_error_details_t detail{};
  zvec_get_last_error_details(&detail);
  Check(false, label + ": " + zvec_error_code_to_string(code) + " " +
                   (detail.message ? detail.message : ""));
  return false;
}

class TempRoot {
public:
  bool Create() {
    std::error_code error;
    const fs::path parent(M4_TEST_TMPDIR);
    fs::create_directories(parent, error);
    if (error)
      return false;
    std::string pattern = (parent / "m4-multifield-tokenizer-XXXXXX").string();
    std::vector<char> buffer(pattern.begin(), pattern.end());
    buffer.push_back('\0');
    char *created = mkdtemp(buffer.data());
    if (!created)
      return false;
    path_ = created;
    return true;
  }

  const fs::path &path() const { return path_; }

  bool Remove() {
    std::error_code error;
    fs::remove_all(path_, error);
    return !error && !fs::exists(path_);
  }

  ~TempRoot() {
    if (!path_.empty()) {
      std::error_code error;
      fs::remove_all(path_, error);
    }
  }

private:
  fs::path path_;
};

struct NativeCollection {
  zvec_collection_t *handle{nullptr};
  ~NativeCollection() {
    if (handle)
      zvec_collection_close(handle);
  }
};

bool AddFtsField(zvec_collection_schema_t *schema, const char *field_name,
                 const char *tokenizer) {
  auto *params = zvec_index_params_create(ZVEC_INDEX_TYPE_FTS);
  auto *filters = zvec_string_array_create(1);
  auto *field =
      zvec_field_schema_create(field_name, ZVEC_DATA_TYPE_STRING, false, 0);
  bool ok = params && filters && field;
  if (!ok) {
    Check(false, std::string("allocate native FTS schema for ") + field_name);
  } else {
    zvec_string_array_add(filters, 0, "lowercase");
    ok = CheckCode(zvec_index_params_set_fts_params(params, tokenizer, filters,
                                                    nullptr),
                   std::string("configure ") + tokenizer + " for " +
                       field_name) &&
         ok;
    ok = CheckCode(zvec_field_schema_set_index_params(field, params),
                   std::string("set FTS params for ") + field_name) &&
         ok;
    ok = CheckCode(zvec_collection_schema_add_field(schema, field),
                   std::string("add field ") + field_name) &&
         ok;
  }
  if (field)
    zvec_field_schema_destroy(field);
  if (params)
    zvec_index_params_destroy(params);
  if (filters)
    zvec_string_array_destroy(filters);
  return ok;
}

zvec_collection_t *CreateNative(const fs::path &path) {
  auto *schema = zvec_collection_schema_create("m4_multifield_tokenizer");
  if (!schema) {
    Check(false, "create native collection schema");
    return nullptr;
  }
  bool ok = AddFtsField(schema, "title", "standard");
  ok = AddFtsField(schema, "body", "jieba") && ok;
  zvec_collection_t *collection = nullptr;
  if (ok) {
    ok = CheckCode(zvec_collection_create_and_open(path.c_str(), schema,
                                                   nullptr, &collection),
                   "create/open native two-field collection");
  }
  zvec_collection_schema_destroy(schema);
  if (!ok) {
    if (collection)
      zvec_collection_close(collection);
    return nullptr;
  }
  return collection;
}

struct FixtureDoc {
  std::string pk;
  std::string title;
  std::string body;
};

bool NativeWrite(zvec_collection_t *collection, const FixtureDoc &item,
                 bool upsert) {
  auto *doc = zvec_doc_create();
  if (!doc) {
    Check(false, "allocate native document " + item.pk);
    return false;
  }
  zvec_doc_set_pk(doc, item.pk.c_str());
  bool ok = CheckCode(
      zvec_doc_add_field_by_value(doc, "title", ZVEC_DATA_TYPE_STRING,
                                  item.title.data(), item.title.size()),
      "set title " + item.pk);
  ok =
      CheckCode(zvec_doc_add_field_by_value(doc, "body", ZVEC_DATA_TYPE_STRING,
                                            item.body.data(), item.body.size()),
                "set body " + item.pk) &&
      ok;
  const zvec_doc_t *docs[] = {doc};
  zvec_write_result_t *results = nullptr;
  size_t count = 0;
  if (ok) {
    const auto code = upsert ? zvec_collection_upsert_with_results(
                                   collection, docs, 1, &results, &count)
                             : zvec_collection_insert_with_results(
                                   collection, docs, 1, &results, &count);
    ok = CheckCode(code,
                   std::string(upsert ? "upsert " : "insert ") + item.pk) &&
         ok;
  }
  Check(count == 1 && results && results[0].code == ZVEC_OK,
        "native per-document write result " + item.pk);
  zvec_write_results_free(results, count);
  zvec_doc_destroy(doc);
  return ok && count == 1;
}

bool NativeDelete(zvec_collection_t *collection, const std::string &pk) {
  const char *pks[] = {pk.c_str()};
  zvec_write_result_t *results = nullptr;
  size_t count = 0;
  bool ok = CheckCode(
      zvec_collection_delete_with_results(collection, pks, 1, &results, &count),
      "delete native " + pk);
  ok = count == 1 && results && results[0].code == ZVEC_OK && ok;
  Check(ok, "native delete result " + pk);
  zvec_write_results_free(results, count);
  return ok;
}

struct NativeHits {
  bool ok{false};
  std::set<std::string> pks;
};

NativeHits NativeSearch(zvec_collection_t *collection, const std::string &field,
                        const std::string &query_text) {
  auto *query = zvec_vector_query_create();
  auto *fts = zvec_fts_create();
  auto *params = zvec_query_params_fts_create("OR");
  bool ok = query && fts && params;
  if (!ok)
    Check(false, "allocate native query objects");
  if (ok) {
    ok = CheckCode(zvec_vector_query_set_field_name(query, field.c_str()),
                   "select native FTS field " + field) &&
         ok;
    ok = CheckCode(zvec_vector_query_set_topk(query, 32), "set native top-k") &&
         ok;
    ok = CheckCode(zvec_fts_set_query_string(fts, query_text.c_str()),
                   "set native query string") &&
         ok;
    ok = CheckCode(zvec_vector_query_set_fts(query, fts),
                   "attach native FTS query") &&
         ok;
    if (ok) {
      const auto code = zvec_vector_query_set_fts_params(query, params);
      if (code == ZVEC_OK)
        params = nullptr;
      ok = CheckCode(code, "set native default OR operator") && ok;
    }
  }
  zvec_doc_t **docs = nullptr;
  size_t count = 0;
  if (ok) {
    ok = CheckCode(zvec_collection_query(collection, query, &docs, &count),
                   "native query field=" + field + " text=`" + query_text +
                       "`") &&
         ok;
  }
  NativeHits result;
  result.ok = ok;
  if (ok) {
    for (size_t i = 0; i < count; ++i) {
      const char *pk = zvec_doc_get_pk_pointer(docs[i]);
      if (pk)
        result.pks.emplace(pk);
    }
  }
  zvec_docs_free(docs, count);
  if (query)
    zvec_vector_query_destroy(query);
  if (fts)
    zvec_fts_destroy(fts);
  if (params)
    zvec_query_params_fts_destroy(params);
  return result;
}

bool M4Upsert(m4::Index &index, const std::string &pk, const std::string &text,
              const std::string &field) {
  m4::Document doc;
  doc.text = text;
  const auto status = index.Upsert(pk, doc);
  Check(status.ok,
        "M4 upsert field=" + field + " pk=" + pk + ": " + status.message);
  return status.ok;
}

bool M4MultiUpsert(m4::Index &index, const FixtureDoc &item) {
  m4::Document doc;
  doc.fts_fields.emplace("title", item.title);
  doc.fts_fields.emplace("body", item.body);
  const auto status = index.Upsert(item.pk, doc);
  Check(status.ok,
        "M4 multi-field upsert pk=" + item.pk + ": " + status.message);
  return status.ok;
}

std::set<std::string> M4Search(const m4::Index &index, const std::string &field,
                               const std::string &query_text, bool *ok) {
  m4::SearchResult result;
  const auto status = index.SearchFts(query_text, 32, &result);
  *ok = status.ok;
  Check(status.ok, "M4 query field=" + field + " text=`" + query_text +
                       "`: " + status.message);
  std::set<std::string> pks;
  if (status.ok) {
    for (const auto &hit : result.hits)
      pks.emplace(hit.pk);
  }
  return pks;
}

std::set<std::string> M4MultiSearch(const m4::Index &index,
                                    const std::string &field,
                                    const std::string &query_text, bool *ok) {
  m4::SearchResult result;
  const auto status = index.SearchFts(field, query_text, 32, &result);
  *ok = status.ok;
  Check(status.ok, "M4 multi-field query field=" + field + " text=`" +
                       query_text + "`: " + status.message);
  std::set<std::string> pks;
  if (status.ok) {
    for (const auto &hit : result.hits)
      pks.emplace(hit.pk);
  }
  return pks;
}

void CompareQuery(zvec_collection_t *native, const m4::Index &title_index,
                  const m4::Index &body_index, const m4::Index &multi_index,
                  const std::string &field, const std::string &query,
                  const std::set<std::string> &expected,
                  const std::string &stage) {
  const auto native_result = NativeSearch(native, field, query);
  bool m4_ok = false;
  bool multi_ok = false;
  const auto &m4_index = field == "title" ? title_index : body_index;
  const auto m4_result = M4Search(m4_index, field, query, &m4_ok);
  const auto multi_result = M4MultiSearch(multi_index, field, query, &multi_ok);
  if (native_result.ok && m4_ok && native_result.pks != m4_result) {
    std::cerr << "DIFF stage=" << stage << " field=" << field << " query=`"
              << query << "` native={";
    for (const auto &pk : native_result.pks)
      std::cerr << pk << ",";
    std::cerr << "} m4={";
    for (const auto &pk : m4_result)
      std::cerr << pk << ",";
    std::cerr << "}\n";
  }
  Check(native_result.ok && m4_ok && native_result.pks == m4_result,
        stage + " native/M4 single-field parity field=" + field + " query=`" +
            query + "`");
  Check(native_result.ok && multi_ok && native_result.pks == multi_result,
        stage + " native/M4 multi-field parity field=" + field + " query=`" +
            query + "`");
  Check(native_result.ok && native_result.pks == expected,
        stage + " expected field-isolated hits field=" + field + " query=`" +
            query + "`");
}

std::vector<TokenSignature> NativeStandardTokens(const std::string &text) {
  static const zvec::fts::StandardTokenizer tokenizer = [] {
    zvec::fts::StandardTokenizer instance;
    const zvec::ailego::JsonObject empty_config;
    const auto status = instance.init(empty_config);
    if (!status.ok())
      throw std::runtime_error(status.message());
    return instance;
  }();
  static const zvec::fts::LowercaseTokenFilter lowercase;
  const auto tokens = lowercase.filter(tokenizer.tokenize(text));
  std::vector<TokenSignature> signature;
  for (const auto &token : tokens) {
    signature.emplace_back(token.text, token.position, token.offset);
  }
  return signature;
}

std::vector<TokenSignature> NativeJiebaTokens(const std::string &text) {
  static const zvec::fts::JiebaTokenizer tokenizer = [] {
    zvec::fts::JiebaTokenizer instance;
    zvec::ailego::JsonObject config;
    config.set(zvec::ailego::JsonString("jieba_dict_dir"),
               zvec::ailego::JsonValue(M4_ZVEC_JIEBA_DICT_DIR));
    const auto status = instance.init(config);
    if (!status.ok())
      throw std::runtime_error(status.message());
    return instance;
  }();
  static const zvec::fts::LowercaseTokenFilter lowercase;
  const auto tokens = lowercase.filter(tokenizer.tokenize(text));
  std::vector<TokenSignature> signature;
  for (const auto &token : tokens) {
    signature.emplace_back(token.text, token.position, token.offset);
  }
  return signature;
}

std::vector<TokenSignature> M4Tokens(const m4::Analyzer &analyzer,
                                     const std::string &text) {
  const auto tokens = analyzer.Tokenize(text);
  std::vector<TokenSignature> signature;
  for (const auto &token : tokens) {
    signature.emplace_back(token.term, token.pos, token.offset);
  }
  return signature;
}

void CheckTokenParity(const m4::Analyzer &standard, const m4::Analyzer &jieba) {
  const std::vector<std::string> standard_inputs = {
      "The QUICK parser, Café_17.", "alpha-beta 3.14", "HTTPParser API"};
  for (size_t i = 0; i < standard_inputs.size(); ++i) {
    Check(NativeStandardTokens(standard_inputs[i]) ==
              M4Tokens(standard, standard_inputs[i]),
          "standard field token/position/offset parity fixture=" +
              std::to_string(i));
  }
  const std::vector<std::string> jieba_inputs = {
      "深度学习模型", "HTTPParser 深度学习 API", "苹果 orchard"};
  for (size_t i = 0; i < jieba_inputs.size(); ++i) {
    Check(NativeJiebaTokens(jieba_inputs[i]) ==
              M4Tokens(jieba, jieba_inputs[i]),
          "Jieba field token/position/offset parity fixture=" +
              std::to_string(i));
  }
}

bool FlushAll(zvec_collection_t *native, m4::Index &title, m4::Index &body,
              m4::Index &multi, const std::string &stage) {
  bool ok = CheckCode(zvec_collection_flush(native), stage + " native flush");
  const auto title_status = title.Flush();
  Check(title_status.ok, stage + " M4 title flush: " + title_status.message);
  const auto body_status = body.Flush();
  Check(body_status.ok, stage + " M4 body flush: " + body_status.message);
  const auto multi_status = multi.Flush();
  Check(multi_status.ok,
        stage + " M4 multi-field flush: " + multi_status.message);
  return ok && title_status.ok && body_status.ok && multi_status.ok;
}

bool AddToAll(zvec_collection_t *native, m4::Index &title, m4::Index &body,
              m4::Index &multi, const FixtureDoc &item, bool upsert) {
  bool ok = NativeWrite(native, item, upsert);
  ok = M4Upsert(title, item.pk, item.title, "title") && ok;
  ok = M4Upsert(body, item.pk, item.body, "body") && ok;
  ok = M4MultiUpsert(multi, item) && ok;
  return ok;
}

void CheckQueries(zvec_collection_t *native, const m4::Index &title,
                  const m4::Index &body, const m4::Index &multi,
                  const std::string &stage, bool after_update) {
  if (!after_update) {
    CompareQuery(native, title, body, multi, "title", "parser", {"doc-a"},
                 stage);
    CompareQuery(native, title, body, multi, "body", "parser", {"doc-b"},
                 stage);
    CompareQuery(native, title, body, multi, "title", "orchard", {"doc-b"},
                 stage);
    CompareQuery(native, title, body, multi, "body", "orchard", {"doc-c"},
                 stage);
    CompareQuery(native, title, body, multi, "title", "Café", {"doc-a"}, stage);
    CompareQuery(native, title, body, multi, "body", "苹果", {"doc-c"}, stage);
    CompareQuery(native, title, body, multi, "title", "\"deep learning\"",
                 {"doc-c"}, stage);
    CompareQuery(native, title, body, multi, "body", "\"深度学习模型\"",
                 {"doc-a"}, stage);
    CompareQuery(native, title, body, multi, "title", "\"深度学习模型\"", {},
                 stage);
  } else {
    CompareQuery(native, title, body, multi, "title", "parser", {"doc-a"},
                 stage);
    CompareQuery(native, title, body, multi, "body", "parser", {"doc-b"},
                 stage);
    CompareQuery(native, title, body, multi, "title", "orchard", {"doc-b"},
                 stage);
    CompareQuery(native, title, body, multi, "body", "orchard", {}, stage);
    CompareQuery(native, title, body, multi, "title", "fresh", {"doc-b"},
                 stage);
    CompareQuery(native, title, body, multi, "body", "\"新鲜模型\"", {"doc-b"},
                 stage);
    CompareQuery(native, title, body, multi, "body", "苹果", {}, stage);
  }
}

} // namespace

int main() {
  TempRoot temporary;
  Check(temporary.Create(), "create isolated temp root under M4_TEST_TMPDIR");
  if (temporary.path().empty())
    return 2;
  const fs::path root = temporary.path();
  const std::string dict_dir = M4_ZVEC_JIEBA_DICT_DIR;
  unsetenv("ZVEC_JIEBA_DICT_DIR");
  zvec_set_default_jieba_dict_dir(dict_dir.c_str());
  if (!CheckCode(zvec_initialize(nullptr), "initialize native Zvec"))
    return 3;

  NativeCollection native{CreateNative(root / "native")};
  Check(native.handle != nullptr,
        "open native collection with standard title and Jieba body");
  if (!native.handle)
    return 4;

  m4::Options title_options;
  title_options.fts_field = "title";
  title_options.fts_tokenizer_name = "standard";
  m4::Options body_options;
  body_options.fts_field = "body";
  body_options.fts_tokenizer_name = "jieba";
  body_options.jieba_dict_dir = dict_dir;
  m4::Options multi_options;
  multi_options.fts_field = "body";
  multi_options.fts_fields = {{"title", "standard", ""},
                              {"body", "jieba", dict_dir}};
  std::unique_ptr<m4::Index> title_index;
  std::unique_ptr<m4::Index> body_index;
  std::unique_ptr<m4::Index> multi_index;
  auto title_status = m4::Index::Open((root / "m4-title").string(),
                                      title_options, &title_index);
  auto body_status =
      m4::Index::Open((root / "m4-body").string(), body_options, &body_index);
  auto multi_status = m4::Index::Open((root / "m4-multi").string(),
                                      multi_options, &multi_index);
  Check(title_status.ok,
        "open standard M4 title index: " + title_status.message);
  Check(body_status.ok, "open Jieba M4 body index: " + body_status.message);
  Check(multi_status.ok, "open M4 multi-field index: " + multi_status.message);
  if (!title_status.ok || !body_status.ok || !multi_status.ok)
    return 5;

  m4::Options mismatched_options = multi_options;
  mismatched_options.fts_fields[1].tokenizer_name = "standard";
  std::unique_ptr<m4::Index> mismatched_index;
  const auto mismatched_status = m4::Index::Open(
      (root / "m4-multi").string(), mismatched_options, &mismatched_index);
  Check(!mismatched_status.ok,
        "reject reopen with a mismatched per-field tokenizer identity");

  m4::Analyzer standard_analyzer;
  m4::Analyzer jieba_analyzer;
  const auto standard_init = standard_analyzer.Init("standard", "");
  const auto jieba_init = jieba_analyzer.Init("jieba", dict_dir);
  Check(standard_init.ok,
        "init standard M4 analyzer: " + standard_init.message);
  Check(jieba_init.ok, "init Jieba M4 analyzer: " + jieba_init.message);
  if (!standard_init.ok || !jieba_init.ok)
    return 6;
  CheckTokenParity(standard_analyzer, jieba_analyzer);

  const std::vector<FixtureDoc> initial_docs = {
      {"doc-a", "QUICK parser Café", "深度学习模型 quick"},
      {"doc-b", "orchard index", "parser 深度学习"},
      {"doc-c", "deep learning", "苹果 orchard"},
  };
  for (const auto &item : initial_docs) {
    AddToAll(native.handle, *title_index, *body_index, *multi_index, item,
             false);
  }
  FlushAll(native.handle, *title_index, *body_index, *multi_index, "initial");
  CheckQueries(native.handle, *title_index, *body_index, *multi_index,
               "initial", false);
  m4::SearchResult default_field_hits;
  const auto default_field_status =
      multi_index->SearchFts("parser", 10, &default_field_hits);
  Check(default_field_status.ok && default_field_hits.hits.size() == 1 &&
            default_field_hits.hits.front().pk == "doc-b",
        "legacy query shorthand uses configured default FTS field");

  m4::Document unknown_field;
  unknown_field.fts_fields.emplace("not-configured", "ignored");
  const auto unknown_status =
      multi_index->Upsert("unknown-field", unknown_field);
  Check(!unknown_status.ok, "reject document with unconfigured FTS field");
  m4::SearchResult invalid_query_result;
  const auto invalid_query_status = multi_index->SearchFts(
      "not-configured", "ignored", 10, &invalid_query_result);
  Check(!invalid_query_status.ok,
        "reject query against unconfigured FTS field");
  const auto invalid_hybrid_status = multi_index->SearchHybrid(
      "not-configured", "ignored", 10, 1.0, {}, &invalid_query_result);
  Check(!invalid_hybrid_status.ok,
        "reject hybrid query against unconfigured FTS field");

  m4::SearchResult title_hybrid_hits;
  const auto title_hybrid_status = multi_index->SearchHybrid(
      "title", "parser", 10, 1.0, {}, &title_hybrid_hits);
  Check(title_hybrid_status.ok && title_hybrid_hits.hits.size() == 1 &&
            title_hybrid_hits.hits.front().pk == "doc-a",
        "use selected field for hybrid title retrieval");
  m4::SearchResult body_hybrid_hits;
  const auto body_hybrid_status = multi_index->SearchHybrid(
      "body", "parser", 10, 1.0, {}, &body_hybrid_hits);
  Check(body_hybrid_status.ok && body_hybrid_hits.hits.size() == 1 &&
            body_hybrid_hits.hits.front().pk == "doc-b",
        "use selected field for hybrid body retrieval");

  m4::Document omitted_field;
  omitted_field.fts_fields.emplace("title", "titleonlyterm");
  const auto omitted_status =
      multi_index->Upsert("omitted-body", omitted_field);
  Check(omitted_status.ok, "allow omitted configured FTS field to be empty: " +
                               omitted_status.message);
  m4::SearchResult omitted_hits;
  const auto omitted_title_status =
      multi_index->SearchFts("title", "titleonlyterm", 10, &omitted_hits);
  Check(omitted_title_status.ok && omitted_hits.hits.size() == 1 &&
            omitted_hits.hits.front().pk == "omitted-body",
        "index present field in multi-field document");
  const auto omitted_body_status =
      multi_index->SearchFts("body", "titleonlyterm", 10, &omitted_hits);
  Check(omitted_body_status.ok && omitted_hits.hits.empty(),
        "treat omitted configured field as empty");
  const auto omitted_delete_status = multi_index->Delete("omitted-body");
  Check(omitted_delete_status.ok, "delete omitted-field test document");
  m4::Document default_text;
  default_text.text = "legacybodyterm";
  const auto default_text_status =
      multi_index->Upsert("default-text", default_text);
  Check(default_text_status.ok,
        "accept legacy document text for default FTS field");
  const auto default_text_query_status =
      multi_index->SearchFts("body", "legacybodyterm", 10, &omitted_hits);
  Check(default_text_query_status.ok && omitted_hits.hits.size() == 1 &&
            omitted_hits.hits.front().pk == "default-text",
        "index legacy document text in configured default field");
  const auto default_text_delete_status = multi_index->Delete("default-text");
  Check(default_text_delete_status.ok, "delete default-text test document");
  Check(FlushAll(native.handle, *title_index, *body_index, *multi_index,
                 "omitted-field"),
        "flush omitted-field multi-field test state");

  AddToAll(native.handle, *title_index, *body_index, *multi_index,
           {"doc-b", "fresh orchard", "新鲜模型 parser"}, true);
  NativeDelete(native.handle, "doc-c");
  auto title_delete = title_index->Delete("doc-c");
  auto body_delete = body_index->Delete("doc-c");
  auto multi_delete = multi_index->Delete("doc-c");
  Check(title_delete.ok, "delete doc-c from M4 title: " + title_delete.message);
  Check(body_delete.ok, "delete doc-c from M4 body: " + body_delete.message);
  Check(multi_delete.ok,
        "delete doc-c from M4 multi-field: " + multi_delete.message);
  FlushAll(native.handle, *title_index, *body_index, *multi_index, "mutated");
  CheckQueries(native.handle, *title_index, *body_index, *multi_index,
               "mutated", true);

  CheckCode(zvec_collection_optimize(native.handle),
            "optimize native multi-field collection");
  auto title_compact = title_index->CompactNow();
  auto body_compact = body_index->CompactNow();
  auto multi_compact = multi_index->CompactNow();
  Check(title_compact.ok, "compact M4 title: " + title_compact.message);
  Check(body_compact.ok, "compact M4 body: " + body_compact.message);
  Check(multi_compact.ok, "compact M4 multi-field: " + multi_compact.message);
  CheckQueries(native.handle, *title_index, *body_index, *multi_index,
               "after-compaction", true);

  CheckCode(zvec_collection_close(native.handle), "close native before reopen");
  native.handle = nullptr;
  CheckCode(
      zvec_collection_open((root / "native").c_str(), nullptr, &native.handle),
      "reopen native multi-field collection");
  title_index.reset();
  body_index.reset();
  multi_index.reset();
  title_status = m4::Index::Open((root / "m4-title").string(), title_options,
                                 &title_index);
  body_status =
      m4::Index::Open((root / "m4-body").string(), body_options, &body_index);
  multi_status = m4::Index::Open((root / "m4-multi").string(), multi_options,
                                 &multi_index);
  Check(title_status.ok, "reopen M4 title: " + title_status.message);
  Check(body_status.ok, "reopen M4 body: " + body_status.message);
  Check(multi_status.ok, "reopen M4 multi-field: " + multi_status.message);
  if (native.handle && title_status.ok && body_status.ok && multi_status.ok) {
    CheckQueries(native.handle, *title_index, *body_index, *multi_index,
                 "after-reopen", true);

    m4::Document batch_a;
    batch_a.fts_fields.emplace("title", "batchrecoveryalpha");
    batch_a.fts_fields.emplace("body", "批量恢复甲");
    m4::Document batch_b;
    batch_b.fts_fields.emplace("title", "batchrecoverybeta");
    batch_b.fts_fields.emplace("body", "批量恢复乙");
    const auto batch_status = multi_index->ApplyBatch(
        {{m4::MutationKind::UPSERT, "batch-wal-a", batch_a},
         {m4::MutationKind::UPSERT, "batch-wal-b", batch_b}});
    Check(batch_status.ok,
          "write multi-field batch WAL: " + batch_status.message);

    AddToAll(native.handle, *title_index, *body_index, *multi_index,
             {"wal-doc", "wal recovery title", "恢复词条"}, true);
    CheckCode(zvec_collection_flush(native.handle), "flush native WAL fixture");
    title_index.reset();
    body_index.reset();
    multi_index.reset();
    title_status = m4::Index::Open((root / "m4-title").string(), title_options,
                                   &title_index);
    body_status =
        m4::Index::Open((root / "m4-body").string(), body_options, &body_index);
    multi_status = m4::Index::Open((root / "m4-multi").string(), multi_options,
                                   &multi_index);
    Check(title_status.ok, "replay title WAL: " + title_status.message);
    Check(body_status.ok, "replay body WAL: " + body_status.message);
    Check(multi_status.ok, "replay multi-field WAL: " + multi_status.message);
    if (title_status.ok && body_status.ok && multi_status.ok) {
      CompareQuery(native.handle, *title_index, *body_index, *multi_index,
                   "title", "recovery", {"wal-doc"}, "after-wal-replay");
      CompareQuery(native.handle, *title_index, *body_index, *multi_index,
                   "body", "\"恢复词条\"", {"wal-doc"}, "after-wal-replay");
      m4::SearchResult batch_hits;
      const auto batch_title_status = multi_index->SearchFts(
          "title", "batchrecoveryalpha", 10, &batch_hits);
      Check(batch_title_status.ok && batch_hits.hits.size() == 1 &&
                batch_hits.hits.front().pk == "batch-wal-a",
            "replay multi-field batch WAL title field");
      const auto batch_body_status =
          multi_index->SearchFts("body", "\"批量恢复乙\"", 10, &batch_hits);
      Check(batch_body_status.ok && batch_hits.hits.size() == 1 &&
                batch_hits.hits.front().pk == "batch-wal-b",
            "replay multi-field batch WAL body field");
      multi_index->Delete("batch-wal-a");
      multi_index->Delete("batch-wal-b");
      multi_index->Flush();
    }
  }

  if (native.handle) {
    CheckCode(zvec_collection_close(native.handle), "close native collection");
    native.handle = nullptr;
  }
  title_index.reset();
  body_index.reset();
  multi_index.reset();
  Check(temporary.Remove(), "remove isolated native/M4 test data");
  std::cout << "native_multifield_tokenizer checks=" << checks
            << " failures=" << failures << '\n';
  return failures == 0 ? 0 : 1;
}
