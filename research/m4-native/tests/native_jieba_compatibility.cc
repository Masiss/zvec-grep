// Research-only Jieba analyzer differential. M4 and the native collection use
// isolated indexes but the same pinned tokenizer implementation and dict.
#include <filesystem>
#include <fstream>
#include <cstring>
#include <iostream>
#include <set>
#include <string>
#include <tuple>
#include <unistd.h>
#include <utility>
#include <vector>

#include "m4/index.h"
#include "../src/tokenizer.h"
#include "../src/util.h"
#include <zvec/ailego/encoding/json/mod_json_plus.h>
#include <zvec/c_api.h>
#include "db/index/column/fts_column/tokenizer/jieba_tokenizer.h"
#include "db/index/column/fts_column/tokenizer/token_filter.h"

#ifndef M4_ZVEC_JIEBA_DICT_DIR
#error "CMake must provide the native cppjieba dictionary path"
#endif

namespace fs = std::filesystem;
namespace {
int checks = 0;
int failures = 0;
using TokenSignature = std::tuple<std::string, uint32_t, uint32_t>;

void Check(bool ok, const std::string& message) {
  ++checks;
  if (!ok) {
    ++failures;
    std::cerr << "FAIL " << message << '\n';
  }
}

bool Code(zvec_error_code_t code, const std::string& message) {
  if (code == ZVEC_OK) return true;
  zvec_error_details_t detail{};
  zvec_get_last_error_details(&detail);
  std::cerr << "FAIL " << message << " code="
            << zvec_error_code_to_string(code) << " message="
            << (detail.message ? detail.message : "") << '\n';
  ++checks;
  ++failures;
  return false;
}

std::vector<TokenSignature> NativeTokens(const std::string& text) {
  static const zvec::fts::JiebaTokenizer tokenizer = [] {
    zvec::fts::JiebaTokenizer instance;
    zvec::ailego::JsonObject config;
    config.set(zvec::ailego::JsonString("jieba_dict_dir"),
               zvec::ailego::JsonValue(M4_ZVEC_JIEBA_DICT_DIR));
    const auto status = instance.init(config);
    if (!status.ok()) throw std::runtime_error(status.message());
    return instance;
  }();
  static const zvec::fts::LowercaseTokenFilter lowercase;
  const auto tokens = lowercase.filter(tokenizer.tokenize(text));
  std::vector<TokenSignature> result;
  result.reserve(tokens.size());
  for (const auto& token : tokens) {
    result.emplace_back(token.text, token.position, token.offset);
  }
  return result;
}

std::vector<TokenSignature> M4Tokens(const m4::Analyzer& analyzer,
                                     const std::string& text) {
  const auto tokens = analyzer.Tokenize(text);
  std::vector<TokenSignature> result;
  result.reserve(tokens.size());
  for (const auto& token : tokens) {
    result.emplace_back(token.term, token.pos, token.offset);
  }
  return result;
}

zvec_collection_t* OpenNative(const std::string& path) {
  auto* schema = zvec_collection_schema_create("zg_jieba_compat_research");
  auto* params = zvec_index_params_create(ZVEC_INDEX_TYPE_FTS);
  auto* filters = zvec_string_array_create(1);
  zvec_string_array_add(filters, 0, "lowercase");
  // Same configuration as zg-engine: jieba + lowercase, with the dictionary
  // registered through zvec-rust's equivalent SDK initialization hook.
  bool ok = Code(zvec_index_params_set_fts_params(params, "jieba", filters,
                                                  nullptr),
                 "configure Jieba FTS");
  auto* field = zvec_field_schema_create("text", ZVEC_DATA_TYPE_STRING,
                                         false, 0);
  ok = Code(zvec_field_schema_set_index_params(field, params),
            "attach Jieba FTS params") && ok;
  ok = Code(zvec_collection_schema_add_field(schema, field),
            "add FTS text field") && ok;
  zvec_collection_t* collection = nullptr;
  if (ok) {
    ok = Code(zvec_collection_create_and_open(path.c_str(), schema, nullptr,
                                              &collection),
              "create Jieba collection");
  }
  zvec_field_schema_destroy(field);
  zvec_index_params_destroy(params);
  zvec_string_array_destroy(filters);
  zvec_collection_schema_destroy(schema);
  return ok ? collection : nullptr;
}

bool NativeWrite(zvec_collection_t* collection, const std::string& pk,
                 const std::string& text, bool upsert) {
  auto* doc = zvec_doc_create();
  zvec_doc_set_pk(doc, pk.c_str());
  bool ok = Code(zvec_doc_add_field_by_value(
                    doc, "text", ZVEC_DATA_TYPE_STRING, text.data(),
                    text.size()),
                 "set native text " + pk);
  const zvec_doc_t* docs[] = {doc};
  zvec_write_result_t* results = nullptr;
  size_t count = 0;
  const auto status = upsert
      ? zvec_collection_upsert_with_results(collection, docs, 1, &results,
                                            &count)
      : zvec_collection_insert_with_results(collection, docs, 1, &results,
                                            &count);
  ok = Code(status, (upsert ? "upsert " : "insert ") + pk) && ok;
  ok = count == 1 && results && results[0].code == ZVEC_OK && ok;
  Check(ok, "successful native write " + pk);
  zvec_write_results_free(results, count);
  zvec_doc_destroy(doc);
  return ok;
}

bool NativeDelete(zvec_collection_t* collection, const std::string& pk) {
  const char* pks[] = {pk.c_str()};
  zvec_write_result_t* results = nullptr;
  size_t count = 0;
  bool ok = Code(zvec_collection_delete_with_results(collection, pks, 1,
                                                       &results, &count),
                 "delete native " + pk);
  ok = count == 1 && results && results[0].code == ZVEC_OK && ok;
  Check(ok, "successful native delete " + pk);
  zvec_write_results_free(results, count);
  return ok;
}

std::set<std::string> NativeSearch(zvec_collection_t* collection,
                                   const std::string& text) {
  auto* query = zvec_vector_query_create();
  bool ok = Code(zvec_vector_query_set_field_name(query, "text"),
                 "set native FTS field");
  ok = Code(zvec_vector_query_set_topk(query, 32), "set native top-k") && ok;
  auto* fts = zvec_fts_create();
  ok = Code(zvec_fts_set_query_string(fts, text.c_str()),
            "set native FTS query") && ok;
  ok = Code(zvec_vector_query_set_fts(query, fts),
            "attach native FTS query") && ok;
  auto* params = zvec_query_params_fts_create("OR");
  const auto params_status = zvec_vector_query_set_fts_params(query, params);
  if (params_status == ZVEC_OK) params = nullptr;
  ok = Code(params_status, "attach native FTS params") && ok;

  zvec_doc_t** docs = nullptr;
  size_t count = 0;
  if (ok) {
    ok = Code(zvec_collection_query(collection, query, &docs, &count),
              "native FTS query `" + text + "`") && ok;
  }
  std::set<std::string> pks;
  if (ok) {
    for (size_t i = 0; i < count; ++i) {
      const char* pk = zvec_doc_get_pk_pointer(docs[i]);
      if (pk) pks.emplace(pk);
    }
  }
  zvec_docs_free(docs, count);
  zvec_vector_query_destroy(query);
  if (params) zvec_query_params_fts_destroy(params);
  zvec_fts_destroy(fts);
  return pks;
}

void M4Write(m4::Index& index, const std::string& pk,
             const std::string& text) {
  m4::Document doc;
  doc.text = text;
  const auto status = index.Upsert(pk, doc);
  Check(status.ok, "M4 upsert " + pk + ": " + status.message);
}

std::set<std::string> M4Search(const m4::Index& index,
                               const std::string& text) {
  m4::SearchResult result;
  const auto status = index.SearchFts(text, 32, &result);
  Check(status.ok, "M4 FTS query `" + text + "`: " + status.message);
  std::set<std::string> pks;
  if (status.ok) {
    for (const auto& hit : result.hits) pks.insert(hit.pk);
  }
  return pks;
}

void CompareQuery(zvec_collection_t* native, const m4::Index& m4_index,
                  const std::string& query) {
  const auto expected = NativeSearch(native, query);
  const auto actual = M4Search(m4_index, query);
  if (expected != actual) {
    std::cerr << "DIFF query=`" << query << "` native={";
    for (const auto& pk : expected) std::cerr << pk << ",";
    std::cerr << "} m4={";
    for (const auto& pk : actual) std::cerr << pk << ",";
    std::cerr << "}\n";
  }
  Check(expected == actual, "query result parity for `" + query + "`");
}

std::string ReadFile(const fs::path& path) {
  std::ifstream in(path, std::ios::binary);
  return std::string(std::istreambuf_iterator<char>(in),
                     std::istreambuf_iterator<char>());
}

void WriteFile(const fs::path& path, const std::string& bytes) {
  std::ofstream out(path, std::ios::binary | std::ios::trunc);
  out.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
}

std::string ManifestWithoutIdentity(const std::string& bytes, uint32_t version) {
  if (bytes.size() < 16) return {};
  m4::BufReader body(bytes.data() + 8, bytes.size() - 16);
  for (int i = 0; i < 5; ++i) body.U64();
  body.String();
  const uint64_t count = body.Varint();
  for (uint64_t i = 0; i < count; ++i) {
    for (int j = 0; j < 5; ++j) body.U64();
    body.String();
  }
  if (!body.ok()) return {};
  std::string out(bytes.data(), 8 + body.offset());
  uint32_t magic = 0x4D344D46;
  std::memcpy(out.data(), &magic, sizeof(magic));
  std::memcpy(out.data() + 4, &version, sizeof(version));
  if (version == 2) out.push_back('\0');  // valid but empty identity field
  m4::BufWriter checksum;
  checksum.U64(m4::Fnv1a(out.data() + 8, out.size() - 8));
  out.append(checksum.data());
  return out;
}

void ReplaceManifests(const fs::path& dir, const std::string& bytes) {
  WriteFile(dir / "manifest", bytes);
  WriteFile(dir / "manifest.bak", bytes);
}
}  // namespace

int main() {
  Check(m4::Sha256("abc") ==
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        "SHA-256 implementation matches the standard abc vector");
  std::string temporary = (fs::temp_directory_path() / "m4-jieba-compat-XXXXXX").string();
  std::vector<char> temp_buffer(temporary.begin(), temporary.end());
  temp_buffer.push_back('\0');
  char* root = mkdtemp(temp_buffer.data());
  if (!root) {
    std::cerr << "FAIL mkdtemp\n";
    return 2;
  }
  const fs::path base(root);
  const std::string dict_dir = M4_ZVEC_JIEBA_DICT_DIR;
  unsetenv("ZVEC_JIEBA_DICT_DIR");
  zvec_set_default_jieba_dict_dir(dict_dir.c_str());
  if (!Code(zvec_initialize(nullptr), "initialize native Zvec")) return 3;

  m4::Options options;
  options.fts_field = "text";
  options.fts_tokenizer_name = "jieba";
  options.jieba_dict_dir = dict_dir;

  // Missing and invalid dictionary configuration must fail explicitly; it may
  // not silently route to M4's standard tokenizer or an environment fallback.
  m4::Options missing_options = options;
  missing_options.jieba_dict_dir.clear();
  std::unique_ptr<m4::Index> rejected;
  auto status = m4::Index::Open((base / "missing-dict").string(),
                                missing_options, &rejected);
  Check(!status.ok && status.message.find("explicit jieba_dict_dir") !=
                          std::string::npos,
        "missing Jieba dictionary rejected explicitly");
  m4::Options invalid_options = options;
  invalid_options.jieba_dict_dir = (base / "not-a-dictionary").string();
  status = m4::Index::Open((base / "invalid-dict").string(), invalid_options,
                           &rejected);
  Check(!status.ok && status.message.find("JiebaTokenizer identity requires") !=
                          std::string::npos,
        "invalid Jieba dictionary is rejected during identity preflight");

  // Independent initialization with the exact native config must yield
  // identical terms, sequence positions, and native byte offsets.
  m4::Analyzer analyzer_a;
  m4::Analyzer analyzer_b;
  const auto init_a = analyzer_a.Init("jieba", dict_dir);
  const auto init_b = analyzer_b.Init("jieba", dict_dir);
  Check(init_a.ok, "initialize M4 analyzer A: " + init_a.message);
  Check(init_b.ok, "initialize M4 analyzer B: " + init_b.message);
  if (!init_a.ok || !init_b.ok) return 4;

  const std::vector<std::pair<std::string, std::string>> fixtures = {
      {"ascii", "The QUICK brown fox jumps over quick."},
      {"punctuation", "foo-bar rock'n'roll foo_bar 3.14"},
      {"chinese", "深度学习模型"},
      {"mixed", "HTTPParser 深度学习 API"},
      {"unicode", "Café naïve Straße 中文测试"},
      {"phrase", "orchard parser vocabulary"},
      {"to-update", "obsolete apple"},
      {"to-delete", "retire orchard"},
  };
  for (const auto& [label, text] : fixtures) {
    const auto native_tokens = NativeTokens(text);
    const auto m4_tokens = M4Tokens(analyzer_a, text);
    Check(native_tokens == m4_tokens,
          label + " token text/position/offset parity");
    Check(m4_tokens == M4Tokens(analyzer_b, text),
          label + " deterministic repeated initialization");
    for (size_t i = 0; i < m4_tokens.size(); ++i) {
      Check(std::get<1>(m4_tokens[i]) == i,
            label + " contiguous output-sequence position " +
                std::to_string(i));
    }
  }

  auto* native = OpenNative((base / "native").string());
  std::unique_ptr<m4::Index> m4_index;
  status = m4::Index::Open((base / "m4").string(), options, &m4_index);
  Check(status.ok, "open M4 Jieba index: " + status.message);
  if (!native || !status.ok) return 5;

  for (const auto& [pk, text] : fixtures) {
    NativeWrite(native, pk, text, false);
    M4Write(*m4_index, pk, text);
  }
  Code(zvec_collection_flush(native), "flush native initial docs");
  status = m4_index->Flush();
  Check(status.ok, "flush M4 initial docs: " + status.message);

  for (const auto& query : {"PARSER", "foo-bar", "深度", "学", "API",
                            "café", "\"orchard parser\"",
                            "\"深度学习模型\"", "\"foo-bar\"",
                            "\"Café naïve Straße\"",
                            "\"HTTPParser 深度学习 API\""}) {
    CompareQuery(native, *m4_index, query);
  }
  Check(NativeSearch(native, "\"orchard parser\"").count("phrase") == 1,
        "native phrase fixture matches as adjacent Jieba positions");

  // Update/delete and compaction must preserve the analyzer contract without
  // changing BM25: differential compares candidate identities, not scores.
  NativeWrite(native, "to-update", "fresh blossom", true);
  M4Write(*m4_index, "to-update", "fresh blossom");
  NativeDelete(native, "to-delete");
  status = m4_index->Delete("to-delete");
  Check(status.ok, "M4 delete to-delete: " + status.message);
  Code(zvec_collection_flush(native), "flush native update/delete");
  status = m4_index->Flush();
  Check(status.ok, "flush M4 update/delete: " + status.message);
  for (const auto& query : {"obsolete", "fresh", "retire", "orchard"}) {
    CompareQuery(native, *m4_index, query);
  }

  status = m4_index->CompactNow();
  Check(status.ok, "M4 Jieba compaction: " + status.message);
  m4_index.reset();
  status = m4::Index::Open((base / "m4").string(), options, &m4_index);
  Check(status.ok, "reopen compacted M4 Jieba index: " + status.message);
  if (status.ok) {
    for (const auto& query : {"fresh", "深度", "\"orchard parser\""}) {
      CompareQuery(native, *m4_index, query);
    }

    // Simulate a crash with a valid Jieba upsert only in the WAL; recovery must
    // re-tokenize the payload with the same configured analyzer.
    M4Write(*m4_index, "wal-recovery", "recovered orchard parser");
    m4_index.reset();
    status = m4::Index::Open((base / "m4").string(), options, &m4_index);
    Check(status.ok, "reopen M4 Jieba index with WAL recovery: " +
                        status.message);
    if (status.ok) {
      const auto recovered = M4Search(*m4_index, "recovered");
      Check(recovered.count("wal-recovery") == 1,
            "WAL recovery reuses the configured Jieba analyzer");
      status = m4_index->CompactNow();
      Check(status.ok, "compact WAL-recovered Jieba index: " + status.message);
      m4_index.reset();
      status = m4::Index::Open((base / "m4").string(), options, &m4_index);
      Check(status.ok, "reopen after WAL recovery and compaction: " +
                          status.message);
      if (status.ok) {
        Check(M4Search(*m4_index, "recovered").count("wal-recovery") == 1,
              "recovered Jieba term survives compact/reopen");
      }
    }
  }

  // Analyzer identity is content-addressed: a relocated copy with identical
  // files reopens and replays pending WAL, while changed content/options fail
  // before replay. The failed attempts must leave the original WAL recoverable.
  const fs::path relocated_dict = base / "relocated-dict";
  fs::create_directories(relocated_dict);
  for (const char* name : {"jieba.dict.utf8", "hmm_model.utf8"})
    fs::copy_file(fs::path(dict_dir) / name, relocated_dict / name);
  const fs::path identity_index_dir = base / "identity-index";
  std::unique_ptr<m4::Index> identity_index;
  status = m4::Index::Open(identity_index_dir.string(), options, &identity_index);
  Check(status.ok, "create fresh identity-pinned Jieba index: " + status.message);
  if (status.ok) M4Write(*identity_index, "identity-wal", "identity recovered 深度学习");
  identity_index.reset();

  m4::Options relocated_options = options;
  relocated_options.jieba_dict_dir = relocated_dict.string();
  status = m4::Index::Open(identity_index_dir.string(), relocated_options,
                           &identity_index);
  Check(status.ok, "same dictionary contents at a different path are equivalent: " +
                       status.message);
  if (status.ok) {
    Check(M4Search(*identity_index, "identity").count("identity-wal") == 1,
          "same-content relocated dictionary replays the pending WAL");
  }
  identity_index.reset();

  {
    std::ofstream append(relocated_dict / "jieba.dict.utf8", std::ios::binary | std::ios::app);
    append << "指纹变化词条 100000 n\n";
  }
  status = m4::Index::Open(identity_index_dir.string(), relocated_options,
                           &identity_index);
  Check(!status.ok && status.message.find("analyzer identity mismatch") != std::string::npos,
        "changed dictionary content rejected before opening/replaying WAL");
  m4::Options wrong_analyzer = options;
  wrong_analyzer.fts_tokenizer_name = "standard";
  status = m4::Index::Open(identity_index_dir.string(), wrong_analyzer,
                           &identity_index);
  Check(!status.ok && status.message.find("analyzer identity mismatch") != std::string::npos,
        "different tokenizer rejected before WAL replay");
  status = m4::Index::Open(identity_index_dir.string(), options, &identity_index);
  Check(status.ok, "correct identity still recovers after rejected mismatches: " +
                       status.message);
  if (status.ok) {
    Check(M4Search(*identity_index, "identity").count("identity-wal") == 1,
          "mismatch attempts did not consume or mutate pending WAL");
    ::setenv("M4_FAULT", "after_manifest", 1);
    const auto fault_status = identity_index->CompactNow();
    ::unsetenv("M4_FAULT");
    Check(!fault_status.ok && fault_status.message.find("after manifest") != std::string::npos,
          "fault injection stops after atomic manifest publish");
  }
  identity_index.reset();
  status = m4::Index::Open(identity_index_dir.string(), options, &identity_index);
  Check(status.ok, "same identity recovers after post-manifest fault: " + status.message);
  if (status.ok)
    Check(M4Search(*identity_index, "identity").count("identity-wal") == 1,
          "post-manifest recovery retains data and analyzer identity");
  identity_index.reset();

  // Missing identity (v2 empty field), legacy v1 without identity, and corrupt
  // metadata have explicit reject/rebuild behavior rather than guessed config.
  const fs::path metadata_dir = base / "metadata-policy";
  m4::Options standard_options;
  std::unique_ptr<m4::Index> metadata_index;
  status = m4::Index::Open(metadata_dir.string(), standard_options, &metadata_index);
  Check(status.ok, "create metadata-policy fixture: " + status.message);
  metadata_index.reset();
  const std::string manifest = ReadFile(metadata_dir / "manifest");
  const std::string missing_identity = ManifestWithoutIdentity(manifest, 2);
  Check(!missing_identity.empty(), "construct valid v2 manifest with missing identity");
  ReplaceManifests(metadata_dir, missing_identity);
  status = m4::Index::Open(metadata_dir.string(), standard_options, &metadata_index);
  Check(!status.ok && status.message.find("no analyzer identity") != std::string::npos,
        "v2 manifest with missing identity is rejected with rebuild guidance");

  const std::string legacy_manifest = ManifestWithoutIdentity(manifest, 1);
  Check(!legacy_manifest.empty(), "construct valid legacy v1 manifest");
  ReplaceManifests(metadata_dir, legacy_manifest);
  status = m4::Index::Open(metadata_dir.string(), standard_options, &metadata_index);
  Check(!status.ok && status.message.find("legacy/corrupt index") != std::string::npos,
        "legacy v1 index without analyzer identity requires rebuild");

  const fs::path corrupt_dir = base / "corrupt-metadata";
  fs::copy(metadata_dir, corrupt_dir, fs::copy_options::recursive);
  std::string corrupt = manifest;
  corrupt.back() ^= 0x40;
  ReplaceManifests(corrupt_dir, corrupt);
  status = m4::Index::Open(corrupt_dir.string(), standard_options, &metadata_index);
  Check(!status.ok && status.message.find("no valid manifest") != std::string::npos,
        "corrupt manifest and backup are rejected, not reinitialized");

  std::cout << "jieba_compatibility checks=" << checks
            << " failures=" << failures << '\n';
  m4_index.reset();
  zvec_collection_close(native);
  fs::remove_all(base);
  return failures ? 1 : 0;
}
