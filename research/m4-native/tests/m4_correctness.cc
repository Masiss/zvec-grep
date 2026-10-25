// Correctness oracle for the M4 research prototype.
//
// The reference is an exhaustive full-scan of the same live document set with
// the same tokenizer and BM25 formula. It intentionally does not share any
// index code with the implementation.

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <limits>
#include <lz4.h>
#include <map>
#include <optional>
#include <random>
#include <set>
#include <string>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "../src/format.h"
#include "../src/tokenizer.h"
#include "../src/util.h"
#include "m4/index.h"

namespace fs = std::filesystem;
using m4::CompareOp;
using m4::Document;
using m4::Index;
using m4::ScalarValue;
using m4::Token;

namespace {

int g_failures = 0;
int g_checks = 0;

void Check(bool ok, const std::string &what) {
  ++g_checks;
  if (!ok) {
    ++g_failures;
    std::fprintf(stderr, "FAIL: %s\n", what.c_str());
  }
}

uint32_t WalSequenceChecksumForTest(uint64_t seq, const char *payload,
                                    size_t size) {
  uint8_t seq_bytes[sizeof(seq)];
  for (size_t i = 0; i < sizeof(seq); ++i) {
    seq_bytes[i] = static_cast<uint8_t>(seq >> (i * 8));
  }
  uint64_t checksum = m4::Fnv1aInit();
  m4::Fnv1aUpdate(&checksum, seq_bytes, sizeof(seq_bytes));
  m4::Fnv1aUpdate(&checksum, payload, size);
  return static_cast<uint32_t>(checksum & 0xffffffffu);
}

uint32_t WalHeaderChecksumForTest(uint32_t magic, uint32_t len, uint64_t seq,
                                  uint32_t original_len) {
  uint8_t metadata[20];
  size_t offset = 0;
  const auto append_le = [&metadata, &offset](uint64_t value, size_t width) {
    for (size_t i = 0; i < width; ++i) {
      metadata[offset++] = static_cast<uint8_t>(value >> (i * 8));
    }
  };
  append_le(magic, sizeof(magic));
  append_le(len, sizeof(len));
  append_le(seq, sizeof(seq));
  append_le(original_len, sizeof(original_len));
  return static_cast<uint32_t>(m4::Fnv1a(metadata, sizeof(metadata)) &
                               0xffffffffu);
}

void RewriteWalFrameAsLegacy(const fs::path &wal_path, bool compressed) {
  std::string bytes;
  {
    std::ifstream input(wal_path, std::ios::binary);
    bytes.assign(std::istreambuf_iterator<char>(input),
                 std::istreambuf_iterator<char>());
  }
  const size_t header_size = compressed ? 28u : 24u;
  if (bytes.size() < header_size) {
    Check(false, "new WAL fixture has a complete frame to rewrite");
    return;
  }
  m4::BufReader reader(bytes.data(), bytes.size());
  (void)reader.U32();
  const uint32_t len = reader.U32();
  (void)reader.U64();
  uint32_t original_len = len;
  uint32_t old_magic = 0x4D34574C;
  const char *payload = bytes.data() + header_size;
  size_t payload_size = len;
  std::string decoded;
  if (compressed) {
    old_magic = 0x4D34575A;
    original_len = reader.U32();
    (void)reader.U32(); // New header checksum.
    (void)reader.U32(); // New payload checksum.
    if (len > static_cast<uint32_t>(std::numeric_limits<int>::max()) ||
        original_len > (8u << 20) || bytes.size() - header_size < len) {
      Check(false, "compressed WAL fixture lengths are bounded");
      return;
    }
    decoded.resize(original_len);
    const int result =
        LZ4_decompress_safe(payload, decoded.data(), static_cast<int>(len),
                            static_cast<int>(original_len));
    if (result < 0 || static_cast<uint32_t>(result) != original_len) {
      Check(false, "new compressed WAL fixture decodes before legacy rewrite");
      return;
    }
    payload = decoded.data();
    payload_size = decoded.size();
  } else {
    (void)reader.U32(); // New header checksum.
    (void)reader.U32(); // New payload checksum.
    if (bytes.size() - header_size < len) {
      Check(false, "raw WAL fixture payload is complete");
      return;
    }
  }
  const uint32_t checksum =
      static_cast<uint32_t>(m4::Fnv1a(payload, payload_size) & 0xffffffffu);
  m4::BufWriter magic_bytes;
  magic_bytes.U32(old_magic);
  std::string legacy(bytes.data(), bytes.data() + (compressed ? 20 : 16));
  legacy.replace(0, 4, magic_bytes.data());
  m4::BufWriter checksum_bytes;
  checksum_bytes.U32(checksum);
  legacy.append(checksum_bytes.data());
  legacy.append(bytes.data() + header_size, len);
  legacy.append(bytes.data() + header_size + len,
                bytes.size() - header_size - len);
  std::ofstream output(wal_path, std::ios::binary | std::ios::trunc);
  output.write(legacy.data(), static_cast<std::streamsize>(legacy.size()));
  output.flush();
  Check(output.good(), "write legacy WAL frame fixture");
}

void TestSha256BlockBoundaries() {
  const auto check_repeated_a = [](size_t bytes, const char *expected) {
    Check(m4::Sha256(std::string(bytes, 'a')) == expected,
          "SHA-256 repeated-a input length " + std::to_string(bytes));
  };
  Check(m4::Sha256("") ==
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "SHA-256 empty input");
  Check(m4::Sha256("abc") ==
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        "SHA-256 short known vector");
  check_repeated_a(55, "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318");
  check_repeated_a(56, "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a");
  check_repeated_a(63, "7d3e74a05d7db15bce4ad9ec0658ea98e3f06eeecf16b4c6fff2da457ddc2f34");
  check_repeated_a(64, "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb");
  check_repeated_a(65, "635361c48bb9eab14198e76ea8ab7f1a41685d6ad62aa9146d301d4f17eb0ae0");
  check_repeated_a(127, "c57e9278af78fa3cab38667bef4ce29d783787a2f731d4e12200270f0c32320a");
  check_repeated_a(128, "6836cf13bac400e9105071cd6af47084dfacad4e5e302c94bfed24e013afb73e");
  check_repeated_a(129, "c12cb024a2e5551cca0e08fce8f1c5e314555cc3fef6329ee994a3db752166ae");
  check_repeated_a(1u << 20, "9bc1b2a288b26af7257a36277ae3816a7d4f16e89c1e7e77d0a5c48bad62b360");
}

struct RefDoc {
  std::string pk;
  std::string text;
  std::vector<ScalarValue> values;
};

struct RefState {
  std::unordered_map<std::string, RefDoc> docs;
  uint64_t total_tokens{0};
};

struct RefHit {
  std::string pk;
  double score{0.0};
};

std::vector<Token> Tok(const std::string& text) { return m4::Tokenize(text); }

std::unordered_map<std::string, std::vector<uint32_t>> Positions(
    const std::vector<Token>& tokens) {
  std::unordered_map<std::string, std::vector<uint32_t>> pos;
  for (const auto& t : tokens) pos[t.term].push_back(t.pos);
  return pos;
}

bool PhraseOk(const std::vector<std::vector<uint32_t>>& positions) {
  if (positions.empty()) return false;
  std::vector<std::unordered_set<uint32_t>> sets;
  for (const auto& p : positions) sets.emplace_back(p.begin(), p.end());
  for (uint32_t start : positions[0]) {
    bool ok = true;
    for (size_t i = 1; i < sets.size(); ++i) {
      if (sets[i].count(start + static_cast<uint32_t>(i)) == 0) {
        ok = false;
        break;
      }
    }
    if (ok) return true;
  }
  return false;
}

struct RefQuery {
  std::vector<std::string> terms;
  bool and_mode{false};
  std::vector<std::vector<std::string>> phrases;
};

RefQuery MakeRefQuery(const std::vector<std::string>& terms, bool and_mode,
                      const std::vector<std::vector<std::string>>& phrases = {}) {
  RefQuery q;
  for (const auto& t : terms) {
    if (std::find(q.terms.begin(), q.terms.end(), t) == q.terms.end()) {
      q.terms.push_back(t);
    }
  }
  q.and_mode = and_mode || !phrases.empty();
  q.phrases = phrases;
  for (const auto& ph : phrases) {
    for (const auto& t : ph) {
      if (std::find(q.terms.begin(), q.terms.end(), t) == q.terms.end()) {
        q.terms.push_back(t);
      }
    }
  }
  return q;
}

std::vector<RefHit> RefSearchFts(const RefState& ref, const RefQuery& q,
                                 uint32_t top_k, double k1, double b) {
  const double n = static_cast<double>(ref.docs.size());
  if (n <= 0 || q.terms.empty()) return {};
  const double avg_dl =
      ref.total_tokens == 0 ? 1.0 : static_cast<double>(ref.total_tokens) / n;
  std::unordered_map<std::string, uint64_t> df;
  for (const auto& [pk, d] : ref.docs) {
    (void)pk;
    std::unordered_set<std::string> uniq;
    for (const auto& t : Tok(d.text)) uniq.insert(t.term);
    for (const auto& t : uniq) ++df[t];
  }
  std::vector<RefHit> hits;
  for (const auto& [pk, d] : ref.docs) {
    const auto tokens = Tok(d.text);
    const auto pos = Positions(tokens);
    bool all = true;
    double score = 0.0;
    for (const auto& term : q.terms) {
      const auto it = pos.find(term);
      if (it == pos.end()) {
        all = false;
        if (q.and_mode) break;
        continue;
      }
      const double tf = static_cast<double>(it->second.size());
      const double dl = static_cast<double>(tokens.size());
      const double dfv = static_cast<double>(df[term]);
      const double idf =
          std::log((n - dfv + 0.5) / (dfv + 0.5) + 1.0);
      score += idf * tf * (k1 + 1.0) /
               (tf + k1 * (1.0 - b + b * dl / avg_dl));
    }
    if (q.and_mode && !all) continue;
    bool phrase_ok = true;
    for (const auto& ph : q.phrases) {
      std::vector<std::vector<uint32_t>> pp;
      for (const auto& t : ph) {
        const auto it = pos.find(t);
        if (it == pos.end()) {
          phrase_ok = false;
          break;
        }
        pp.push_back(it->second);
      }
      if (!phrase_ok || !PhraseOk(pp)) {
        phrase_ok = false;
        break;
      }
    }
    if (!phrase_ok) continue;
    if (score > 0.0) hits.push_back(RefHit{pk, score});
  }
  std::sort(hits.begin(), hits.end(), [](const RefHit& a, const RefHit& b) {
    if (a.score != b.score) return a.score > b.score;
    return a.pk < b.pk;
  });
  if (hits.size() > top_k) hits.resize(top_k);
  return hits;
}

void CheckFts(const Index& index, const RefState& ref, const std::string& query,
              const RefQuery& rq, uint32_t top_k, const std::string& label) {
  m4::SearchResult res;
  m4::Status s = index.SearchFts(query, top_k, &res);
  Check(s.ok, label + ": search status");
  if (!s.ok) return;
  auto want_all = RefSearchFts(ref, rq, std::numeric_limits<uint32_t>::max(),
                               1.2, 0.75);
  const size_t expected_n = std::min<size_t>(top_k, want_all.size());
  Check(res.hits.size() == expected_n,
        label + ": hit count " + std::to_string(res.hits.size()) + " vs " +
            std::to_string(expected_n));
  std::unordered_map<std::string, double> ref_score;
  for (const auto& h : want_all) ref_score[h.pk] = h.score;
  std::set<std::string> got_pks;
  for (const auto& h : res.hits) got_pks.insert(h.pk);
  for (const auto& h : res.hits) {
    const auto it = ref_score.find(h.pk);
    Check(it != ref_score.end(), label + ": unexpected pk " + h.pk);
  }
  // Every reference hit strictly better than the returned cutoff must appear.
  if (!res.hits.empty()) {
    const double cutoff = res.hits.back().score;
    for (const auto& h : want_all) {
      if (h.score > cutoff + 1e-3 && got_pks.count(h.pk) == 0) {
        Check(false, label + ": missing better hit " + h.pk);
      }
    }
  }
}

std::set<std::string> RefInvert(const RefState& ref, const std::string& field,
                                const std::string& value, CompareOp op) {
  std::set<std::string> out;
  auto match = [&](const std::string& v) {
    switch (op) {
      case CompareOp::EQ: return v == value;
      case CompareOp::LT: return v < value;
      case CompareOp::LE: return v <= value;
      case CompareOp::GT: return v > value;
      case CompareOp::GE: return v >= value;
    }
    return false;
  };
  for (const auto& [pk, d] : ref.docs) {
    for (const auto& v : d.values) {
      if (v.field == field && match(v.value)) {
        out.insert(pk);
        break;
      }
    }
  }
  return out;
}

void CheckInvert(const Index& index, const RefState& ref,
                 const std::string& field, const std::string& value,
                 CompareOp op, const std::string& label) {
  m4::SearchResult res;
  m4::Status s = index.SearchInvert(field, value, op, &res);
  Check(s.ok, label + ": invert status");
  if (!s.ok) return;
  std::set<std::string> got;
  for (const auto& h : res.hits) got.insert(h.pk);
  auto want = RefInvert(ref, field, value, op);
  Check(got == want, label + ": invert set size " + std::to_string(got.size()) +
                         " vs " + std::to_string(want.size()));
  if (got != want) {
    std::fprintf(stderr, "  got-only:");
    for (const auto& p : got) {
      if (want.count(p) == 0) std::fprintf(stderr, " %s", p.c_str());
    }
    std::fprintf(stderr, "\n  want-only:");
    for (const auto& p : want) {
      if (got.count(p) == 0) std::fprintf(stderr, " %s", p.c_str());
    }
    std::fprintf(stderr, "\n");
  }
}

void AddDoc(RefState* ref, const std::string& pk, const Document& doc) {
  auto it = ref->docs.find(pk);
  if (it != ref->docs.end()) {
    ref->total_tokens -= Tok(it->second.text).size();
    ref->docs.erase(it);
  }
  ref->docs[pk] = RefDoc{pk, doc.text, doc.values};
  ref->total_tokens += Tok(doc.text).size();
}

void DeleteDoc(RefState* ref, const std::string& pk) {
  auto it = ref->docs.find(pk);
  if (it == ref->docs.end()) return;
  ref->total_tokens -= Tok(it->second.text).size();
  ref->docs.erase(it);
}

std::string RandomText(std::mt19937* rng, int vocab) {
  std::uniform_int_distribution<int> len(0, 30);
  std::uniform_int_distribution<int> word(0, vocab - 1);
  std::string out;
  const int n = len(*rng);
  for (int i = 0; i < n; ++i) {
    if (i) out.push_back(' ');
    out += "w" + std::to_string(word(*rng));
  }
  return out;
}

Document RandomDoc(std::mt19937* rng, int vocab) {
  Document d;
  d.text = RandomText(rng, vocab);
  const char* statuses[] = {"active", "pending", "done"};
  const char* regions[] = {"a", "b", "c"};
  d.values.push_back(ScalarValue{"status", statuses[(*rng)() % 3]});
  d.values.push_back(ScalarValue{"region", regions[(*rng)() % 3]});
  return d;
}

void RunRandomized(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.max_delta_docs = 64;
  opt.max_delta_bytes = 4 << 20;
  opt.max_runs_per_level = 3;
  opt.max_total_runs = 7;
  opt.stats_log_threshold = 2048;
  opt.fsync_wal = false;

  RefState ref;
  std::mt19937 rng(20261017);
  {
    std::unique_ptr<Index> index;
    m4::Status s = Index::Open(dir, opt, &index);
    Check(s.ok, "open randomized index: " + s.message);
    if (!s.ok) return;

    const int base = 1200;
    for (int i = 0; i < base; ++i) {
      const std::string pk = "doc-" + std::to_string(i);
      Document d = RandomDoc(&rng, 120);
      AddDoc(&ref, pk, d);
      s = index->Upsert(pk, d);
      Check(s.ok, "base upsert: " + s.message);
      if ((i + 1) % 137 == 0) {
        s = index->Flush();
        Check(s.ok, "base flush: " + s.message);
      }
    }
    s = index->Flush();
    Check(s.ok, "base final flush: " + s.message);

    for (int round = 0; round < 500; ++round) {
      const int action = static_cast<int>(rng() % 100);
      if (action < 55) {
        const std::string pk = "doc-" + std::to_string(rng() % base);
        Document d = RandomDoc(&rng, 120);
        AddDoc(&ref, pk, d);
        s = index->Upsert(pk, d);
        Check(s.ok, "mutation upsert: " + s.message);
      } else if (action < 80) {
        const std::string pk = "new-" + std::to_string(round);
        Document d = RandomDoc(&rng, 120);
        AddDoc(&ref, pk, d);
        s = index->Upsert(pk, d);
        Check(s.ok, "mutation new: " + s.message);
      } else {
        const std::string pk =
            (rng() % 2 == 0) ? "doc-" + std::to_string(rng() % base)
                             : "new-" + std::to_string(rng() % (round + 1));
        DeleteDoc(&ref, pk);
        s = index->Delete(pk);
        Check(s.ok, "mutation delete: " + s.message);
      }
      if (round % 23 == 0) {
        s = index->Flush();
        Check(s.ok, "mutation flush: " + s.message);
      }
    }
    s = index->Flush();
    Check(s.ok, "final mutation flush: " + s.message);

    auto verify = [&](const std::string& phase) {
      for (const std::string term : {"w0", "w7", "w42", "w119"}) {
        CheckFts(*index, ref, term, MakeRefQuery({term}, false), 10,
                 phase + " term " + term);
        CheckFts(*index, ref, term + " AND w1",
                 MakeRefQuery({term, "w1"}, true), 10,
                 phase + " AND " + term);
      }
      CheckFts(*index, ref, "w3 w5 w9",
               MakeRefQuery({"w3", "w5", "w9"}, false), 10, phase + " OR");
      CheckInvert(*index, ref, "status", "active", CompareOp::EQ,
                  phase + " invert eq");
      CheckInvert(*index, ref, "status", "pending", CompareOp::LT,
                  phase + " invert lt");
      CheckInvert(*index, ref, "region", "b", CompareOp::GE,
                  phase + " invert ge");
    };
    verify("before-compaction");

    s = index->CompactNow();
    Check(s.ok, "compact now: " + s.message);
    verify("after-compaction");
    Check(index->GetStats().runs <= 1, "compact leaves one run");
  }

  // Reopen from the committed state.
  {
    std::unique_ptr<Index> index;
    m4::Status s = Index::Open(dir, opt, &index);
    Check(s.ok, "reopen randomized: " + s.message);
    if (!s.ok) return;
    CheckFts(*index, ref, "w11", MakeRefQuery({"w11"}, false), 10,
             "reopened term");
    CheckInvert(*index, ref, "status", "done", CompareOp::EQ,
                "reopened invert");
  }
}

void TestTypedUint32Scalar(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options options;
  options.fsync_wal = false;
  std::map<std::string, uint32_t> reference;
  auto put = [&](Index& index, const std::string& pk,
                 const std::optional<uint32_t>& value) {
    Document doc;
    doc.text = "scalar fixture";
    if (value) {
      doc.values.push_back(ScalarValue::Unsigned32("file_id", *value));
      reference[pk] = *value;
    } else {
      reference.erase(pk);
    }
    Check(index.Upsert(pk, doc).ok, "typed u32 upsert " + pk);
  };
  auto verify = [&](Index& index, const std::string& phase) {
    const uint32_t max_value = std::numeric_limits<uint32_t>::max();
    for (uint32_t value : {0u, 1u, 9u, 10u, 42u, 2147483648u, max_value}) {
      m4::SearchResult result;
      const auto status = index.SearchInvertUint32("file_id", value, &result);
      Check(status.ok, phase + " typed u32 EQ status");
      std::set<std::string> actual;
      for (const auto& hit : result.hits) actual.insert(hit.pk);
      std::set<std::string> expected;
      for (const auto& [pk, stored] : reference) {
        if (stored == value) expected.insert(pk);
      }
      Check(actual == expected, phase + " typed u32 EQ PK set");
      Check(actual.size() == result.hits.size(), phase + " EQ no duplicates");
    }
    for (const auto& values : std::vector<std::vector<uint32_t>>{
             {}, {0u}, {1u, 1u, max_value, 42u, 42u},
             {0u, 9u, 10u, 2147483648u, max_value}}) {
      m4::SearchResult result;
      const auto status = index.SearchInvertUint32In("file_id", values, &result);
      Check(status.ok, phase + " typed u32 IN status");
      std::set<std::string> actual;
      for (const auto& hit : result.hits) actual.insert(hit.pk);
      std::set<std::string> expected;
      for (const auto& [pk, stored] : reference) {
        if (std::find(values.begin(), values.end(), stored) != values.end()) {
          expected.insert(pk);
        }
      }
      Check(actual == expected, phase + " typed u32 IN PK set");
      Check(actual.size() == result.hits.size(), phase + " IN no duplicates");
    }
  };

  std::unique_ptr<Index> index;
  Check(Index::Open(dir, options, &index).ok, "typed u32 open");
  if (!index) return;
  put(*index, "pk-zero", 0u);
  put(*index, "pk-one", 1u);
  put(*index, "pk-nine", 9u);
  put(*index, "pk-ten", 10u);
  put(*index, "pk-half", 2147483648u);
  put(*index, "pk-max", std::numeric_limits<uint32_t>::max());
  put(*index, "pk-dup-a", 42u);
  put(*index, "pk-dup-b", 42u);
  put(*index, "pk-missing", std::nullopt);
  Check(index->Flush().ok, "typed u32 initial flush");
  verify(*index, "typed u32 initial");
  m4::SearchResult ordered_in;
  const auto ordered_status =
      index->SearchInvertUint32In("file_id", {1u, 42u}, &ordered_in);
  Check(ordered_status.ok && ordered_in.hits.size() == 3 &&
            std::vector<std::string>{ordered_in.hits[0].pk,
                                     ordered_in.hits[1].pk,
                                     ordered_in.hits[2].pk} ==
                std::vector<std::string>{"pk-one", "pk-dup-a", "pk-dup-b"},
        "typed u32 IN preserves exact PK order with single-lookup result construction");

  put(*index, "pk-one", std::numeric_limits<uint32_t>::max());
  Check(index->Delete("pk-nine").ok, "typed u32 delete");
  reference.erase("pk-nine");
  verify(*index, "typed u32 after update/delete before flush");
  Check(index->Flush().ok, "typed u32 mutation flush");
  verify(*index, "typed u32 after flush");
  Check(index->CompactNow().ok, "typed u32 compact");
  verify(*index, "typed u32 after compact");
  index.reset();

  Check(Index::Open(dir, options, &index).ok, "typed u32 reopen");
  if (index) verify(*index, "typed u32 after reopen");
}

void TestTypedArrayUint32(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options options;
  options.fsync_wal = false;
  using Values = std::optional<std::vector<uint32_t>>;
  std::map<std::string, Values> reference;
  auto put = [&](Index& index, const std::string& pk, Values values) {
    Document doc;
    if (values) {
      doc.array_uint32_values.push_back({"ancestor_directory_ids", *values});
    }
    reference[pk] = std::move(values);
    Check(index.Upsert(pk, doc).ok, "typed array u32 upsert " + pk);
  };
  auto verify = [&](Index& index, const std::string& phase) {
    const uint32_t max_value = std::numeric_limits<uint32_t>::max();
    const std::vector<std::vector<uint32_t>> queries = {
        {}, {17u}, {17u, 18u}, {17u, 17u, 18u}, {0u, max_value},
        {max_value}, {999u}};
    for (bool negate : {false, true}) {
      for (const auto& operands : queries) {
        m4::SearchResult result;
        const auto status = negate
            ? index.SearchArrayUint32NotContainsAny(
                  "ancestor_directory_ids", operands, &result)
            : index.SearchArrayUint32ContainsAny(
                  "ancestor_directory_ids", operands, &result);
        Check(status.ok, phase + " typed array query status: " + status.message);
        std::set<std::string> actual;
        for (const auto& hit : result.hits) actual.insert(hit.pk);
        std::set<std::string> expected;
        for (const auto& [pk, stored] : reference) {
          if (!stored) continue;
          bool overlaps = false;
          for (uint32_t operand : operands) {
            if (std::find(stored->begin(), stored->end(), operand) !=
                stored->end()) {
              overlaps = true;
              break;
            }
          }
          if (negate ? !overlaps : overlaps) expected.insert(pk);
        }
        auto describe = [](const std::set<std::string>& pks) {
          std::string out;
          for (const auto& pk : pks) out += (out.empty() ? "" : ",") + pk;
          return out;
        };
        Check(actual == expected,
              phase + (negate ? " NOT CONTAIN_ANY" : " CONTAIN_ANY") +
                  " PK set actual=[" + describe(actual) + "] expected=[" +
                  describe(expected) + "]");
        Check(actual.size() == result.hits.size(),
              phase + " array query no duplicate logical docs");
      }
    }
    m4::SearchResult too_many;
    Check(!index.SearchArrayUint32ContainsAny(
                    "ancestor_directory_ids", std::vector<uint32_t>(33, 17u),
                    &too_many)
               .ok,
          phase + " ArrayUint32 operand limit");
  };

  std::unique_ptr<Index> index;
  Check(Index::Open(dir, options, &index).ok, "typed array u32 open");
  if (!index) return;
  put(*index, "root-empty", std::vector<uint32_t>{});
  put(*index, "src-child", std::vector<uint32_t>{17u, 1u, 17u});
  put(*index, "src-deep", std::vector<uint32_t>{17u, 18u, 18u});
  put(*index, "other", std::vector<uint32_t>{42u, 0u});
  put(*index, "boundary", std::vector<uint32_t>{
                                std::numeric_limits<uint32_t>::max(), 0u});
  put(*index, "empty-other", std::vector<uint32_t>{});
  put(*index, "missing", std::nullopt);
  verify(*index, "typed array before WAL replay");
  index.reset();

  Check(Index::Open(dir, options, &index).ok, "typed array u32 WAL reopen");
  if (!index) return;
  verify(*index, "typed array after WAL replay");
  put(*index, "src-deep", std::vector<uint32_t>{42u,
                                                   std::numeric_limits<uint32_t>::max(),
                                                   42u});
  Check(index->Delete("other").ok, "typed array delete");
  reference.erase("other");
  verify(*index, "typed array after update/delete");
  put(*index, "other", std::vector<uint32_t>{17u});
  verify(*index, "typed array after reinsert");
  Check(index->Flush().ok, "typed array flush");
  verify(*index, "typed array after flush");
  ::setenv("M4_FAULT", "before_manifest", 1);
  const auto failed_compaction = index->CompactNow();
  ::unsetenv("M4_FAULT");
  Check(!failed_compaction.ok, "typed array injected compaction failure");
  verify(*index, "typed array remains searchable after failed compaction");
  Check(index->CompactNow().ok, "typed array compact");
  verify(*index, "typed array after compact");
  index.reset();

  Check(Index::Open(dir, options, &index).ok, "typed array u32 reopen");
  if (index) verify(*index, "typed array after reopen");
}

struct BooleanRow {
  std::optional<uint32_t> file_id;
  std::optional<std::vector<uint32_t>> directories;
  std::optional<std::string> status;
  std::string text;
  std::optional<std::string> file_name{};
};

std::optional<bool> EvalBooleanRef(const m4::FilterExpr& expr,
                                   const BooleanRow& row) {
  using m4::FilterKind;
  switch (expr.op) {
    case FilterKind::ALL: return true;
    case FilterKind::NONE: return false;
    case FilterKind::STRING_EQ:
      if (expr.field == "file_name") {
        if (!row.file_name) return std::nullopt;
        return *row.file_name == expr.string_value;
      }
      if (!row.status) return std::nullopt;
      return *row.status == expr.string_value;
    case FilterKind::STRING_PREFIX:
    case FilterKind::STRING_SUFFIX:
      if (!row.file_name) return std::nullopt;
      if (expr.op == FilterKind::STRING_PREFIX) {
        return row.file_name->size() >= expr.string_value.size() &&
               row.file_name->compare(0, expr.string_value.size(),
                                      expr.string_value) == 0;
      }
      return row.file_name->size() >= expr.string_value.size() &&
             row.file_name->compare(row.file_name->size() -
                                        expr.string_value.size(),
                                    expr.string_value.size(),
                                    expr.string_value) == 0;
    case FilterKind::UINT32_EQ:
      if (!row.file_id) return std::nullopt;
      return *row.file_id == expr.uint32_value;
    case FilterKind::UINT32_IN:
      if (!row.file_id) return std::nullopt;
      return std::find(expr.uint32_values.begin(), expr.uint32_values.end(),
                       *row.file_id) != expr.uint32_values.end();
    case FilterKind::ARRAY_UINT32_CONTAINS_ANY:
    case FilterKind::ARRAY_UINT32_NOT_CONTAINS_ANY: {
      if (expr.uint32_values.empty()) {
        if (expr.op == FilterKind::ARRAY_UINT32_CONTAINS_ANY) return false;
        return row.directories.has_value();
      }
      if (!row.directories) return std::nullopt;
      bool any = false;
      for (uint32_t value : expr.uint32_values) {
        any = any || std::find(row.directories->begin(), row.directories->end(),
                               value) != row.directories->end();
      }
      return expr.op == FilterKind::ARRAY_UINT32_NOT_CONTAINS_ANY ? !any : any;
    }
    case FilterKind::NOT: {
      if (expr.children.size() != 1) return std::nullopt;
      const auto value = EvalBooleanRef(expr.children.front(), row);
      return value ? std::optional<bool>(!*value) : std::nullopt;
    }
    case FilterKind::AND:
    case FilterKind::OR: {
      const bool is_and = expr.op == FilterKind::AND;
      bool unknown = false;
      for (const auto& child : expr.children) {
        const auto value = EvalBooleanRef(child, row);
        if (!value) {
          unknown = true;
        } else if (is_and && !*value) {
          return false;
        } else if (!is_and && *value) {
          return true;
        }
      }
      if (unknown) return std::nullopt;
      return is_and;
    }
  }
  return std::nullopt;
}

void TestBooleanFilters(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options options;
  options.fsync_wal = false;
  std::map<std::string, BooleanRow> rows;
  auto put = [&](Index& index, const std::string& pk, BooleanRow row) {
    Document doc;
    doc.text = row.text;
    if (row.file_id) {
      doc.values.push_back(ScalarValue::Unsigned32("file_id", *row.file_id));
    }
    if (row.status) doc.values.push_back({"status", *row.status});
    if (row.file_name) doc.values.push_back({"file_name", *row.file_name});
    if (row.directories) {
      doc.array_uint32_values.push_back(
          {"ancestor_directory_ids", *row.directories});
    }
    Check(index.Upsert(pk, doc).ok, "Boolean filter upsert " + pk);
    rows[pk] = std::move(row);
  };
  std::unique_ptr<Index> index;
  Check(Index::Open(dir, options, &index).ok, "Boolean filter open");
  if (!index) return;
  put(*index, "array-hit", {1u, std::vector<uint32_t>{17u}, "active",
                             "needle needle needle"});
  put(*index, "empty-array", {1u, std::vector<uint32_t>{}, "inactive",
                               "needle"});
  put(*index, "missing-array", {2u, std::nullopt, "active", "needle"});
  put(*index, "other-array", {2u, std::vector<uint32_t>{42u}, std::nullopt,
                               "needle needle"});
  put(*index, "missing-scalars", {std::nullopt, std::nullopt, std::nullopt,
                                   "needle"});
  put(*index, "file-main", {3u, std::vector<uint32_t>{}, "active", "needle",
                              "main.rs"});
  put(*index, "bare-main", {3u, std::vector<uint32_t>{}, "active", "needle",
                             "main"});
  put(*index, "file-case", {3u, std::vector<uint32_t>{}, "active", "needle",
                              "Main.rs"});
  put(*index, "file-under", {3u, std::vector<uint32_t>{}, "active", "needle",
                              "under_.rs"});
  put(*index, "file-percent", {3u, std::vector<uint32_t>{}, "active", "needle",
                                "literal%.rs"});
  put(*index, "file-decoy", {3u, std::vector<uint32_t>{}, "active", "needle",
                              "literalX.rs"});
  put(*index, "file-unicode", {3u, std::vector<uint32_t>{}, "active", "needle",
                                "果园.rs"});
  put(*index, "file-empty", {3u, std::vector<uint32_t>{}, "active", "needle",
                              ""});

  const auto array = m4::FilterExpr::ArrayContainsAny("ancestor_directory_ids",
                                                      {17u});
  const auto uid = m4::FilterExpr::Uint32Eq("file_id", 1u);
  const auto status = m4::FilterExpr::StringEq("status", "active");
  const std::vector<std::pair<std::string, m4::FilterExpr>> expressions = {
      {"nested AND/OR", m4::FilterExpr::And({
          array, m4::FilterExpr::Or({uid, m4::FilterExpr::Not(status)})})},
      {"AND skips ALL identities", m4::FilterExpr::And({
          m4::FilterExpr::All(), uid, m4::FilterExpr::All()})},
      {"OR skips NONE identities", m4::FilterExpr::Or({
          m4::FilterExpr::None(), uid, m4::FilterExpr::None()})},
      {"AND absorbs NONE", m4::FilterExpr::And({
          m4::FilterExpr::None(), uid})},
      {"OR absorbs ALL", m4::FilterExpr::Or({
          m4::FilterExpr::All(), uid})},
      {"NOT AND constants", m4::FilterExpr::Not(m4::FilterExpr::And({
          m4::FilterExpr::All(), uid}))},
      {"NOT OR constants", m4::FilterExpr::Not(m4::FilterExpr::Or({
          m4::FilterExpr::None(), uid}))},
      {"NOT of conjunction", m4::FilterExpr::Not(
          m4::FilterExpr::And({array, uid}))},
      {"NOT of disjunction", m4::FilterExpr::Not(
          m4::FilterExpr::Or({array, status}))},
      {"nested double NOT", m4::FilterExpr::Not(
          m4::FilterExpr::Not(m4::FilterExpr::Or({uid, status})))},
      {"typed IN and array", m4::FilterExpr::And({
          m4::FilterExpr::Uint32In("file_id", {1u, 2u}),
          m4::FilterExpr::ArrayNotContainsAny("ancestor_directory_ids",
                                               {17u})})},
      {"filename prefix", m4::FilterExpr::StringPrefix("file_name", "main")},
      {"filename suffix", m4::FilterExpr::StringSuffix("file_name", ".rs")},
      {"filename case-sensitive suffix", m4::FilterExpr::StringSuffix("file_name", ".RS")},
      {"filename literal underscore", m4::FilterExpr::StringPrefix("file_name", "under_")},
      {"filename literal percent", m4::FilterExpr::StringSuffix("file_name", "%.rs")},
      {"filename unicode prefix", m4::FilterExpr::StringPrefix("file_name", "果园")},
      {"filename unicode suffix", m4::FilterExpr::StringSuffix("file_name", "园.rs")},
      {"filename empty prefix", m4::FilterExpr::StringPrefix("file_name", "")},
      {"filename empty suffix", m4::FilterExpr::StringSuffix("file_name", "")},
      {"filename exact empty", m4::FilterExpr::StringEq("file_name", "")},
      {"filename not exact empty", m4::FilterExpr::Not(
          m4::FilterExpr::StringEq("file_name", ""))},
      {"filename Boolean composition", m4::FilterExpr::And({
          m4::FilterExpr::StringPrefix("file_name", "main"),
          m4::FilterExpr::StringSuffix("file_name", ".rs")})},
      {"overlapping prefix/suffix composition", m4::FilterExpr::And({
          m4::FilterExpr::StringPrefix("file_name", "main"),
          m4::FilterExpr::StringSuffix("file_name", "main")})},
      {"empty Uint32 IN", m4::FilterExpr::Uint32In("file_id", {})},
      {"empty AND", m4::FilterExpr::And({})},
      {"empty OR", m4::FilterExpr::Or({})},
      {"NOT empty AND", m4::FilterExpr::Not(m4::FilterExpr::And({}))},
      {"array NOT ANY empty operand", m4::FilterExpr::ArrayNotContainsAny(
          "ancestor_directory_ids", {})},
      {"NOT array ANY empty operand", m4::FilterExpr::Not(
          m4::FilterExpr::ArrayContainsAny("ancestor_directory_ids", {}))},
      {"NOT array NOT-ANY empty operand", m4::FilterExpr::Not(
          m4::FilterExpr::ArrayNotContainsAny("ancestor_directory_ids", {}))},
  };
  auto verify = [&](const std::string& phase) {
    for (const auto& [label, expr] : expressions) {
      m4::SearchResult actual;
      const auto query_status = index->SearchFilter(expr, &actual);
      Check(query_status.ok, phase + " " + label + " status: " +
                                 query_status.message);
      std::set<std::string> expected;
      for (const auto& [pk, row] : rows) {
        const auto value = EvalBooleanRef(expr, row);
        if (value && *value) expected.insert(pk);
      }
      std::set<std::string> pks;
      for (const auto& hit : actual.hits) pks.insert(hit.pk);
      Check(pks == expected, phase + " " + label + " PK set");
      Check(pks.size() == actual.hits.size(),
            phase + " " + label + " no duplicate docs");
    }
    m4::SearchResult direct_not_any;
    m4::SearchResult generic_not;
    Check(index->SearchArrayUint32NotContainsAny(
              "ancestor_directory_ids", {17u}, &direct_not_any).ok,
          phase + " direct NOT CONTAIN_ANY status");
    Check(index->SearchFilter(m4::FilterExpr::Not(array), &generic_not).ok,
          phase + " generic NOT predicate status");
    std::set<std::string> direct_pks, generic_pks;
    for (const auto& hit : direct_not_any.hits) direct_pks.insert(hit.pk);
    for (const auto& hit : generic_not.hits) generic_pks.insert(hit.pk);
    Check(direct_pks == generic_pks,
          phase + " non-empty NOT(predicate) matches native NOT CONTAIN_ANY");
    Check(direct_pks.count("empty-array") == 1 &&
              direct_pks.count("missing-array") == 0 &&
              direct_pks.count("missing-scalars") == 0,
          phase + " negative array includes empty, excludes missing");
    m4::SearchResult generic_empty_not, direct_empty_not;
    Check(index->SearchFilter(m4::FilterExpr::Not(
              m4::FilterExpr::ArrayContainsAny("ancestor_directory_ids", {})),
              &generic_empty_not).ok,
          phase + " generic NOT(empty CONTAIN_ANY) status");
    Check(index->SearchArrayUint32NotContainsAny(
              "ancestor_directory_ids", {}, &direct_empty_not).ok,
          phase + " direct empty NOT CONTAIN_ANY status");
    std::set<std::string> generic_empty_pks, direct_empty_pks;
    for (const auto& hit : generic_empty_not.hits) generic_empty_pks.insert(hit.pk);
    for (const auto& hit : direct_empty_not.hits) direct_empty_pks.insert(hit.pk);
    Check(generic_empty_pks.size() == rows.size() &&
              direct_empty_pks.size() < generic_empty_pks.size() &&
              generic_empty_pks.count("missing-array") == 1 &&
              direct_empty_pks.count("missing-array") == 0 &&
              direct_empty_pks.count("empty-array") == 1,
          phase + " generic NOT false differs from NOT CONTAIN_ANY IS NOT NULL");

    const auto filter = m4::FilterExpr::StringPrefix("file_name", "main");
    auto identical = [](const m4::SearchResult& a, const m4::SearchResult& b) {
      if (a.hits.size() != b.hits.size() || a.examined != b.examined) return false;
      for (size_t i = 0; i < a.hits.size(); ++i) {
        if (a.hits[i].pk != b.hits[i].pk ||
            a.hits[i].doc_id != b.hits[i].doc_id ||
            a.hits[i].score != b.hits[i].score) return false;
      }
      return true;
    };
    for (const std::string query : {"needle OR active", "needle AND active",
                                    "\"needle needle\""}) {
      m4::SearchResult off, on;
      m4::QueryDiagnostics diag;
      diag.collect_counters = true;
      const bool plain_ok = index->SearchFts(query, 5, &off).ok;
      const bool measured_ok = index->SearchFts(query, 5, &on, &diag).ok;
      Check(plain_ok && measured_ok && identical(off, on),
            phase + " diagnostics ON/OFF FTS parity: " + query);
      const bool filtered_plain_ok =
          index->SearchFtsFiltered(query, 5, filter, &off).ok;
      const bool filtered_measured_ok =
          index->SearchFtsFiltered(query, 5, filter, &on, &diag).ok;
      Check(filtered_plain_ok && filtered_measured_ok && identical(off, on),
            phase + " diagnostics ON/OFF filtered parity: " + query);
      Check(diag.topk_pushes == diag.topk_insertions + diag.topk_replacements,
            phase + " diagnostics top-k push accounting: " + query);
      Check(diag.topk_pops == diag.topk_replacements,
            phase + " diagnostics replacement-pop accounting: " + query);
      Check(diag.topk_finalize_pops == diag.sorted_hits &&
                diag.sorted_hits == on.hits.size(),
            phase + " diagnostics finalization accounting: " + query);
      Check(diag.liveness_lookups == diag.candidates +
                                        diag.dead_postings_filtered,
            phase + " diagnostics liveness accounting: " + query);
      Check(diag.filter_checks ==
                diag.filter_rejections + diag.liveness_lookups,
            phase + " diagnostics filter-before-liveness accounting: " + query);
      Check(diag.postings_scanned > 0 &&
                (query.find("AND") != std::string::npos ||
                 diag.cursor_next_calls + diag.cursor_advance_calls > 0),
            phase + " diagnostics cursor/posting accounting: " + query);
      if (query.find(" OR ") != std::string::npos) {
        Check(diag.wand_pivots > 0 && diag.upper_bound_evaluations > 0,
              phase + " OR/WAND diagnostics are populated: " + query);
      } else {
        Check(diag.wand_pivots == 0,
              phase + " AND/phrase paths do not count WAND pivots: " + query);
      }

      m4::SearchResult counters_only, timing_only;
      m4::QueryDiagnostics counter_diag;
      counter_diag.capture_timing = false;
      Check(index->SearchFtsFiltered(query, 5, filter, &counters_only,
                                     &counter_diag).ok &&
                identical(off, counters_only) &&
                counter_diag.score_evaluations > 0 &&
                counter_diag.query_parse_ms == 0.0 &&
                counter_diag.execution_ms == 0.0 &&
                counter_diag.mutex_wait_ms == 0.0,
            phase + " counters-only mode is correct and skips timing: " + query);
      m4::QueryDiagnostics timing_diag;
      timing_diag.collect_counters = false;
      Check(index->SearchFtsFiltered(query, 5, filter, &timing_only,
                                     &timing_diag).ok &&
                identical(off, timing_only) &&
                timing_diag.score_evaluations == 0 &&
                timing_diag.postings_scanned == 0 &&
                timing_diag.liveness_lookups == 0 &&
                timing_diag.query_parse_ms >= 0.0 &&
                timing_diag.execution_ms >= 0.0,
            phase + " timing-only mode skips operation counters: " + query);
    }

    const auto all_filter = m4::FilterExpr::All();
    m4::SearchResult all_plain, all_filtered, all_measured;
    m4::QueryDiagnostics all_diag;
    all_diag.collect_counters = true;
    all_diag.capture_timing = true;
    const bool plain_all_ok = index->SearchFts("needle OR active", 5,
                                               &all_plain).ok;
    const bool all_ok = index->SearchFtsFiltered("needle OR active", 5,
                                                 all_filter, &all_filtered).ok;
    const bool measured_all_ok = index->SearchFtsFiltered(
        "needle OR active", 5, all_filter, &all_measured, &all_diag).ok;
    Check(plain_all_ok && all_ok && measured_all_ok &&
              identical(all_plain, all_filtered) &&
              identical(all_plain, all_measured),
          phase + " ALL filter is retrieval-identical to unfiltered search");
    Check(all_diag.filter_ms == 0.0 && all_diag.filter_cpu_ms == 0.0 &&
              all_diag.filter_candidates == 0 &&
              all_diag.filter_candidate_bytes == 0 &&
              !all_diag.filter_cache_hit,
          phase + " ALL filter bypasses candidate materialization and cache");
  };
  verify("initial");

  // Complement is over field-present rows, while top-k is applied only after
  // the candidate predicate has admitted matching postings.
  m4::SearchResult unfiltered_top1, filtered_top1;
  m4::QueryDiagnostics diagnostics;
  const auto top_filter = m4::FilterExpr::StringPrefix("file_name", "main");
  Check(index->SearchFts("needle", 1, &unfiltered_top1).ok,
        "Boolean filter unfiltered top-k");
  Check(index->SearchFtsFiltered("needle", 1, top_filter, &filtered_top1,
                                 &diagnostics).ok,
        "Boolean filter before top-k");
  Check(!unfiltered_top1.hits.empty() &&
            unfiltered_top1.hits.front().pk == "array-hit",
        "unfiltered top-1 remains the overall highest score");
  Check(filtered_top1.hits.size() == 1 &&
            filtered_top1.hits.front().pk == "file-main",
        "filename filter is applied before top-k");
  Check(diagnostics.filter_candidates == 2 &&
            diagnostics.filter_candidate_bytes >= 2 * sizeof(m4::DocId) &&
            diagnostics.filter_like_strings_scanned > 0 &&
            diagnostics.filter_like_candidate_capacity_bytes >=
                2 * sizeof(m4::DocId),
        "filename filter telemetry reports candidates, scanned strings, and buffer capacity");
  Check(diagnostics.mutex_wait_ms >= 0 && diagnostics.mutex_wait_cpu_ms >= 0 &&
            diagnostics.filter_ms >= 0 && diagnostics.filter_cpu_ms >= 0 &&
            diagnostics.query_parse_ms >= 0 && diagnostics.query_parse_cpu_ms >= 0 &&
            diagnostics.setup_ms >= diagnostics.query_parse_ms &&
            diagnostics.setup_cpu_ms >= diagnostics.query_parse_cpu_ms &&
            diagnostics.execution_ms >= 0 && diagnostics.execution_cpu_ms >= 0 &&
            diagnostics.finalize_ms >= 0 && diagnostics.finalize_cpu_ms >= 0,
        "query phase diagnostics report nonnegative wall/CPU and nested setup/parse timings");

  m4::Options cache_options = options;
  cache_options.cache_filter_candidates = true;
  const std::string cache_dir = dir + "/candidate-cache";
  fs::remove_all(cache_dir);
  std::unique_ptr<Index> cached_index;
  Check(Index::Open(cache_dir, cache_options, &cached_index).ok,
        "filter-candidate cache index open");
  if (cached_index) {
    auto to_document = [](const BooleanRow& row) {
      Document doc;
      doc.text = row.text;
      if (row.file_id)
        doc.values.push_back(ScalarValue::Unsigned32("file_id", *row.file_id));
      if (row.status) doc.values.push_back({"status", *row.status});
      if (row.file_name) doc.values.push_back({"file_name", *row.file_name});
      if (row.directories)
        doc.array_uint32_values.push_back(
            {"ancestor_directory_ids", *row.directories});
      return doc;
    };
    for (const auto& [pk, row] : rows)
      Check(cached_index->Upsert(pk, to_document(row)).ok,
            "filter-candidate cache seed " + pk);
    auto same_hits = [](const m4::SearchResult& a, const m4::SearchResult& b) {
      if (a.hits.size() != b.hits.size()) return false;
      std::vector<std::pair<std::string, double>> a_hits, b_hits;
      for (const auto& hit : a.hits) a_hits.emplace_back(hit.pk, hit.score);
      for (const auto& hit : b.hits) b_hits.emplace_back(hit.pk, hit.score);
      std::sort(a_hits.begin(), a_hits.end());
      std::sort(b_hits.begin(), b_hits.end());
      return a_hits == b_hits;
    };
    auto same_ordered_hits = [](const m4::SearchResult& a,
                                const m4::SearchResult& b) {
      if (a.hits.size() != b.hits.size()) return false;
      for (size_t i = 0; i < a.hits.size(); ++i)
        if (a.hits[i].pk != b.hits[i].pk ||
            a.hits[i].doc_id != b.hits[i].doc_id ||
            a.hits[i].score != b.hits[i].score) return false;
      return true;
    };
    auto check_cache_query = [&](const std::string& phase, bool expect_hit,
                                 size_t expected_candidates) {
      m4::SearchResult uncached, cached;
      m4::QueryDiagnostics cache_diag;
      cache_diag.collect_counters = true;
      const bool plain_ok = index->SearchFtsFiltered(
          "needle", 5, top_filter, &uncached).ok;
      const bool cache_ok = cached_index->SearchFtsFiltered(
          "needle", 5, top_filter, &cached, &cache_diag).ok;
      Check(plain_ok && cache_ok && same_hits(uncached, cached),
            phase + " cached/uncached filtered search parity");
      Check(cache_diag.filter_cache_hit == expect_hit &&
                cache_diag.filter_candidates == expected_candidates,
            phase + " filter cache hit and candidate-count accounting");
      return cached;
    };
    m4::SearchResult cache_warm;
    m4::QueryDiagnostics first_cache_diag;
    first_cache_diag.collect_counters = true;
    Check(cached_index->SearchFtsFiltered("needle", 5, top_filter,
              &cache_warm, &first_cache_diag).ok &&
              !first_cache_diag.filter_cache_hit &&
              first_cache_diag.filter_candidates == 2,
          "filter-candidate cache first query populates cache");
    const auto cache_repeat =
        check_cache_query("filter cache repeat", true, 2);
    Check(same_ordered_hits(cache_warm, cache_repeat),
          "filter-candidate cache preserves within-index hit order and DocIds");
    const BooleanRow cache_added{3u, std::vector<uint32_t>{}, "active",
                                 "needle", "main-cache.rs"};
    put(*index, "cache-invalidation", cache_added);
    Check(cached_index->Upsert("cache-invalidation",
                               to_document(cache_added)).ok,
          "filter-candidate cache mutation invalidates cache");
    const auto cache_after_mutation =
        check_cache_query("filter cache after mutation", false, 3);
    const auto cache_after_repopulation =
        check_cache_query("filter cache after repopulation", true, 3);
    Check(same_ordered_hits(cache_after_mutation, cache_after_repopulation),
          "filter-candidate cache preserves post-mutation hit order and DocIds");
  }

  put(*index, "array-hit", {2u, std::vector<uint32_t>{17u, 17u}, "active",
                             "needle", "main.updated.rs"});
  Check(index->Delete("other-array").ok, "Boolean filter delete");
  rows.erase("other-array");
  verify("update/delete");
  put(*index, "other-array", {2u, std::vector<uint32_t>{42u}, "inactive",
                               "needle", "under_.reinserted.rs"});
  verify("reinsert");
  Check(index->Flush().ok, "Boolean filter flush");
  verify("flush");
  ::setenv("M4_FAULT", "before_manifest", 1);
  const auto failed = index->CompactNow();
  ::unsetenv("M4_FAULT");
  Check(!failed.ok, "Boolean filter pre-manifest fault injected");
  verify("fault keeps old manifest searchable");
  Check(index->CompactNow().ok, "Boolean filter compact");
  verify("compact");

  // WAL high-water invariant: replayed DocIds remain below subsequently
  // allocated IDs, even when the manifest predates the unflushed writes.
  put(*index, "wal-tail", {3u, std::vector<uint32_t>{17u}, "active", "needle"});
  m4::SearchResult before_reopen;
  Check(index->SearchFilter(m4::FilterExpr::All(), &before_reopen).ok,
        "Boolean filter snapshot before WAL replay");
  m4::DocId wal_id = 0;
  for (const auto& hit : before_reopen.hits) {
    if (hit.pk == "wal-tail") wal_id = hit.doc_id;
  }
  index.reset();
  Check(Index::Open(dir, options, &index).ok, "Boolean filter WAL replay open");
  if (!index) return;
  put(*index, "after-wal", {4u, std::vector<uint32_t>{42u}, "active", "needle"});
  m4::SearchResult after_reopen;
  Check(index->SearchFilter(m4::FilterExpr::All(), &after_reopen).ok,
        "Boolean filter post-WAL snapshot");
  m4::DocId next_id = 0;
  for (const auto& hit : after_reopen.hits) {
    if (hit.pk == "after-wal") next_id = hit.doc_id;
  }
  Check(wal_id != 0 && next_id > wal_id,
        "WAL replay preserves DocId non-reuse high-water invariant");
  verify("WAL replay");
  Check(index->Flush().ok, "Boolean filter post-WAL flush");
  Check(index->CompactNow().ok, "Boolean filter post-WAL compact");
  verify("post-WAL reopen preparation");
  index.reset();
  Check(Index::Open(dir, options, &index).ok, "Boolean filter final reopen");
  if (index) verify("reopen");
}

void TestStringPrefixRangeSearch(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options options;
  options.fsync_wal = false;
  options.max_delta_docs = 1024;
  std::unique_ptr<Index> index;
  Check(Index::Open(dir, options, &index).ok,
        "string-prefix range index open");
  if (!index) return;

  constexpr size_t kDocs = 1024;
  constexpr size_t kSelected = 32;
  for (size_t i = 0; i < kDocs; ++i) {
    Document doc;
    doc.text = "needle marker";
    doc.values.push_back({"file_name",
                          i < kSelected
                              ? "src/selected/file-" + std::to_string(i)
                              : "src/other/file-" + std::to_string(i)});
    Check(index->Upsert(std::to_string(i), std::move(doc)).ok,
          "string-prefix range upsert");
    if (i == 511 || i + 1 == kDocs) {
      Check(index->Flush().ok, "string-prefix range segment flush");
    }
  }
  Check(index->GetStats().runs == 2,
        "string-prefix range fixture uses two immutable segments");

  const auto filter = m4::FilterExpr::StringPrefix("file_name", "src/selected/");
  auto verify = [&](const std::string& phase,
                    const std::set<std::string>& expected) {
    m4::SearchResult result;
    m4::QueryDiagnostics diagnostics;
    diagnostics.collect_counters = true;
    const bool ok = index->SearchFtsFiltered("needle", kSelected + 4,
                                             filter, &result, &diagnostics).ok;
    std::set<std::string> actual;
    for (const auto& hit : result.hits) actual.insert(hit.pk);
    Check(ok && actual == expected,
          phase + " string-prefix results equal expected PKs");
    Check(diagnostics.filter_like_strings_scanned < 128,
          phase + " string-prefix seek examines sublinear immutable values");
  };

  std::set<std::string> expected;
  for (size_t i = 0; i < kSelected; ++i) expected.insert(std::to_string(i));
  verify("initial", expected);

  Document moved_out;
  moved_out.text = "needle marker";
  moved_out.values.push_back({"file_name", "src/other/updated"});
  Check(index->Upsert("0", std::move(moved_out)).ok,
        "string-prefix update moves row out of prefix");
  Document moved_in;
  moved_in.text = "needle marker";
  moved_in.values.push_back({"file_name", "src/selected/updated"});
  Check(index->Upsert("900", std::move(moved_in)).ok,
        "string-prefix update moves row into prefix");
  expected.erase("0");
  expected.insert("900");
  verify("delta update", expected);

  Check(index->Flush().ok, "string-prefix delta flush");
  Check(index->CompactNow().ok, "string-prefix compaction");
  verify("compacted", expected);
  index.reset();
  Check(Index::Open(dir, options, &index).ok, "string-prefix reopen");
  if (index) verify("reopened", expected);
}

void TestPhrase(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.max_delta_docs = 1000;
  std::unique_ptr<Index> index;
  m4::Status s = Index::Open(dir, opt, &index);
  Check(s.ok, "phrase open");
  RefState ref;
  auto put = [&](const std::string& pk, const std::string& text) {
    Document d;
    d.text = text;
    AddDoc(&ref, pk, d);
    Check(index->Upsert(pk, d).ok, "phrase upsert");
  };
  put("a", "alpha beta gamma");
  put("b", "alpha x beta");
  put("c", "beta gamma");
  put("d", "alpha beta");
  CheckFts(*index, ref, "\"alpha beta\"",
           MakeRefQuery({}, false, {{"alpha", "beta"}}), 10,
           "phrase ab delta");
  CheckFts(*index, ref, "\"beta gamma\"",
           MakeRefQuery({}, false, {{"beta", "gamma"}}), 10,
           "phrase bg delta");
  Check(index->Flush().ok, "phrase flush");
  CheckFts(*index, ref, "\"alpha beta\"",
           MakeRefQuery({}, false, {{"alpha", "beta"}}), 10,
           "phrase ab v1");
  CheckFts(*index, ref, "\"beta gamma\"",
           MakeRefQuery({}, false, {{"beta", "gamma"}}), 10,
           "phrase bg v1");
  put("e", "alpha beta gamma alpha beta");
  Check(index->Flush().ok, "phrase second flush");
  Check(index->CompactNow().ok, "phrase compaction");
  CheckFts(*index, ref, "\"alpha beta\"",
           MakeRefQuery({}, false, {{"alpha", "beta"}}), 10, "phrase ab");
  CheckFts(*index, ref, "\"beta gamma\"",
           MakeRefQuery({}, false, {{"beta", "gamma"}}), 10, "phrase bg");
  CheckFts(*index, ref, "\"alpha beta\" \"beta gamma\"",
           MakeRefQuery({}, false,
                        {{"alpha", "beta"}, {"beta", "gamma"}}),
           10, "multiple phrases v2");
}

void TestAtomicPublicationSyncFailures(const std::string& root) {
  const std::vector<std::string> initialization_faults = {
      "metadata_file_fsync", "manifest_directory_fsync"};
  m4::Options options;
  options.max_delta_docs = 10000;
  options.max_delta_bytes = 1ull << 30;
  options.wal_enabled = true;
  options.fsync_wal = true;
  for (const auto& fault : initialization_faults) {
    const std::string dir = root + "/initial-" + fault;
    fs::remove_all(dir);
    std::unique_ptr<Index> index;
    Check(::setenv("M4_FAULT", fault.c_str(), 1) == 0,
          "set initialization durability fault " + fault);
    const m4::Status open = Index::Open(dir, options, &index);
    ::unsetenv("M4_FAULT");
    Check(!open.ok, "initialization sync failure must fail open " + fault);
    index.reset();

    std::unique_ptr<Index> recovered;
    const m4::Status recovery = Index::Open(dir, options, &recovered);
    Check(recovery.ok, "initialization sync failure recovery " + fault + ": " +
                           recovery.message);
    if (recovered) {
      Check(recovered->GetStats().live_docs == 0,
            "initialization recovery remains empty " + fault);
    }
  }

  const std::string crash_dir = root + "/after-initial-manifest";
  fs::remove_all(crash_dir);
  const pid_t child = ::fork();
  if (child == 0) {
    ::setenv("M4_FAULT", "initial_after_manifest", 1);
    std::unique_ptr<Index> child_index;
    const m4::Status open = Index::Open(crash_dir, options, &child_index);
    ::_exit(open.ok ? 10 : 11);
  }
  int child_status = 0;
  const bool waited = child > 0 && ::waitpid(child, &child_status, 0) == child;
  Check(waited && WIFEXITED(child_status) && WEXITSTATUS(child_status) == 96,
        "initialization crash occurs after durable empty manifest");
  std::unique_ptr<Index> recovered;
  const m4::Status recovery = Index::Open(crash_dir, options, &recovered);
  Check(recovery.ok,
        "recover empty bootstrap after crash: " + recovery.message);
  if (recovered) {
    Check(recovered->GetStats().live_docs == 0,
          "crash-recovered bootstrap contains no acknowledged documents");
  }
  ::unsetenv("M4_FAULT");

  const std::vector<std::string> faults = {
      "segment_file_fsync", "segment_directory_fsync",
      "metadata_file_fsync", "manifest_directory_fsync"};
  for (const auto& fault : faults) {
    const std::string dir = root + "/" + fault;
    fs::remove_all(dir);
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, options, &index).ok,
          "durability-fault open " + fault);
    if (!index) continue;
    Document document;
    document.text = "atomic publication durable alpha";
    RefState ref;
    AddDoc(&ref, "durable", document);
    Check(index->Upsert("durable", document).ok,
          "durability-fault upsert " + fault);
    Check(::setenv("M4_FAULT", fault.c_str(), 1) == 0,
          "set durability fault " + fault);
    const m4::Status flush = index->Flush();
    ::unsetenv("M4_FAULT");
    Check(!flush.ok,
          "sync failure must fail publication " + fault + ": " + flush.message);
    index.reset();

    std::unique_ptr<Index> reopened;
    const m4::Status open = Index::Open(dir, options, &reopened);
    Check(open.ok, "durability-fault reopen " + fault + ": " + open.message);
    if (!open.ok)
      continue;
    Check(reopened->GetStats().live_docs == 1,
          "durability-fault WAL recovery count " + fault);
    CheckFts(*reopened, ref, "alpha", MakeRefQuery({"alpha"}, false), 10,
             "durability-fault WAL recovery query " + fault);
  }
  ::unsetenv("M4_FAULT");
}

void TestCrashWal(const std::string &dir) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.max_delta_docs = 100000;
  opt.max_delta_bytes = 1ull << 30;
  opt.wal_enabled = true;
  opt.fsync_wal = true;
  RefState ref;
  {
    std::unique_ptr<Index> index;
    m4::Status s = Index::Open(dir, opt, &index);
    Check(s.ok, "wal open");
    for (int i = 0; i < 40; ++i) {
      const std::string pk = "w-" + std::to_string(i);
      Document d;
      d.text = "alpha w" + std::to_string(i % 5) + " beta";
      AddDoc(&ref, pk, d);
      Check(index->Upsert(pk, d).ok, "wal upsert");
    }
    // Destroy without Flush: only the WAL has the writes.
  }
  {
    std::unique_ptr<Index> index;
    m4::Status s = Index::Open(dir, opt, &index);
    Check(s.ok, "wal reopen: " + s.message);
    if (!s.ok)
      return;
    CheckFts(*index, ref, "alpha", MakeRefQuery({"alpha"}, false), 50,
             "wal replay alpha");
    Check(index->GetStats().live_docs == ref.docs.size(), "wal replay count");
    Check(index->Flush().ok, "wal flush after replay");
  }
  {
    std::unique_ptr<Index> index;
    m4::Status s = Index::Open(dir, opt, &index);
    Check(s.ok, "wal reopen after flush: " + s.message);
    if (!s.ok)
      return;
    CheckFts(*index, ref, "beta", MakeRefQuery({"beta"}, false), 50,
             "wal persisted beta");
  }

  // A complete corrupt frame in the middle of an otherwise valid WAL must not
  // be treated as a torn tail: later durable records must not be truncated.
  const std::string corrupt_dir = dir + "-mid-log-corruption";
  fs::remove_all(corrupt_dir);
  {
    std::unique_ptr<Index> index;
    Check(Index::Open(corrupt_dir, opt, &index).ok,
          "mid-log-corruption fixture open");
    if (!index)
      return;
    for (int i = 0; i < 3; ++i) {
      Document document;
      document.text = "durable record " + std::to_string(i);
      Check(index->Upsert("corrupt-" + std::to_string(i), document).ok,
            "mid-log-corruption fixture upsert");
    }
  }
  const fs::path wal_path = fs::path(corrupt_dir) / "wal.log";
  std::string wal_bytes;
  {
    std::ifstream input(wal_path, std::ios::binary);
    wal_bytes.assign(std::istreambuf_iterator<char>(input),
                     std::istreambuf_iterator<char>());
  }
  const std::string clean_wal_bytes = wal_bytes;
  const auto read_u32_le = [&wal_bytes](size_t offset) -> uint32_t {
    return static_cast<uint32_t>(
               static_cast<unsigned char>(wal_bytes[offset])) |
           (static_cast<uint32_t>(
                static_cast<unsigned char>(wal_bytes[offset + 1]))
            << 8) |
           (static_cast<uint32_t>(
                static_cast<unsigned char>(wal_bytes[offset + 2]))
            << 16) |
           (static_cast<uint32_t>(
                static_cast<unsigned char>(wal_bytes[offset + 3]))
            << 24);
  };
  if (wal_bytes.size() < 3 * 24) {
    Check(false, "mid-log-corruption fixture has 3 frames");
    fs::remove_all(corrupt_dir);
    return;
  }
  const size_t second_frame = 24 + read_u32_le(4);
  if (second_frame + 24 >= wal_bytes.size()) {
    Check(false, "mid-log-corruption second frame has payload");
    fs::remove_all(corrupt_dir);
    return;
  }
  const size_t third_frame = second_frame + 24 + read_u32_le(second_frame + 4);
  if (third_frame + 24 > wal_bytes.size()) {
    Check(false, "mid-log-corruption fixture retains a later frame");
    fs::remove_all(corrupt_dir);
    return;
  }
  wal_bytes[second_frame + 24] ^= 0x01;
  {
    std::ofstream output(wal_path, std::ios::binary | std::ios::trunc);
    output.write(wal_bytes.data(),
                 static_cast<std::streamsize>(wal_bytes.size()));
    output.flush();
    Check(output.good(), "write mid-log-corruption fixture");
  }
  std::unique_ptr<Index> corrupted;
  const m4::Status corrupt_open = Index::Open(corrupt_dir, opt, &corrupted);
  Check(!corrupt_open.ok, "complete mid-log corruption must fail closed: " +
                              corrupt_open.message);
  Check(!corrupted, "corrupt WAL must not publish an index");
  {
    std::ifstream input(wal_path, std::ios::binary);
    const std::string after_recovery{std::istreambuf_iterator<char>(input),
                                     std::istreambuf_iterator<char>()};
    Check(after_recovery == wal_bytes,
          "mid-log corruption must not truncate later durable WAL records");
  }

  std::string corrupt_length_bytes = clean_wal_bytes;
  for (size_t i = 0; i < sizeof(uint32_t); ++i) {
    corrupt_length_bytes[second_frame + 4 + i] = static_cast<char>(0xff);
  }
  {
    std::ofstream output(wal_path, std::ios::binary | std::ios::trunc);
    output.write(corrupt_length_bytes.data(),
                 static_cast<std::streamsize>(corrupt_length_bytes.size()));
    output.flush();
    Check(output.good(), "write corrupt-length WAL fixture");
  }
  std::unique_ptr<Index> corrupt_length_index;
  const m4::Status corrupt_length_status =
      Index::Open(corrupt_dir, opt, &corrupt_length_index);
  Check(!corrupt_length_status.ok && !corrupt_length_index,
        "corrupt frame length before later records fails closed");
  {
    std::ifstream input(wal_path, std::ios::binary);
    const std::string after_recovery{std::istreambuf_iterator<char>(input),
                                     std::istreambuf_iterator<char>()};
    Check(after_recovery == corrupt_length_bytes,
          "corrupt length must not truncate later durable WAL records");
  }
  std::string corrupt_sequence_bytes = clean_wal_bytes;
  corrupt_sequence_bytes[second_frame + 8] = 1;
  for (size_t i = 1; i < sizeof(uint64_t); ++i) {
    corrupt_sequence_bytes[second_frame + 8 + i] = 0;
  }
  const uint32_t frame_len = read_u32_le(second_frame + 4);
  const uint32_t duplicate_sequence_header_checksum =
      WalHeaderChecksumForTest(0x4D345731, frame_len, 1, frame_len);
  for (size_t i = 0; i < sizeof(uint32_t); ++i) {
    corrupt_sequence_bytes[second_frame + 16 + i] =
        static_cast<char>(duplicate_sequence_header_checksum >> (i * 8));
  }
  const uint32_t duplicate_sequence_payload_checksum =
      WalSequenceChecksumForTest(
          1, corrupt_sequence_bytes.data() + second_frame + 24, frame_len);
  for (size_t i = 0; i < sizeof(uint32_t); ++i) {
    corrupt_sequence_bytes[second_frame + 20 + i] =
        static_cast<char>(duplicate_sequence_payload_checksum >> (i * 8));
  }
  {
    std::ofstream output(wal_path, std::ios::binary | std::ios::trunc);
    output.write(corrupt_sequence_bytes.data(),
                 static_cast<std::streamsize>(corrupt_sequence_bytes.size()));
    output.flush();
    Check(output.good(), "write duplicate-sequence WAL fixture");
  }
  std::unique_ptr<Index> corrupt_sequence_index;
  const m4::Status corrupt_sequence_status =
      Index::Open(corrupt_dir, opt, &corrupt_sequence_index);
  Check(!corrupt_sequence_status.ok && !corrupt_sequence_index,
        "duplicate WAL sequence fails closed rather than skipping an ACK");
  {
    std::ifstream input(wal_path, std::ios::binary);
    const std::string after_recovery{std::istreambuf_iterator<char>(input),
                                     std::istreambuf_iterator<char>()};
    Check(after_recovery == corrupt_sequence_bytes,
          "duplicate sequence corruption leaves WAL bytes untouched");
  }
  fs::remove_all(corrupt_dir);
}

void TestWalRecordBounds(const std::string &dir) {
  constexpr size_t kRecordLimit = 2u << 20;
  const std::string pk = "record-cap";
  const size_t text_size = kRecordLimit - 32;
  const auto encoded_upsert_size = [&](const std::string& text) {
    m4::BufWriter payload;
    payload.U8(1);
    payload.U64(0);
    payload.U64(1);
    payload.String(pk);
    payload.String(text);
    payload.Varint(0);
    return payload.size();
  };
  Document boundary;
  boundary.text.assign(text_size, '!');
  Check(encoded_upsert_size(boundary.text) == kRecordLimit,
        "record-cap fixture encodes at exact 2 MiB boundary");

  fs::remove_all(dir);
  m4::Options options;
  options.wal_enabled = true;
  options.fsync_wal = true;
  {
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, options, &index).ok,
          "record-cap boundary index open");
    if (!index) return;
    Check(index->Upsert(pk, boundary).ok,
          "Upsert at exact application record boundary is accepted");
    const fs::path wal_path = fs::path(dir) / "wal.log";
    std::ifstream input(wal_path, std::ios::binary);
    std::string before_rejection{std::istreambuf_iterator<char>(input),
                                 std::istreambuf_iterator<char>()};
    Check(before_rejection.size() >= 24 &&
              m4::BufReader(before_rejection.data(), before_rejection.size()).U32() ==
                  0x4D345731,
          "boundary Upsert is represented by a complete sequenced WAL frame");
    Document too_large = boundary;
    too_large.text.push_back('!');
    const m4::Status rejected = index->Upsert("over-limit", too_large);
    Check(!rejected.ok && rejected.message.find("2 MiB") != std::string::npos,
          "Upsert one encoded byte over the record limit is rejected");
    Check(index->GetStats().live_docs == 1,
          "oversized Upsert leaves query-visible state unchanged");
    std::ifstream after_input(wal_path, std::ios::binary);
    const std::string after_rejection{std::istreambuf_iterator<char>(after_input),
                                      std::istreambuf_iterator<char>()};
    Check(after_rejection == before_rejection,
          "oversized Upsert writes no WAL bytes and consumes no sequence");

    Document small;
    small.text = "accepted after rejected oversized input";
    Check(index->Upsert("after-reject", small).ok,
          "valid Upsert after rejection succeeds");
    const std::string after_valid = [&] {
      std::ifstream wal(fs::path(dir) / "wal.log", std::ios::binary);
      return std::string{std::istreambuf_iterator<char>(wal),
                         std::istreambuf_iterator<char>()};
    }();
    std::vector<m4::Mutation> atomic_batch;
    atomic_batch.emplace_back(m4::MutationKind::UPSERT, "batch-small", small);
    atomic_batch.emplace_back(m4::MutationKind::UPSERT, "batch-large", too_large);
    const m4::Status batch_status = index->ApplyBatch(atomic_batch);
    Check(!batch_status.ok,
          "atomic batch rejects oversized child before WAL append");
    Check(index->GetStats().live_docs == 2,
          "oversized atomic batch applies none of its prefix");
    std::ifstream after_batch_input(wal_path, std::ios::binary);
    const std::string after_batch{std::istreambuf_iterator<char>(after_batch_input),
                                  std::istreambuf_iterator<char>()};
    Check(after_batch == after_valid,
          "rejected atomic batch leaves the durable WAL unchanged");
  }
  {
    std::unique_ptr<Index> reopened;
    const m4::Status status = Index::Open(dir, options, &reopened);
    Check(status.ok, "exact-boundary and post-rejection WAL reopens: " +
                         status.message);
    if (reopened) {
      Check(reopened->GetStats().live_docs == 2,
            "reopen recovers boundary record and later valid record exactly");
    }
  }

  const auto write_legacy_frame = [](const fs::path& path,
                                     const std::string& payload) {
    m4::BufWriter frame;
    frame.U32(0x4D34574C);  // Legacy M4WL, with its historical FNV checksum.
    frame.U32(static_cast<uint32_t>(payload.size()));
    frame.U64(1);
    frame.U32(static_cast<uint32_t>(m4::Fnv1a(payload.data(), payload.size()) &
                                    0xffffffffu));
    frame.Bytes(payload.data(), payload.size());
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    output.write(frame.data().data(),
                 static_cast<std::streamsize>(frame.data().size()));
    output.flush();
    return output.good();
  };
  const auto prepare_empty_index = [&](const std::string& path) {
    fs::remove_all(path);
    std::unique_ptr<Index> index;
    const m4::Status status = Index::Open(path, options, &index);
    Check(status.ok, "legacy WAL fixture index open: " + status.message);
  };
  const auto wal_contents = [](const fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    return std::string{std::istreambuf_iterator<char>(input),
                       std::istreambuf_iterator<char>()};
  };
  const auto check_legacy_rejected_and_retained = [&](const std::string& path,
                                                       const std::string& payload,
                                                       const std::string& reason) {
    prepare_empty_index(path);
    const fs::path wal_path = fs::path(path) / "wal.log";
    Check(write_legacy_frame(wal_path, payload), reason + " fixture written");
    const std::string durable_bytes = wal_contents(wal_path);
    std::unique_ptr<Index> rejected;
    const m4::Status status = Index::Open(path, options, &rejected);
    Check(!status.ok && !rejected && status.message.find(reason) != std::string::npos,
          reason + " fails closed with a clear recovery error");
    Check(wal_contents(wal_path) == durable_bytes,
          reason + " leaves the durable legacy WAL unchanged");
  };

  std::string malformed_payload;
  {
    m4::BufWriter payload;
    payload.U8(1);
    payload.U64(0);
    payload.U64(1);
    payload.String("malformed-varint");
    const char overflow_length[10] = {
        char(0x80), char(0x80), char(0x80), char(0x80), char(0x80),
        char(0x80), char(0x80), char(0x80), char(0x80), char(0x02)};
    payload.Bytes(overflow_length, sizeof(overflow_length));
    malformed_payload = payload.data();
  }
  check_legacy_rejected_and_retained(dir + "-malformed-varint",
                                    malformed_payload,
                                    "bad upsert wal record");

  std::string oversized_payload;
  {
    m4::BufWriter payload;
    payload.U8(1);
    payload.U64(0);
    payload.U64(1);
    payload.String("legacy-oversized");
    payload.String(std::string(kRecordLimit, '!'));
    payload.Varint(0);
    oversized_payload = payload.data();
  }
  check_legacy_rejected_and_retained(dir + "-legacy-oversized",
                                    oversized_payload,
                                    "2 MiB record limit");
}

void TestWalSequenceChecksumBoundary(const std::string &dir) {
  fs::remove_all(dir);
  m4::Options options;
  options.max_delta_docs = 100000;
  options.max_delta_bytes = 1ull << 30;
  options.wal_enabled = true;
  options.fsync_wal = true;
  std::unique_ptr<Index> index;
  Check(Index::Open(dir, options, &index).ok,
        "sequence-checksum boundary index open");
  if (!index)
    return;
  Document committed;
  committed.text = "committed before checkpoint";
  Check(index->Upsert("sequence-committed", committed).ok,
        "sequence-checksum boundary first durable Upsert");
  Check(index->Flush().ok, "sequence-checksum boundary checkpoint");
  Document uncommitted;
  uncommitted.text = "durable after checkpoint";
  Check(index->Upsert("sequence-uncommitted", uncommitted).ok,
        "sequence-checksum boundary post-checkpoint durable Upsert");
  index.reset();

  const fs::path wal_path = fs::path(dir) / "wal.log";
  std::string bytes;
  {
    std::ifstream input(wal_path, std::ios::binary);
    bytes.assign(std::istreambuf_iterator<char>(input),
                 std::istreambuf_iterator<char>());
  }
  Check(bytes.size() > 24, "post-checkpoint WAL contains a complete frame");
  if (bytes.size() <= 24)
    return;
  const std::string clean_bytes = bytes;
  std::string corrupt_length_bytes = clean_bytes;
  for (size_t i = 0; i < sizeof(uint32_t); ++i) {
    corrupt_length_bytes[4 + i] = static_cast<char>(0xff);
  }
  {
    std::ofstream output(wal_path, std::ios::binary | std::ios::trunc);
    output.write(corrupt_length_bytes.data(),
                 static_cast<std::streamsize>(corrupt_length_bytes.size()));
    output.flush();
    Check(output.good(), "write final-frame corrupted-length fixture");
  }
  std::unique_ptr<Index> corrupt_length;
  const m4::Status corrupt_length_status =
      Index::Open(dir, options, &corrupt_length);
  Check(!corrupt_length_status.ok && !corrupt_length,
        "corrupt final WAL length fails closed, not as a torn tail");
  {
    std::ifstream input(wal_path, std::ios::binary);
    const std::string after_recovery{std::istreambuf_iterator<char>(input),
                                     std::istreambuf_iterator<char>()};
    Check(after_recovery == corrupt_length_bytes,
          "final corrupted length leaves WAL bytes untouched");
  }

  m4::BufReader frame(bytes.data(), bytes.size());
  const uint32_t magic = frame.U32();
  (void)frame.U32();
  const uint64_t sequence = frame.U64();
  Check(magic == 0x4D345731 && sequence == 2,
        "new raw WAL frame carries expected sequence-bound format");
  bytes[8] = 1; // Change sequence 2 to already-checkpointed sequence 1.
  for (size_t i = 1; i < sizeof(uint64_t); ++i)
    bytes[8 + i] = 0;
  {
    std::ofstream output(wal_path, std::ios::binary | std::ios::trunc);
    output.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    output.flush();
    Check(output.good(), "write manifest-boundary sequence corruption");
  }
  std::unique_ptr<Index> recovered;
  const m4::Status status = Index::Open(dir, options, &recovered);
  Check(!status.ok && !recovered,
        "sequence corruption to manifest value fails closed");
  {
    std::ifstream input(wal_path, std::ios::binary);
    const std::string after_recovery{std::istreambuf_iterator<char>(input),
                                     std::istreambuf_iterator<char>()};
    Check(after_recovery != clean_bytes && after_recovery == bytes,
          "manifest-boundary corruption is detected without truncating WAL");
  }

  const std::string payload_checksum_dir = dir + "-payload-checksum";
  fs::remove_all(payload_checksum_dir);
  std::error_code copy_error;
  fs::copy(dir, payload_checksum_dir, fs::copy_options::recursive, copy_error);
  Check(!copy_error, "copy index for sequence-bound payload checksum test");
  if (!copy_error) {
    std::string header_valid_sequence_corruption = clean_bytes;
    for (size_t i = 0; i < sizeof(uint64_t); ++i) {
      header_valid_sequence_corruption[8 + i] =
          static_cast<char>(i == 0 ? 1 : 0);
    }
    m4::BufReader clean_frame(clean_bytes.data(), clean_bytes.size());
    const uint32_t clean_magic = clean_frame.U32();
    const uint32_t clean_len = clean_frame.U32();
    const uint32_t header_checksum =
        WalHeaderChecksumForTest(clean_magic, clean_len, 1, clean_len);
    for (size_t i = 0; i < sizeof(uint32_t); ++i) {
      header_valid_sequence_corruption[16 + i] =
          static_cast<char>(header_checksum >> (i * 8));
    }
    const fs::path payload_wal_path =
        fs::path(payload_checksum_dir) / "wal.log";
    {
      std::ofstream output(payload_wal_path,
                           std::ios::binary | std::ios::trunc);
      output.write(header_valid_sequence_corruption.data(),
                   static_cast<std::streamsize>(
                       header_valid_sequence_corruption.size()));
      output.flush();
      Check(output.good(), "write header-valid sequence corruption fixture");
    }
    std::unique_ptr<Index> payload_checksum_index;
    const m4::Status payload_checksum_status =
        Index::Open(payload_checksum_dir, options, &payload_checksum_index);
    Check(!payload_checksum_status.ok && !payload_checksum_index &&
              payload_checksum_status.message.find(
                  "WAL frame checksum mismatch") != std::string::npos,
          "sequence-bound payload checksum rejects tampering after valid "
          "header CRC");
    {
      std::ifstream input(payload_wal_path, std::ios::binary);
      const std::string after_recovery{std::istreambuf_iterator<char>(input),
                                       std::istreambuf_iterator<char>()};
      Check(after_recovery == header_valid_sequence_corruption,
            "payload checksum failure preserves the original WAL bytes");
    }
  }
}

void TestLz4WalFrames(const std::string &root) {
  auto write_fixture = [](const std::string &dir, bool compress,
                          uint32_t acceleration = 1) {
    fs::remove_all(dir);
    m4::Options options;
    options.max_delta_docs = 100000;
    options.max_delta_bytes = 1ull << 30;
    options.wal_compression_lz4 = compress;
    options.wal_lz4_acceleration = acceleration;
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, options, &index).ok, "LZ4 WAL open");
    if (!index)
      return;
    std::vector<m4::Mutation> batch;
    for (int i = 0; i < 40; ++i) {
      Document document;
      document.text = std::string(2048, 'x') + " alpha beta";
      batch.emplace_back(m4::MutationKind::UPSERT, "lz4-" + std::to_string(i),
                         document);
    }
    Check(index->ApplyBatch(batch).ok, "LZ4 WAL batch");
    const auto stats = index->GetStats();
    Check(stats.wal_bytes > 0, "LZ4 WAL retained bytes");
    if (compress) {
      Check(stats.wal_lz4_attempts == 1 &&
                stats.wal_lz4_attempted_input_bytes > 0,
            "LZ4 WAL records compression attempt telemetry");
      Check(stats.wal_lz4_compressed_frames == 1 &&
                stats.wal_lz4_input_bytes > stats.wal_lz4_output_bytes,
            "LZ4 WAL records accepted-frame byte savings");
      Check(stats.wal_lz4_compress_cpu_ms >= 0.0,
            "LZ4 WAL records nonnegative compression thread CPU");
    } else {
      Check(stats.wal_lz4_attempts == 0 &&
                stats.wal_lz4_compressed_frames == 0 &&
                stats.wal_lz4_compress_cpu_ms == 0.0,
            "legacy WAL has zero LZ4 telemetry");
    }
  };

  const std::string compressed_dir = root + "/compressed";
  const std::string fast_compressed_dir = root + "/compressed-fast";
  const std::string raw_dir = root + "/raw";
  write_fixture(compressed_dir, true);
  write_fixture(fast_compressed_dir, true, 4);
  write_fixture(raw_dir, false);
  std::error_code ec;
  const uint64_t compressed_size =
      fs::file_size(fs::path(compressed_dir) / "wal.log", ec);
  Check(!ec, "compressed WAL size");
  const uint64_t fast_compressed_size =
      fs::file_size(fs::path(fast_compressed_dir) / "wal.log", ec);
  Check(!ec, "fast-compressed WAL size");
  const uint64_t raw_size = fs::file_size(fs::path(raw_dir) / "wal.log", ec);
  Check(!ec, "raw WAL size");
  Check(compressed_size < raw_size && fast_compressed_size < raw_size,
        "LZ4 WAL is smaller than legacy frame at default and accelerated "
        "settings");

  // Current writers bind the sequence into each checksum; verify both old
  // payload-only frame variants remain readable by the new replay path.
  RewriteWalFrameAsLegacy(fs::path(raw_dir) / "wal.log", false);
  const std::string old_compressed_dir = root + "/old-compressed";
  fs::copy(compressed_dir, old_compressed_dir, fs::copy_options::recursive, ec);
  Check(!ec, "copy compressed WAL for old-format compatibility test");
  if (!ec) {
    RewriteWalFrameAsLegacy(fs::path(old_compressed_dir) / "wal.log", true);
    std::unique_ptr<Index> old_compressed_reader;
    const m4::Status old_compressed_status =
        Index::Open(old_compressed_dir, m4::Options{}, &old_compressed_reader);
    Check(old_compressed_status.ok && old_compressed_reader &&
              old_compressed_reader->GetStats().live_docs == 40,
          "old payload-only compressed WAL remains readable");
  }
  m4::Options fast_reader_options;
  std::unique_ptr<Index> fast_reader;
  Check(Index::Open(fast_compressed_dir, fast_reader_options, &fast_reader).ok,
        "accelerated LZ4 WAL replay");
  Check(fast_reader && fast_reader->GetStats().live_docs == 40,
        "accelerated LZ4 WAL recovers all documents");
  fast_reader.reset();

  m4::Options replay_options;
  m4::OpenDiagnostics replay_diagnostics;
  std::unique_ptr<Index> interrupted_reader;
  Check(::setenv("M4_FAULT", "wal_pread_eintr_once", 1) == 0,
        "set WAL replay EINTR injection");
  const m4::Status interrupted_status = Index::Open(
      compressed_dir, replay_options, &interrupted_reader, &replay_diagnostics);
  ::unsetenv("M4_FAULT");
  Check(interrupted_status.ok && interrupted_reader &&
            interrupted_reader->GetStats().live_docs == 40,
        "WAL replay retries interrupted pread and recovers every frame");
  Check(
      replay_diagnostics.wal_replay_record_count == 1 &&
          replay_diagnostics.wal_replay_encoded_file_bytes > 0 &&
          replay_diagnostics.wal_replay_decoded_payload_bytes > 0 &&
          replay_diagnostics.wal_replay_decoded_payload_capacity_bytes >=
              replay_diagnostics.wal_replay_decoded_payload_bytes &&
          replay_diagnostics.wal_replay_peak_tracked_live_bytes >=
              replay_diagnostics.wal_replay_encoded_buffer_capacity_bytes +
                  replay_diagnostics.wal_replay_decoded_payload_capacity_bytes,
      "WAL replay reports encoded/decoded live-capacity diagnostics");
  interrupted_reader.reset();

  std::unique_ptr<Index> failed_reader;
  Check(::setenv("M4_FAULT", "wal_pread_error", 1) == 0,
        "set WAL replay read-error injection");
  const m4::Status read_error_status =
      Index::Open(raw_dir, replay_options, &failed_reader);
  ::unsetenv("M4_FAULT");
  Check(!read_error_status.ok && !failed_reader,
        "WAL replay fails closed on a non-EINTR read error");
  std::unique_ptr<Index> recovered_reader;
  const m4::Status recovered_status =
      Index::Open(raw_dir, replay_options, &recovered_reader);
  Check(recovered_status.ok && recovered_reader &&
            recovered_reader->GetStats().live_docs == 40,
        "WAL remains intact after replay read error and recovers on retry");
  recovered_reader.reset();

  m4::Options mixed_writer_options;
  mixed_writer_options.wal_compression_lz4 = true;
  std::unique_ptr<Index> mixed_writer;
  const m4::Status mixed_writer_status =
      Index::Open(raw_dir, mixed_writer_options, &mixed_writer);
  Check(mixed_writer_status.ok && mixed_writer,
        "open legacy WAL to append mixed compressed frame");
  if (mixed_writer) {
    Document mixed_document;
    mixed_document.text = "mixed legacy compressed recovery";
    Check(mixed_writer
              ->ApplyBatch({m4::Mutation(m4::MutationKind::UPSERT,
                                         "mixed-frame", mixed_document)})
              .ok,
          "append compressed record after legacy WAL frame");
    mixed_writer.reset();
  }
  m4::OpenDiagnostics mixed_diagnostics;
  std::unique_ptr<Index> mixed_reader;
  const m4::Status mixed_status =
      Index::Open(raw_dir, replay_options, &mixed_reader, &mixed_diagnostics);
  Check(mixed_status.ok && mixed_reader &&
            mixed_reader->GetStats().live_docs == 41 &&
            mixed_diagnostics.wal_replay_record_count == 2,
        "mixed legacy/LZ4 WAL frames replay in order");
  mixed_reader.reset();

  const std::string corrupt_dir = root + "/corrupt-checksum";
  fs::copy(compressed_dir, corrupt_dir, fs::copy_options::recursive, ec);
  Check(!ec, "copy compressed WAL for checksum test");
  {
    std::fstream wal(fs::path(corrupt_dir) / "wal.log",
                     std::ios::binary | std::ios::in | std::ios::out);
    wal.seekg(20);
    char checksum_byte = 0;
    wal.read(&checksum_byte, 1);
    wal.seekp(20);
    checksum_byte ^= 1;
    wal.write(&checksum_byte, 1);
    Check(static_cast<bool>(wal), "corrupt compressed WAL checksum");
  }
  std::string corrupt_frame_bytes;
  {
    std::ifstream wal(fs::path(corrupt_dir) / "wal.log", std::ios::binary);
    corrupt_frame_bytes.assign(std::istreambuf_iterator<char>(wal),
                               std::istreambuf_iterator<char>());
  }
  m4::Options checksum_reader;
  std::unique_ptr<Index> checksum_index;
  const m4::Status checksum_status =
      Index::Open(corrupt_dir, checksum_reader, &checksum_index);
  Check(!checksum_status.ok && !checksum_index,
        "complete bad compressed WAL checksum fails closed");
  {
    std::ifstream wal(fs::path(corrupt_dir) / "wal.log", std::ios::binary);
    const std::string after_recovery{std::istreambuf_iterator<char>(wal),
                                     std::istreambuf_iterator<char>()};
    Check(after_recovery == corrupt_frame_bytes,
          "complete corrupt WAL frame remains untouched for recovery");
  }

  const std::string corrupt_suffix_dir = root + "/corrupt-compressed-suffix";
  fs::copy(compressed_dir, corrupt_suffix_dir, fs::copy_options::recursive, ec);
  Check(!ec, "copy compressed WAL for valid-prefix suffix test");
  std::string good_frame;
  {
    std::ifstream wal(fs::path(compressed_dir) / "wal.log", std::ios::binary);
    good_frame.assign(std::istreambuf_iterator<char>(wal),
                      std::istreambuf_iterator<char>());
  }
  Check(good_frame.size() > 24, "compressed WAL fixture contains a full frame");
  if (good_frame.size() > 24) {
    good_frame[20] ^= 1; // Corrupt only the checksum of the duplicate suffix.
    std::ofstream wal(fs::path(corrupt_suffix_dir) / "wal.log",
                      std::ios::binary | std::ios::app);
    wal.write(good_frame.data(),
              static_cast<std::streamsize>(good_frame.size()));
    Check(static_cast<bool>(wal), "append checksum-corrupt compressed suffix");
  }
  std::string corrupt_suffix_bytes;
  {
    std::ifstream wal(fs::path(corrupt_suffix_dir) / "wal.log",
                      std::ios::binary);
    corrupt_suffix_bytes.assign(std::istreambuf_iterator<char>(wal),
                                std::istreambuf_iterator<char>());
  }
  m4::Options suffix_reader_options;
  suffix_reader_options.max_delta_docs = 100000;
  suffix_reader_options.max_delta_bytes = 1ull << 30;
  std::unique_ptr<Index> suffix_reader;
  const m4::Status suffix_status =
      Index::Open(corrupt_suffix_dir, suffix_reader_options, &suffix_reader);
  Check(!suffix_status.ok && !suffix_reader,
        "complete corrupt compressed suffix fails closed");
  {
    std::ifstream wal(fs::path(corrupt_suffix_dir) / "wal.log",
                      std::ios::binary);
    const std::string after_recovery{std::istreambuf_iterator<char>(wal),
                                     std::istreambuf_iterator<char>()};
    Check(after_recovery == corrupt_suffix_bytes,
          "failed recovery preserves valid prefix and corrupt suffix bytes");
  }
  {
    std::ifstream wal(fs::path(compressed_dir) / "wal.log", std::ios::binary);
    char magic[4]{};
    wal.read(magic, sizeof(magic));
    Check(wal.gcount() == 4 && std::string(magic, 4) == "2W4M",
          "compressed WAL frame has versioned little-endian magic");
  }

  // Legacy mode must auto-detect and replay compressed frames, then append a
  // legacy frame. A compressed-mode reopen must accept this mixed WAL.
  m4::Options legacy_reader;
  legacy_reader.max_delta_docs = 100000;
  legacy_reader.max_delta_bytes = 1ull << 30;
  std::unique_ptr<Index> index;
  Check(Index::Open(compressed_dir, legacy_reader, &index).ok,
        "legacy-option reader opens compressed WAL");
  if (!index)
    return;
  Check(index->GetStats().live_docs == 40, "compressed WAL replay count");
  m4::Document extra;
  extra.text = "mixed legacy frame gamma";
  Check(index->Upsert("mixed-legacy", extra).ok,
        "legacy-option reader appends legacy frame");
  index.reset();

  m4::Options compressed_reader = legacy_reader;
  compressed_reader.wal_compression_lz4 = true;
  Check(Index::Open(compressed_dir, compressed_reader, &index).ok,
        "compressed-option reader opens mixed WAL");
  if (!index)
    return;
  Check(index->GetStats().live_docs == 41, "mixed WAL replay count");
  m4::SearchResult result;
  Check(index->SearchFts("gamma", 10, &result).ok && result.hits.size() == 1 &&
            result.hits[0].pk == "mixed-legacy",
        "mixed WAL replay query");
  index.reset();
  {
    std::ofstream wal(fs::path(compressed_dir) / "wal.log",
                      std::ios::binary | std::ios::app);
    wal.write("2W4", 3); // A torn prefix of the compressed-frame magic.
  }
  Check(Index::Open(compressed_dir, compressed_reader, &index).ok,
        "compressed WAL torn suffix repair");
  Check(index && index->GetStats().live_docs == 41,
        "compressed WAL survives torn suffix repair");
  index.reset();

  // Hard-crash immediately after a compressed frame is fsynced, then recover
  // it with compression disabled to validate the durability boundary.
  const std::string crash_dir = root + "/crash-after-compressed-sync";
  fs::remove_all(crash_dir);
  std::unique_ptr<Index> seeded;
  Check(Index::Open(crash_dir, compressed_reader, &seeded).ok,
        "compressed crash seed open");
  Document base;
  base.text = "durable base document";
  Check(seeded && seeded->Upsert("base", base).ok && seeded->Flush().ok,
        "compressed crash seed commit");
  seeded.reset();
  const pid_t child = ::fork();
  if (child == 0) {
    ::setenv("M4_FAULT", "batch_after_wal_sync", 1);
    std::unique_ptr<Index> child_index;
    if (!Index::Open(crash_dir, compressed_reader, &child_index).ok)
      ::_exit(10);
    Document recovered_doc;
    recovered_doc.text = std::string(4096, 'z') + " crash-survivor";
    const auto status = child_index->ApplyBatch(
        {{m4::MutationKind::UPSERT, "crash-survivor", recovered_doc}});
    ::_exit(status.ok ? 11 : 12);
  }
  int child_status = 0;
  Check(child > 0 && ::waitpid(child, &child_status, 0) == child &&
            WIFEXITED(child_status) && WEXITSTATUS(child_status) == 86,
        "compressed WAL reaches post-fsync crash point");
  m4::Options recovery_options;
  Check(Index::Open(crash_dir, recovery_options, &index).ok,
        "legacy-mode recovery opens compressed crash WAL");
  Check(index && index->GetStats().live_docs == 2,
        "compressed WAL survives process crash");
  Check(index && index->SearchFts("crash-survivor", 10, &result).ok &&
            result.hits.size() == 1 && result.hits[0].pk == "crash-survivor",
        "compressed WAL crash recovery query");
}

void TestAtomicMutationBatch(const std::string &root) {
  const m4::Options options{};
  auto doc = [](std::string text) {
    Document out;
    out.text = std::move(text);
    return out;
  };
  const std::vector<m4::Mutation> batch = {
      {m4::MutationKind::UPSERT, "same", doc("beta final")},
      {m4::MutationKind::DELETE, "same", {}},
      {m4::MutationKind::UPSERT, "same", doc("gamma final")},
      {m4::MutationKind::DELETE, "gone", {}},
      {m4::MutationKind::UPSERT, "new", doc("delta added")},
  };

  // Batch-stage diagnostics are per atomic API call and remain opt-in.
  {
    const std::string dir = root + "/diagnostics";
    fs::remove_all(dir);
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, options, &index).ok, "batch diagnostics open");
    m4::WriteDiagnostics diagnostics;
    const auto status = index->ApplyBatch(batch, &diagnostics);
    Check(status.ok, "batch diagnostics apply: " + status.message);
    const double timings[] = {
        diagnostics.normalize_ms, diagnostics.normalize_cpu_ms,
        diagnostics.state_mutex_wait_ms, diagnostics.state_mutex_wait_cpu_ms,
        diagnostics.state_mutex_hold_ms, diagnostics.state_mutex_hold_cpu_ms,
        diagnostics.wal_encode_ms, diagnostics.wal_encode_cpu_ms,
        diagnostics.wal_append_ms, diagnostics.wal_append_cpu_ms,
        diagnostics.wal_write_ms, diagnostics.wal_fsync_ms,
        diagnostics.query_mutex_wait_ms, diagnostics.query_mutex_wait_cpu_ms,
        diagnostics.apply_ms, diagnostics.apply_cpu_ms,
        diagnostics.auto_flush_ms, diagnostics.auto_flush_cpu_ms};
    for (double timing : timings) {
      Check(std::isfinite(timing) && timing >= 0.0,
            "batch diagnostics are finite and nonnegative");
    }
    Check(diagnostics.wal_write_ms + diagnostics.wal_fsync_ms <=
              diagnostics.wal_append_ms + 1e-3,
          "WAL write and fsync timings are contained in append duration");
    m4::WriteDiagnostics disabled;
    disabled.capture_timing = false;
    Check(index->ApplyBatch({{m4::MutationKind::UPSERT, "untimed",
                              doc("untimed batch")}}, &disabled).ok,
          "untimed batch apply");
    Check(disabled.state_mutex_hold_ms == 0.0 &&
              disabled.wal_append_ms == 0.0 && disabled.wal_write_ms == 0.0 &&
              disabled.wal_fsync_ms == 0.0 && disabled.apply_ms == 0.0,
          "disabled batch diagnostics stay zero");
    m4::WriteDiagnostics disabled_upsert;
    disabled_upsert.capture_timing = false;
    Check(index->Upsert("untimed-upsert", doc("untimed writer"),
                        &disabled_upsert).ok,
          "untimed Upsert");
    Check(!disabled_upsert.capture_timing &&
              disabled_upsert.wal_append_ms == 0.0 &&
              disabled_upsert.wal_write_ms == 0.0 &&
              disabled_upsert.wal_fsync_ms == 0.0,
          "disabled Upsert WAL diagnostics stay zero");

    m4::CompactionDiagnostics flush_diagnostics;
    Check(index->Flush(&flush_diagnostics).ok,
          "explicit flush diagnostics flush pending batch");
    const double flush_timings[] = {
        flush_diagnostics.total_ms, flush_diagnostics.total_cpu_ms,
        flush_diagnostics.state_mutex_wait_ms,
        flush_diagnostics.state_mutex_wait_cpu_ms,
        flush_diagnostics.state_mutex_hold_ms,
        flush_diagnostics.state_mutex_hold_cpu_ms,
        flush_diagnostics.query_mutex_wait_ms,
        flush_diagnostics.query_mutex_wait_cpu_ms,
        flush_diagnostics.pending_flush_ms,
        flush_diagnostics.pending_flush_cpu_ms,
        flush_diagnostics.flush_segment_build_ms,
        flush_diagnostics.flush_segment_build_cpu_ms,
        flush_diagnostics.flush_segment_write_ms,
        flush_diagnostics.flush_segment_write_cpu_ms,
        flush_diagnostics.flush_segment_open_ms,
        flush_diagnostics.flush_segment_open_cpu_ms,
        flush_diagnostics.flush_query_mutex_wait_ms,
        flush_diagnostics.flush_query_mutex_wait_cpu_ms,
        flush_diagnostics.flush_query_mutex_hold_ms,
        flush_diagnostics.flush_query_mutex_hold_cpu_ms,
        flush_diagnostics.flush_stats_append_ms,
        flush_diagnostics.flush_stats_append_cpu_ms,
        flush_diagnostics.flush_tombstone_write_ms,
        flush_diagnostics.flush_tombstone_write_cpu_ms,
        flush_diagnostics.flush_manifest_write_ms,
        flush_diagnostics.flush_manifest_write_cpu_ms,
        flush_diagnostics.flush_wal_reset_ms,
        flush_diagnostics.flush_wal_reset_cpu_ms,
        flush_diagnostics.flush_gc_ms,
        flush_diagnostics.flush_gc_cpu_ms,
        flush_diagnostics.flush_delta_reset_ms,
        flush_diagnostics.flush_delta_reset_cpu_ms};
    for (double timing : flush_timings) {
      Check(std::isfinite(timing) && timing >= 0.0,
            "explicit flush diagnostics are finite and nonnegative");
    }
    Check(flush_diagnostics.total_ms >= flush_diagnostics.pending_flush_ms &&
              flush_diagnostics.state_mutex_hold_ms >=
                  flush_diagnostics.pending_flush_ms &&
              flush_diagnostics.flush_query_mutex_hold_ms <=
                  flush_diagnostics.pending_flush_ms,
          "explicit flush phase and query-lock hold lie within outer durations");

    m4::CompactionDiagnostics disabled_flush;
    disabled_flush.capture_timing = false;
    Check(index->Upsert("untimed-flush", doc("flush without timers")).ok,
          "prepare untimed explicit flush");
    Check(index->Flush(&disabled_flush).ok, "untimed explicit flush");
    Check(disabled_flush.total_ms == 0.0 &&
              disabled_flush.state_mutex_hold_ms == 0.0 &&
              disabled_flush.pending_flush_ms == 0.0 &&
              disabled_flush.flush_manifest_write_ms == 0.0 &&
              disabled_flush.flush_query_mutex_hold_ms == 0.0,
          "disabled explicit flush diagnostics stay zero");
  }

  auto seed = [&](const std::string& dir) {
    fs::remove_all(dir);
    std::unique_ptr<Index> index;
    const auto opened = Index::Open(dir, options, &index);
    Check(opened.ok, "batch seed open: " + opened.message);
    Document same = doc("alpha old");
    Document gone = doc("beta gone");
    Check(index->Upsert("same", same).ok, "batch seed same");
    Check(index->Upsert("gone", gone).ok, "batch seed gone");
  };

  // Pre-WAL failure leaves the complete old state visible and replayable.
  {
    const std::string dir = root + "/before-wal";
    seed(dir);
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, options, &index).ok, "batch before-wal reopen");
    ::setenv("M4_FAULT", "batch_before_wal", 1);
    const auto failed = index->ApplyBatch(batch);
    ::unsetenv("M4_FAULT");
    Check(!failed.ok, "batch before-wal failpoint fires");
    Check(index->GetStats().live_docs == 2, "pre-WAL batch leaves old count");
    index.reset();
    Check(Index::Open(dir, options, &index).ok, "batch before-wal recovery");
    Check(index->GetStats().live_docs == 2, "pre-WAL batch absent after recovery");
  }

  // Mutation-count and encoded-byte caps are enforced before WAL publication.
  {
    const std::string dir = root + "/bounds";
    seed(dir);
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, options, &index).ok, "batch bounds reopen");
    std::vector<m4::Mutation> too_many;
    for (int i = 0; i < 257; ++i)
      too_many.emplace_back(m4::MutationKind::UPSERT, "cap-" + std::to_string(i), doc("bounded"));
    Check(!index->ApplyBatch(too_many).ok, "batch mutation count cap");
    Check(!index->ApplyBatch({{m4::MutationKind::UPSERT, "oversize",
                               doc(std::string((8u << 20) + 1, 'x'))}}).ok,
          "batch encoded byte cap");
    Check(index->GetStats().live_docs == 2 && index->GetStats().wal_fsyncs == 0,
          "rejected batches do not publish or sync");
  }

  // A process crash immediately after durable publication, and another after
  // the first in-memory operation, must both recover the complete batch.
  for (const std::string failpoint : {"batch_after_wal_sync",
                                      "batch_after_first_apply"}) {
    const std::string dir = root + "/" + failpoint;
    seed(dir);
    pid_t child = ::fork();
    if (child == 0) {
      ::setenv("M4_FAULT", failpoint.c_str(), 1);
      std::unique_ptr<Index> index;
      if (!Index::Open(dir, options, &index).ok) ::_exit(10);
      const auto status = index->ApplyBatch(batch);
      ::_exit(status.ok ? 11 : 12);
    }
    int child_status = 0;
    const bool waited = child > 0 && ::waitpid(child, &child_status, 0) == child;
    Check(waited, failpoint + " child wait");
    Check(waited && WIFEXITED(child_status) &&
              WEXITSTATUS(child_status) ==
                  (failpoint == "batch_after_wal_sync" ? 86 : 87),
          failpoint + " crash point reached");

    std::unique_ptr<Index> recovered;
    const auto opened = Index::Open(dir, options, &recovered);
    Check(opened.ok, failpoint + " recovery open: " + opened.message);
    if (!opened.ok) continue;
    Check(recovered->GetStats().live_docs == 2,
          failpoint + " recovers final live count");
    m4::SearchResult all;
    Check(recovered->SearchFilter(m4::FilterExpr::All(), &all).ok,
          failpoint + " all-doc query");
    std::set<std::string> pks;
    std::set<m4::DocId> ids;
    for (const auto& hit : all.hits) {
      pks.insert(hit.pk);
      ids.insert(hit.doc_id);
    }
    Check(pks == std::set<std::string>({"new", "same"}),
          failpoint + " no partial visibility after replay");
    Check(ids.size() == 2 && *ids.begin() > 2,
          failpoint + " DocIds are not reused");
    m4::SearchResult query;
    Check(recovered->SearchFts("gamma", 10, &query).ok &&
              query.hits.size() == 1 && query.hits[0].pk == "same",
          failpoint + " same-PK update/delete/update final version");
    Check(recovered->SearchFts("alpha", 10, &query).ok && query.hits.empty(),
          failpoint + " old same-PK version remains tombstoned");
  }

  // A torn suffix is discarded; subsequent appends remain replayable.
  {
    const std::string dir = root + "/torn-tail";
    fs::remove_all(dir);
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, options, &index).ok, "torn-tail open");
    std::vector<m4::Mutation> first = {
        {m4::MutationKind::UPSERT, "a", doc("alpha")},
        {m4::MutationKind::UPSERT, "b", doc("beta")},
    };
    Check(index->ApplyBatch(first).ok, "torn-tail first batch");
    index.reset();
    {
      std::ofstream wal(fs::path(dir) / "wal.log", std::ios::binary | std::ios::app);
      wal.write("1W4", 3); // A torn prefix of the sequence-bound raw-frame magic.
    }
    Check(Index::Open(dir, options, &index).ok, "torn-tail replay repairs suffix");
    Check(index->GetStats().live_docs == 2, "torn-tail retains complete batch");
    Check(index->ApplyBatch({{m4::MutationKind::UPSERT, "c", doc("gamma")}}).ok,
          "append after torn-tail repair");
    index.reset();
    Check(Index::Open(dir, options, &index).ok, "reopen after repaired append");
    Check(index->GetStats().live_docs == 3,
          "post-repair record is not hidden behind torn bytes");
  }

  // Readers must observe the pre- or post-batch snapshot, never a prefix.
  {
    const std::string dir = root + "/visibility";
    fs::remove_all(dir);
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, options, &index).ok, "visibility open");
    Check(index->Upsert("base-0", doc("base")).ok, "visibility seed 0");
    Check(index->Upsert("base-1", doc("base")).ok, "visibility seed 1");
    Check(index->GetStats().wal_fsyncs == 2,
          "single-document calls each sync their WAL record");
    std::vector<m4::Mutation> many;
    for (int i = 0; i < 256; ++i) {
      many.push_back({m4::MutationKind::UPSERT, "batch-" + std::to_string(i),
                      doc(std::string(2048, 'x') + " token")});
    }
    std::atomic<bool> started{false};
    std::atomic<bool> done{false};
    std::atomic<int> bad_snapshot{0};
    std::thread reader([&] {
      started.store(true, std::memory_order_release);
      while (!done.load(std::memory_order_acquire)) {
        m4::SearchResult all;
        if (!index->SearchFilter(m4::FilterExpr::All(), &all).ok ||
            (all.hits.size() != 2 && all.hits.size() != 258)) {
          bad_snapshot.fetch_add(1, std::memory_order_relaxed);
        }
      }
    });
    while (!started.load(std::memory_order_acquire)) std::this_thread::yield();
    Check(index->ApplyBatch(many).ok, "visibility atomic batch");
    const auto write_stats = index->GetStats();
    Check(write_stats.wal_fsyncs == 3,
          "full durable batch uses one WAL sync");
    Check(write_stats.wal_bytes_appended > 0 &&
              write_stats.wal_bytes_appended == write_stats.wal_bytes,
          "WAL append-byte counter matches bytes retained before checkpoint");
    done.store(true, std::memory_order_release);
    reader.join();
    Check(bad_snapshot.load() == 0, "readers see only batch boundaries");
  }

  // Overlapping Upserts may share one bounded WAL transaction. Readers must
  // observe only the pre- or post-group snapshot; one durable batch must replay
  // completely after restart.
  {
    const std::string dir = root + "/writer-group-commit";
    fs::remove_all(dir);
    m4::Options grouped = options;
    grouped.writer_group_commit_max_batch_size = 4;
    grouped.writer_group_commit_max_delay_us = 50000;
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, grouped, &index).ok, "group-commit open");

    std::atomic<bool> reader_done{false};
    std::atomic<int> reader_errors{0};
    std::thread reader([&] {
      while (!reader_done.load(std::memory_order_acquire)) {
        m4::SearchResult all;
        if (!index->SearchFilter(m4::FilterExpr::All(), &all).ok ||
            (all.hits.size() != 0 && all.hits.size() != 4)) {
          reader_errors.fetch_add(1, std::memory_order_relaxed);
        }
      }
    });
    std::atomic<int> ready{0};
    std::atomic<bool> start{false};
    std::atomic<int> writer_errors{0};
    std::vector<std::thread> writers;
    for (int i = 0; i < 4; ++i) {
      writers.emplace_back([&, i] {
        ready.fetch_add(1, std::memory_order_release);
        while (!start.load(std::memory_order_acquire)) std::this_thread::yield();
        if (!index->Upsert("group-" + std::to_string(i), doc("group token")).ok)
          writer_errors.fetch_add(1, std::memory_order_relaxed);
      });
    }
    while (ready.load(std::memory_order_acquire) != 4) std::this_thread::yield();
    start.store(true, std::memory_order_release);
    for (auto& writer : writers) writer.join();
    reader_done.store(true, std::memory_order_release);
    reader.join();
    Check(writer_errors.load() == 0, "group-commit writers succeed");
    Check(reader_errors.load() == 0, "readers see atomic group boundaries");
    Check(index->GetStats().live_docs == 4 && index->GetStats().wal_fsyncs == 1,
          "four concurrent Upserts share one durable WAL sync");
    index.reset();
    Check(Index::Open(dir, grouped, &index).ok, "group-commit reopen");
    m4::SearchResult all;
    Check(index->SearchFilter(m4::FilterExpr::All(), &all).ok &&
              all.hits.size() == 4,
          "group-commit WAL replay restores every Upsert");
  }

  // Separate-frame group commit shares the fsync while preserving one reader
  // boundary per Upsert. Pause after the first apply to force an observable
  // one-document prefix, then replay two individually compressed WAL frames.
  {
    const std::string dir = root + "/writer-group-per-upsert-records";
    const std::string barrier = dir + "-barrier";
    fs::remove_all(dir);
    fs::remove_all(barrier);
    ::setenv("M4_TEST_WRITER_GROUP_STEP_BARRIER", barrier.c_str(), 1);
    m4::Options grouped = options;
    grouped.writer_group_commit_max_batch_size = 2;
    grouped.writer_group_commit_max_delay_us = 50000;
    grouped.writer_group_commit_per_upsert_records = true;
    grouped.wal_compression_lz4 = true;
    std::unique_ptr<Index> index;
    const bool opened = Index::Open(dir, grouped, &index).ok;
    Check(opened, "per-Upsert group open");
    if (opened) {
      std::atomic<int> ready{0};
      std::atomic<bool> start{false};
      std::atomic<int> writer_errors{0};
      std::vector<std::thread> writers;
      for (int i = 0; i < 2; ++i) {
        writers.emplace_back([&, i] {
          ready.fetch_add(1, std::memory_order_release);
          while (!start.load(std::memory_order_acquire)) std::this_thread::yield();
          const std::string text = std::string(2048, i == 0 ? 'x' : 'y') +
                                   " group token";
          if (!index->Upsert("separate-" + std::to_string(i), doc(text)).ok)
            writer_errors.fetch_add(1, std::memory_order_relaxed);
        });
      }
      while (ready.load(std::memory_order_acquire) != 2) std::this_thread::yield();
      start.store(true, std::memory_order_release);
      const auto deadline = std::chrono::steady_clock::now() +
                            std::chrono::seconds(5);
      std::error_code ec;
      const fs::path applied = fs::path(barrier) / "first-upsert-applied";
      while (!fs::exists(applied, ec) && std::chrono::steady_clock::now() < deadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
      const bool paused_after_first = fs::exists(applied, ec);
      m4::SearchResult prefix;
      const bool prefix_read = index->SearchFilter(m4::FilterExpr::All(), &prefix).ok;
      Check(paused_after_first && prefix_read && prefix.hits.size() == 1,
            "separate-frame group exposes one complete Upsert before the next");
      fs::create_directories(barrier, ec);
      std::ofstream release(fs::path(barrier) / "continue", std::ios::binary);
      release << "continue\n";
      release.close();
      for (auto& writer : writers) writer.join();
      ::unsetenv("M4_TEST_WRITER_GROUP_STEP_BARRIER");
      Check(writer_errors.load() == 0, "per-Upsert group callers succeed");
      const auto stats = index->GetStats();
      Check(stats.live_docs == 2 && stats.wal_fsyncs == 1,
            "two separate Upserts share one durable WAL sync");
      std::ifstream wal(fs::path(dir) / "wal.log", std::ios::binary);
      const std::string contents((std::istreambuf_iterator<char>(wal)),
                                 std::istreambuf_iterator<char>());
      size_t compressed_frames = 0;
      for (size_t pos = 0; (pos = contents.find("2W4M", pos)) != std::string::npos;
           pos += 4) {
        ++compressed_frames;
      }
      Check(compressed_frames == 2,
            "separate-record group writes two individually compressed WAL frames");
      index.reset();
      Check(Index::Open(dir, grouped, &index).ok,
            "separate-record compressed group replay open");
      if (index) {
        m4::SearchResult replayed;
        Check(index->SearchFilter(m4::FilterExpr::All(), &replayed).ok &&
                  replayed.hits.size() == 2,
              "separate compressed frames replay independently");
      }
    }
    ::unsetenv("M4_TEST_WRITER_GROUP_STEP_BARRIER");
  }

  // Hold the writer in a two-request group while more than the bounded queue
  // capacity enters. Producers must backpressure rather than lose durable ACKs;
  // after orderly shutdown, every acknowledged Upsert must replay.
  {
    const std::string dir = root + "/writer-group-queue-saturation";
    const std::string barrier = dir + "-barrier";
    fs::remove_all(dir);
    fs::remove_all(barrier);
    ::setenv("M4_TEST_WRITER_GROUP_STEP_BARRIER", barrier.c_str(), 1);
    m4::Options grouped = options;
    grouped.writer_group_commit_max_batch_size = 2;
    grouped.writer_group_commit_max_delay_us = 50000;
    grouped.writer_group_commit_per_upsert_records = true;
    std::unique_ptr<Index> index;
    const bool opened = Index::Open(dir, grouped, &index).ok;
    Check(opened, "saturated writer-group open");
    if (opened) {
      constexpr int kWriters = 24;  // queue holds at most 8 plus the active group
      std::atomic<int> ready{0};
      std::atomic<int> completed{0};
      std::atomic<int> errors{0};
      std::vector<std::thread> writers;
      for (int i = 0; i < kWriters; ++i) {
        writers.emplace_back([&, i] {
          ready.fetch_add(1, std::memory_order_release);
          const std::string pk = "saturated-" + std::to_string(i);
          if (!index->Upsert(pk, doc("saturation token")).ok)
            errors.fetch_add(1, std::memory_order_relaxed);
          completed.fetch_add(1, std::memory_order_release);
        });
      }
      const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
      std::error_code ec;
      const fs::path applied = fs::path(barrier) / "first-upsert-applied";
      while ((!fs::exists(applied, ec) || ready.load(std::memory_order_acquire) != kWriters) &&
             std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
      }
      const bool saturated = fs::exists(applied, ec) &&
          ready.load(std::memory_order_acquire) == kWriters &&
          completed.load(std::memory_order_acquire) == 0;
      Check(saturated, "bounded writer queue applies backpressure at saturation");
      fs::create_directories(barrier, ec);
      std::ofstream release(fs::path(barrier) / "continue", std::ios::binary);
      release << "continue\\n";
      release.close();
      for (auto& writer : writers) writer.join();
      ::unsetenv("M4_TEST_WRITER_GROUP_STEP_BARRIER");
      Check(errors.load() == 0 && completed.load() == kWriters,
            "saturated writer queue eventually returns every durable ACK");
      index.reset();  // orderly shutdown after all callers have received ACKs
      Check(Index::Open(dir, grouped, &index).ok,
            "saturated writer-group recovery open");
      if (index) {
        m4::SearchResult replayed;
        Check(index->SearchFilter(m4::FilterExpr::All(), &replayed).ok &&
                  replayed.hits.size() == kWriters,
              "orderly shutdown/recovery retains all saturated-queue Upserts");
      }
    }
    ::unsetenv("M4_TEST_WRITER_GROUP_STEP_BARRIER");
  }

  // Duplicate PKs within one group must use the previous queued record's new
  // DocId as the next record's old DocId and leave exactly one live version.
  {
    const std::string dir = root + "/writer-group-duplicate-pk";
    fs::remove_all(dir);
    m4::Options grouped = options;
    grouped.writer_group_commit_max_batch_size = 2;
    grouped.writer_group_commit_max_delay_us = 50000;
    grouped.writer_group_commit_per_upsert_records = true;
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, grouped, &index).ok, "duplicate-PK group open");
    if (index) {
      std::atomic<int> ready{0};
      std::atomic<bool> start{false};
      std::atomic<int> errors{0};
      std::vector<std::thread> writers;
      for (const std::string text : {"first-token", "second-token"}) {
        writers.emplace_back([&, text] {
          ready.fetch_add(1, std::memory_order_release);
          while (!start.load(std::memory_order_acquire)) std::this_thread::yield();
          if (!index->Upsert("same-pk", doc(text)).ok)
            errors.fetch_add(1, std::memory_order_relaxed);
        });
      }
      while (ready.load(std::memory_order_acquire) != 2) std::this_thread::yield();
      start.store(true, std::memory_order_release);
      for (auto& writer : writers) writer.join();
      Check(errors.load() == 0 && index->GetStats().live_docs == 1 &&
                index->GetStats().wal_fsyncs == 1,
            "duplicate-PK group preserves one live version with shared fsync");
      index.reset();
      Check(Index::Open(dir, grouped, &index).ok,
            "duplicate-PK group replay open");
      if (index) {
        Check(index->GetStats().live_docs == 1,
              "duplicate-PK group replay retains one live version");
      }
    }
  }

  // A threshold-triggered flush must run after the complete group has been
  // applied, not between records where WAL reset could discard an un-applied
  // suffix that already shares the group's fsync.
  {
    const std::string dir = root + "/writer-group-auto-flush";
    fs::remove_all(dir);
    m4::Options grouped = options;
    grouped.max_delta_docs = 1;
    grouped.writer_group_commit_max_batch_size = 2;
    grouped.writer_group_commit_max_delay_us = 50000;
    grouped.writer_group_commit_per_upsert_records = true;
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, grouped, &index).ok, "group auto-flush open");
    if (index) {
      std::atomic<int> ready{0};
      std::atomic<bool> start{false};
      std::atomic<int> errors{0};
      std::vector<std::thread> writers;
      for (int i = 0; i < 2; ++i) {
        writers.emplace_back([&, i] {
          ready.fetch_add(1, std::memory_order_release);
          while (!start.load(std::memory_order_acquire)) std::this_thread::yield();
          if (!index->Upsert("auto-flush-" + std::to_string(i),
                             doc("auto flush token")).ok)
            errors.fetch_add(1, std::memory_order_relaxed);
        });
      }
      while (ready.load(std::memory_order_acquire) != 2) std::this_thread::yield();
      start.store(true, std::memory_order_release);
      for (auto& writer : writers) writer.join();
      Check(errors.load() == 0 && index->GetStats().live_docs == 2 &&
                index->GetStats().wal_fsyncs == 1,
            "auto-flush after a grouped sync publishes every Upsert");
      index.reset();
      Check(Index::Open(dir, grouped, &index).ok,
            "auto-flushed per-Upsert group reopen");
      if (index) {
        Check(index->GetStats().live_docs == 2,
              "auto-flush checkpoint retains every grouped Upsert");
      }
    }
  }

  // Once all individual frames share a durable fsync, crashes before or during
  // publication must recover only complete records, and after-sync recovery
  // must restore the entire group even if only its first record was applied.
  for (const auto& crash_case : std::vector<std::pair<std::string, int>>{
           {"writer_group_before_wal_sync", 92},
           {"writer_group_after_wal_sync", 90},
           {"writer_group_after_first_apply", 91}}) {
    const std::string dir = root + "/" + crash_case.first;
    fs::remove_all(dir);
    const pid_t child = ::fork();
    if (child == 0) {
      ::setenv("M4_FAULT", crash_case.first.c_str(), 1);
      m4::Options grouped = options;
      grouped.writer_group_commit_max_batch_size = 2;
      grouped.writer_group_commit_max_delay_us = 50000;
      grouped.writer_group_commit_per_upsert_records = true;
      grouped.wal_compression_lz4 = true;
      std::unique_ptr<Index> index;
      if (!Index::Open(dir, grouped, &index).ok) ::_exit(10);
      std::atomic<int> ready{0};
      std::atomic<bool> start{false};
      std::vector<std::thread> writers;
      for (int i = 0; i < 2; ++i) {
        writers.emplace_back([&, i] {
          ready.fetch_add(1, std::memory_order_release);
          while (!start.load(std::memory_order_acquire)) std::this_thread::yield();
          (void)index->Upsert("crash-" + std::to_string(i),
                              doc(std::string(2048, 'z') + " durable"));
        });
      }
      while (ready.load(std::memory_order_acquire) != 2) std::this_thread::yield();
      start.store(true, std::memory_order_release);
      for (auto& writer : writers) writer.join();
      ::_exit(12);
    }
    int child_status = 0;
    const bool waited = child > 0 && ::waitpid(child, &child_status, 0) == child;
    Check(waited && WIFEXITED(child_status) &&
              WEXITSTATUS(child_status) == crash_case.second,
          crash_case.first + " reaches its group-commit crash boundary");
    std::unique_ptr<Index> recovered;
    const bool reopened = Index::Open(dir, options, &recovered).ok;
    Check(reopened, crash_case.first + " WAL reopen succeeds");
    if (recovered) {
      const uint64_t live = recovered->GetStats().live_docs;
      if (crash_case.first == "writer_group_before_wal_sync") {
        Check(live <= 2, "pre-sync crash replays only complete group records");
      } else {
        Check(live == 2, "post-sync crash replays the entire individual-frame group");
      }
    }
  }

  // The per-Upsert mode must not silently provide a durability guarantee when
  // WAL fsync or WAL itself is disabled; retain the existing direct-path rules.
  for (const bool wal_enabled : {true, false}) {
    const std::string dir = root +
        (wal_enabled ? "/writer-group-no-fsync" : "/writer-group-no-wal");
    fs::remove_all(dir);
    m4::Options grouped = options;
    grouped.wal_enabled = wal_enabled;
    grouped.fsync_wal = false;
    grouped.writer_group_commit_max_batch_size = 2;
    grouped.writer_group_commit_max_delay_us = 50000;
    grouped.writer_group_commit_per_upsert_records = true;
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, grouped, &index).ok,
          wal_enabled ? "no-fsync group open" : "no-WAL group open");
    if (!index) continue;
    std::atomic<int> ready{0};
    std::atomic<bool> start{false};
    std::atomic<int> errors{0};
    std::vector<std::thread> writers;
    for (int i = 0; i < 2; ++i) {
      writers.emplace_back([&, i] {
        ready.fetch_add(1, std::memory_order_release);
        while (!start.load(std::memory_order_acquire)) std::this_thread::yield();
        if (!index->Upsert("non-durable-" + std::to_string(i),
                           doc("non durable token")).ok)
          errors.fetch_add(1, std::memory_order_relaxed);
      });
    }
    while (ready.load(std::memory_order_acquire) != 2) std::this_thread::yield();
    start.store(true, std::memory_order_release);
    for (auto& writer : writers) writer.join();
    const auto stats = index->GetStats();
    Check(errors.load() == 0 && stats.live_docs == 2 && stats.wal_fsyncs == 0,
          wal_enabled ? "fsync-disabled mode makes no durability claim or hidden sync"
                      : "WAL-disabled mode makes no durability claim or hidden sync");
    if (!wal_enabled) {
      Check(stats.wal_bytes == 0, "WAL-disabled mode appends no WAL records");
    } else {
      Check(stats.wal_bytes > 0, "fsync-disabled direct path retains WAL append accounting");
    }
  }

  // An append failure after one successful frame commits only that durable
  // prefix; the request whose frame was not appended fails, and the guard
  // requires reopen before accepting new work.
  {
    const std::string dir = root + "/writer-group-append-prefix-failure";
    fs::remove_all(dir);
    m4::Options grouped = options;
    grouped.writer_group_commit_max_batch_size = 2;
    grouped.writer_group_commit_max_delay_us = 50000;
    grouped.writer_group_commit_per_upsert_records = true;
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, grouped, &index).ok, "append-prefix group open");
    if (index) {
      ::setenv("M4_FAULT", "writer_group_append_error_after_first", 1);
      std::atomic<int> ready{0};
      std::atomic<bool> start{false};
      std::atomic<int> successes{0}, prefix_failures{0};
      std::vector<std::thread> writers;
      for (int i = 0; i < 2; ++i) {
        writers.emplace_back([&, i] {
          ready.fetch_add(1, std::memory_order_release);
          while (!start.load(std::memory_order_acquire)) std::this_thread::yield();
          const auto status = index->Upsert(
              "append-prefix-" + std::to_string(i), doc("append prefix"));
          if (status.ok) successes.fetch_add(1, std::memory_order_relaxed);
          else if (status.message.find("durable prefix") != std::string::npos)
            prefix_failures.fetch_add(1, std::memory_order_relaxed);
        });
      }
      while (ready.load(std::memory_order_acquire) != 2) std::this_thread::yield();
      start.store(true, std::memory_order_release);
      for (auto& writer : writers) writer.join();
      ::unsetenv("M4_FAULT");
      m4::SearchResult visible;
      Check(successes.load() == 1 && prefix_failures.load() == 1 &&
                index->SearchFilter(m4::FilterExpr::All(), &visible).ok &&
                visible.hits.size() == 1,
            "append failure returns one successful durable prefix and one unappended suffix");
      Check(!index->Upsert("append-after-error", doc("blocked")).ok,
            "append-prefix error blocks further mutation until reopen");
      index.reset();
      Check(Index::Open(dir, options, &index).ok,
            "append-prefix failure resolves through WAL reopen");
      Check(index && index->GetStats().live_docs == 1,
            "append-prefix reopen restores only the durable successful frame");
    }
  }

  // If application stops after a durable first record, return its success and
  // an explicit reopen-required error for the suffix; replay resolves the
  // second caller's unacknowledged, already-durable frame.
  {
    const std::string dir = root + "/writer-group-apply-prefix-failure";
    fs::remove_all(dir);
    m4::Options grouped = options;
    grouped.writer_group_commit_max_batch_size = 2;
    grouped.writer_group_commit_max_delay_us = 50000;
    grouped.writer_group_commit_per_upsert_records = true;
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, grouped, &index).ok, "apply-prefix group open");
    if (index) {
      ::setenv("M4_FAULT", "writer_group_apply_error_after_first", 1);
      std::atomic<int> ready{0};
      std::atomic<bool> start{false};
      std::atomic<int> successes{0}, reopen_failures{0};
      std::vector<std::thread> writers;
      for (int i = 0; i < 2; ++i) {
        writers.emplace_back([&, i] {
          ready.fetch_add(1, std::memory_order_release);
          while (!start.load(std::memory_order_acquire)) std::this_thread::yield();
          const auto status = index->Upsert(
              "apply-prefix-" + std::to_string(i), doc("apply prefix"));
          if (status.ok) successes.fetch_add(1, std::memory_order_relaxed);
          else if (status.message.find("suffix is durable") != std::string::npos)
            reopen_failures.fetch_add(1, std::memory_order_relaxed);
        });
      }
      while (ready.load(std::memory_order_acquire) != 2) std::this_thread::yield();
      start.store(true, std::memory_order_release);
      for (auto& writer : writers) writer.join();
      ::unsetenv("M4_FAULT");
      m4::SearchResult visible;
      Check(successes.load() == 1 && reopen_failures.load() == 1 &&
                index->SearchFilter(m4::FilterExpr::All(), &visible).ok &&
                visible.hits.size() == 1,
            "post-sync apply failure exposes only a complete visible prefix");
      Check(!index->ApplyBatch({{m4::MutationKind::UPSERT, "apply-blocked",
                                 doc("blocked")}}).ok && !index->Flush().ok,
            "post-sync apply failure blocks new batches/checkpoints until reopen");
      index.reset();
      Check(Index::Open(dir, options, &index).ok,
            "post-sync apply failure resolves through WAL reopen");
      Check(index && index->GetStats().live_docs == 2,
            "reopen replays the durable suffix after apply interruption");
    }
  }

  // Inject a shared-fsync failure. The result is explicitly uncertain, no
  // further mutation/checkpoint is allowed until reopen replays the WAL.
  {
    const std::string dir = root + "/writer-group-fsync-failure";
    fs::remove_all(dir);
    m4::Options grouped = options;
    grouped.writer_group_commit_max_batch_size = 2;
    grouped.writer_group_commit_max_delay_us = 50000;
    grouped.writer_group_commit_per_upsert_records = true;
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, grouped, &index).ok, "fsync-failure group open");
    if (index) {
      ::setenv("M4_FAULT", "writer_group_fsync_error", 1);
      std::atomic<int> ready{0};
      std::atomic<bool> start{false};
      std::atomic<int> failures{0};
      std::vector<std::thread> writers;
      for (int i = 0; i < 2; ++i) {
        writers.emplace_back([&, i] {
          ready.fetch_add(1, std::memory_order_release);
          while (!start.load(std::memory_order_acquire)) std::this_thread::yield();
          const auto status = index->Upsert("uncertain-" + std::to_string(i),
                                            doc("uncertain token"));
          if (!status.ok && status.message.find("outcome uncertain") != std::string::npos)
            failures.fetch_add(1, std::memory_order_relaxed);
        });
      }
      while (ready.load(std::memory_order_acquire) != 2) std::this_thread::yield();
      start.store(true, std::memory_order_release);
      for (auto& writer : writers) writer.join();
      ::unsetenv("M4_FAULT");
      Check(failures.load() == 2, "shared-fsync failure marks both ACKs uncertain");
      Check(!index->Upsert("after-failure", doc("blocked")).ok &&
                !index->Delete("uncertain-0").ok &&
                !index->ApplyBatch({{m4::MutationKind::UPSERT, "blocked-batch",
                                     doc("blocked")}}).ok &&
                !index->Flush().ok && !index->CompactNow().ok,
            "uncertain durable/apply state blocks all further mutations and checkpoints");
      index.reset();
      Check(Index::Open(dir, options, &index).ok,
            "fsync-failure state can be resolved by reopening WAL");
    }
  }

  // A crash after the group transaction is durable but before publication must
  // recover the complete group, not a prefix or an absent transaction.
  {
    const std::string dir = root + "/writer-group-commit-crash";
    seed(dir);
    pid_t child = ::fork();
    if (child == 0) {
      ::setenv("M4_FAULT", "batch_after_wal_sync", 1);
      m4::Options grouped = options;
      grouped.writer_group_commit_max_batch_size = 2;
      grouped.writer_group_commit_max_delay_us = 50000;
      std::unique_ptr<Index> index;
      if (!Index::Open(dir, grouped, &index).ok) ::_exit(10);
      if (!index->Upsert("group-crash", doc("durable group" )).ok) ::_exit(11);
      ::_exit(12);
    }
    int child_status = 0;
    const bool waited = child > 0 && ::waitpid(child, &child_status, 0) == child;
    Check(waited && WIFEXITED(child_status) && WEXITSTATUS(child_status) == 86,
          "group-commit crash occurs after WAL sync");
    std::unique_ptr<Index> recovered;
    Check(Index::Open(dir, options, &recovered).ok, "group-commit crash reopen");
    if (recovered) {
      m4::SearchResult result;
      Check(recovered->GetStats().live_docs == 3 &&
                recovered->SearchFts("durable", 10, &result).ok &&
                result.hits.size() == 1 && result.hits[0].pk == "group-crash",
            "group-commit crash recovers the complete durable write");
    }
  }

  // Concurrent writers to one logical PK must serialize into a single live
  // version; active readers must remain safe while compaction reclaims runs.
  {
    const std::string dir = root + "/same-pk-race";
    fs::remove_all(dir);
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, options, &index).ok, "same-PK race open");
    std::atomic<bool> reader_done{false};
    std::atomic<int> reader_errors{0};
    std::atomic<uint64_t> reader_queries{0};
    std::thread active_reader([&] {
      while (!reader_done.load(std::memory_order_acquire)) {
        m4::SearchResult all;
        m4::SearchResult fts;
        m4::SearchResult filtered_fts;
        if (!index->SearchFilter(m4::FilterExpr::All(), &all).ok ||
            !index->SearchFts("race", 10, &fts).ok ||
            !index->SearchFtsFiltered("race", 10, m4::FilterExpr::All(),
                                      &filtered_fts).ok ||
            all.hits.size() > 1 || fts.hits.size() > 1 ||
            filtered_fts.hits.size() > 1 ||
            (!fts.hits.empty() && fts.hits[0].pk != "race") ||
            (!filtered_fts.hits.empty() && filtered_fts.hits[0].pk != "race")) {
          reader_errors.fetch_add(1, std::memory_order_relaxed);
        }
        reader_queries.fetch_add(1, std::memory_order_release);
      }
    });
    while (reader_queries.load(std::memory_order_acquire) == 0)
      std::this_thread::yield();

    std::atomic<int> writer_errors{0};
    std::vector<std::thread> writers;
    for (int worker = 0; worker < 4; ++worker) {
      writers.emplace_back([&, worker] {
        for (int i = 0; i < 40; ++i) {
          m4::Mutation mutation;
          if ((worker + i) % 3 == 0) {
            mutation = {m4::MutationKind::DELETE, "race", {}};
          } else {
            mutation = {m4::MutationKind::UPSERT, "race",
                        doc("race token version")};
          }
          if (!index->ApplyBatch({mutation}).ok)
            writer_errors.fetch_add(1, std::memory_order_relaxed);
        }
      });
    }
    for (auto& writer : writers) writer.join();
    Check(writer_errors.load() == 0, "same-PK concurrent writers succeed");
    Check(index->GetStats().live_docs <= 1,
          "same-PK concurrent writers preserve one live version");

    Check(index->CompactNow().ok, "compact with active readers");
    Check(index->Delete("race").ok, "same-PK final delete");
    Check(index->CompactNow().ok, "tombstone GC with active readers");
    reader_done.store(true, std::memory_order_release);
    active_reader.join();
    Check(reader_errors.load() == 0,
          "active readers see consistent snapshots through compaction");
    index.reset();
    Check(Index::Open(dir, options, &index).ok,
          "same-PK race reopen after GC");
    m4::SearchResult all;
    Check(index->SearchFilter(m4::FilterExpr::All(), &all).ok && all.hits.empty(),
          "same-PK final tombstone survives GC and recovery");
  }
}

void TestStatsEventCoalescing(const std::string& root) {
  RefState ref;
  std::vector<m4::Mutation> batch;
  batch.reserve(128);
  for (int i = 0; i < 128; ++i) {
    Document d;
    d.text = "sharedtoken commontoken unique" + std::to_string(i);
    const std::string pk = "coalesce-" + std::to_string(i);
    AddDoc(&ref, pk, d);
    batch.push_back({m4::MutationKind::UPSERT, pk, std::move(d)});
  }

  uint64_t legacy_log_bytes = 0;
  uint64_t coalesced_log_bytes = 0;
  for (bool coalesce : {false, true}) {
    const std::string dir = root + (coalesce ? "/coalesced" : "/legacy");
    fs::remove_all(dir);
    m4::Options options;
    options.coalesce_stats_events = coalesce;
    options.stats_log_threshold = 1ull << 30;
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, options, &index).ok,
          "stats event coalescing open");
    Check(index->ApplyBatch(batch).ok, "stats event coalescing batch");
    Check(index->Flush().ok, "stats event coalescing flush");
    const auto before_reopen = index->GetStats();
    Check(before_reopen.live_docs == batch.size(),
          "stats event coalescing live-doc count");
    if (coalesce) coalesced_log_bytes = before_reopen.stats_log_bytes;
    else legacy_log_bytes = before_reopen.stats_log_bytes;
    index.reset();

    // Reopen with coalescing disabled to prove the on-disk delta stream is
    // independent of the writer-only experiment switch.
    options.coalesce_stats_events = false;
    Check(Index::Open(dir, options, &index).ok,
          "stats event coalescing reopen in legacy mode");
    Check(index->GetStats().live_docs == batch.size(),
          "stats event coalescing replayed live docs");
    CheckFts(*index, ref, "sharedtoken",
             MakeRefQuery({"sharedtoken"}, false), 10,
             "stats event coalescing replayed document frequency");
  }
  Check(coalesced_log_bytes < legacy_log_bytes / 2,
        "coalesced stats log is less than half legacy size: " +
            std::to_string(coalesced_log_bytes) + " vs " +
            std::to_string(legacy_log_bytes));

  uint64_t legacy_zero_net_log_bytes = 0;
  uint64_t coalesced_zero_net_log_bytes = 0;
  for (bool coalesce : {false, true}) {
    const std::string dir = root + (coalesce ? "/zero-net-coalesced"
                                             : "/zero-net-legacy");
    fs::remove_all(dir);
    m4::Options options;
    options.coalesce_stats_events = coalesce;
    options.stats_log_threshold = 1ull << 30;
    std::unique_ptr<Index> index;
    Check(Index::Open(dir, options, &index).ok,
          "zero-net stats coalescing open");
    RefState zero_net_ref;
    Document old_doc;
    old_doc.text = "oldcoalesced sharedcoalesced";
    Check(index->Upsert("zero-net", old_doc).ok,
          "zero-net stats initial upsert");
    AddDoc(&zero_net_ref, "zero-net", old_doc);
    Document replacement;
    replacement.text = "newcoalesced sharedcoalesced";
    Check(index->Upsert("zero-net", replacement).ok,
          "zero-net stats replacement upsert");
    AddDoc(&zero_net_ref, "zero-net", replacement);
    Check(index->Delete("zero-net").ok, "zero-net stats first delete");
    DeleteDoc(&zero_net_ref, "zero-net");
    Check(index->Upsert("zero-net", old_doc).ok,
          "zero-net stats reinsert");
    AddDoc(&zero_net_ref, "zero-net", old_doc);
    Check(index->Delete("zero-net").ok, "zero-net stats final delete");
    DeleteDoc(&zero_net_ref, "zero-net");
    Check(index->Flush().ok, "zero-net stats flush");
    Check(index->GetStats().live_docs == 0,
          "zero-net stats final live-doc count");
    const uint64_t log_bytes = index->GetStats().stats_log_bytes;
    if (coalesce) coalesced_zero_net_log_bytes = log_bytes;
    else legacy_zero_net_log_bytes = log_bytes;
    index.reset();

    options.coalesce_stats_events = false;
    Check(Index::Open(dir, options, &index).ok,
          "zero-net stats reopen in legacy mode");
    Check(index->GetStats().live_docs == 0,
          "zero-net stats replayed live-doc count");
    CheckFts(*index, zero_net_ref, "oldcoalesced",
             MakeRefQuery({"oldcoalesced"}, false), 10,
             "zero-net stats replayed removed old term");
    CheckFts(*index, zero_net_ref, "newcoalesced",
             MakeRefQuery({"newcoalesced"}, false), 10,
             "zero-net stats replayed removed replacement term");
  }
  Check(legacy_zero_net_log_bytes > 0,
        "legacy zero-net sequence persists stats events");
  Check(coalesced_zero_net_log_bytes == 0,
        "coalesced zero-net sequence omits all stats events");

  // A crash after WAL durability but before in-memory apply must recover the
  // complete batch with stats coalescing enabled, then persist/reopen it.
  const std::string crash_dir = root + "/coalesced-crash-after-wal-sync";
  fs::remove_all(crash_dir);
  m4::Options coalesced_options;
  coalesced_options.coalesce_stats_events = true;
  coalesced_options.stats_log_threshold = 1ull << 30;
  const pid_t child = ::fork();
  if (child == 0) {
    ::setenv("M4_FAULT", "batch_after_wal_sync", 1);
    std::unique_ptr<Index> child_index;
    if (!Index::Open(crash_dir, coalesced_options, &child_index).ok)
      ::_exit(10);
    const auto status = child_index->ApplyBatch(batch);
    ::_exit(status.ok ? 11 : 12);
  }
  int child_status = 0;
  Check(child > 0 && ::waitpid(child, &child_status, 0) == child &&
            WIFEXITED(child_status) && WEXITSTATUS(child_status) == 86,
        "coalesced batch crashes after WAL sync");

  std::unique_ptr<Index> recovered;
  Check(Index::Open(crash_dir, coalesced_options, &recovered).ok,
        "coalesced batch WAL recovery open");
  m4::SearchResult recovered_docs;
  Check(recovered && recovered->GetStats().live_docs == batch.size() &&
            recovered->SearchFts("sharedtoken", 200, &recovered_docs).ok &&
            recovered_docs.hits.size() == batch.size(),
        "coalesced crash recovery restores every durable document and term");
  Check(recovered && recovered->Flush().ok,
        "coalesced crash recovery flushes stats");
  Check(recovered && recovered->GetStats().stats_log_bytes > 0,
        "coalesced crash recovery persists nonzero stats events");
  recovered.reset();

  coalesced_options.coalesce_stats_events = false;
  Check(Index::Open(crash_dir, coalesced_options, &recovered).ok,
        "coalesced crash output reopens with legacy writer setting");
  Check(recovered && recovered->GetStats().live_docs == batch.size() &&
            recovered->SearchFts("sharedtoken", 200, &recovered_docs).ok &&
            recovered_docs.hits.size() == batch.size(),
        "coalesced crash output preserves stats after legacy-setting reopen");
}

void TestBoundedRuns(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.max_delta_docs = 8;
  opt.max_runs_per_level = 2;
  opt.max_total_runs = 4;
  opt.stats_log_threshold = 1024;
  std::unique_ptr<Index> index;
  m4::Status s = Index::Open(dir, opt, &index);
  Check(s.ok, "bounded open");
  RefState ref;
  std::mt19937 rng(7);
  for (int i = 0; i < 600; ++i) {
    const std::string pk = "b-" + std::to_string(i);
    Document d;
    d.text = RandomText(&rng, 30);
    AddDoc(&ref, pk, d);
    Check(index->Upsert(pk, d).ok, "bounded upsert");
    if (i % 9 == 0) index->Flush();
  }
  Check(index->Flush().ok, "bounded flush");
  const auto st = index->GetStats();
  Check(st.runs <= opt.max_total_runs,
        "runs bounded: " + std::to_string(st.runs));
  CheckFts(*index, ref, "w4", MakeRefQuery({"w4"}, false), 10,
           "bounded query");
}

void TestStatIndependentBounds(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.max_delta_docs = 32;
  std::unique_ptr<Index> index;
  m4::Status s = Index::Open(dir, opt, &index);
  Check(s.ok, "bounds open");
  RefState ref;
  for (int i = 0; i < 60; ++i) {
    const std::string pk = "g-" + std::to_string(i);
    Document d;
    const int tf = (i % 7) + 1;
    d.text.clear();
    for (int k = 0; k < tf; ++k) d.text += "growth ";
    d.text += "filler" + std::to_string(i % 10);
    AddDoc(&ref, pk, d);
    Check(index->Upsert(pk, d).ok, "bounds upsert");
  }
  Check(index->Flush().ok, "bounds flush");
  // Add many documents that do not contain the term. IDF grows; a stored
  // IDF-weighted block bound would now be stale.
  std::mt19937 rng(11);
  for (int i = 0; i < 3000; ++i) {
    const std::string pk = "f-" + std::to_string(i);
    Document d;
    d.text = RandomText(&rng, 50);
    AddDoc(&ref, pk, d);
    Check(index->Upsert(pk, d).ok, "bounds filler");
  }
  Check(index->Flush().ok, "bounds flush 2");
  CheckFts(*index, ref, "growth", MakeRefQuery({"growth"}, false), 20,
           "bounds after corpus growth");
}


void TestHybrid(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options opt;
  std::unique_ptr<Index> index;
  m4::Status s = Index::Open(dir, opt, &index);
  Check(s.ok, "hybrid open");
  auto put = [&](const std::string& pk, const std::string& text) {
    Document d;
    d.text = text;
    Check(index->Upsert(pk, d).ok, "hybrid upsert");
  };
  put("a", "alpha beta");
  put("b", "alpha");
  put("c", "beta");
  put("d", "alpha beta gamma");
  put("e", "gamma");
  Check(index->Flush().ok, "hybrid flush");

  const std::unordered_map<std::string, float> vec = {
      {"a", 0.1f}, {"b", 0.9f}, {"c", 0.5f}, {"d", 0.2f}, {"e", 0.95f}};
  m4::SearchResult out;
  s = index->SearchHybrid("alpha beta", 3, 0.5, vec, &out);
  Check(s.ok, "hybrid search");
  Check(!out.hits.empty(), "hybrid non-empty");
  Check(out.examined > 0, "hybrid reports lexical postings examined");
  Check(out.hits.size() <= 3, "hybrid top-k bound");
  // The blend must be deterministic and sorted.
  for (size_t i = 1; i < out.hits.size(); ++i) {
    Check(out.hits[i - 1].score >= out.hits[i].score, "hybrid sorted");
  }
  // Vector-only e cannot appear before a strong fused result.
  for (const auto& h : out.hits) {
    Check(vec.count(h.pk) != 0, "hybrid pk domain");
  }

  out.hits = {m4::Hit{999, "stale", 1.0f}};
  out.examined = 99;
  s = index->SearchHybrid("no_such_term", 2, 0.5, vec, &out);
  Check(s.ok, "hybrid vector-only status");
  Check(out.hits.size() == 2 && out.hits[0].pk == "e" &&
            out.hits[1].pk == "b",
        "hybrid returns vector-only candidates when FTS has no hits");
  Check(out.examined == 0, "hybrid clears stale examined count");

  out.hits = {m4::Hit{999, "stale", 1.0f}};
  out.examined = 99;
  s = index->SearchHybrid("alpha", 0, 0.5, vec, &out);
  Check(s.ok && out.hits.empty() && out.examined == 0,
        "hybrid top-k zero clears prior results");
}


void TestCompactionBudget(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.max_delta_docs = 1000;
  opt.max_total_runs = 12;
  opt.max_runs_per_level = 4;
  opt.compaction_memory_limit_bytes = 2048;
  std::unique_ptr<Index> index;
  m4::Status s = Index::Open(dir, opt, &index);
  Check(s.ok, "budget open");
  for (int i = 0; i < 300; ++i) {
    Document d;
    d.text = "term" + std::to_string(i) + " common";
    Check(index->Upsert("k" + std::to_string(i), d).ok, "budget upsert");
    if (i == 149) Check(index->Flush().ok, "budget flush 1");
  }
  Check(index->Flush().ok, "budget flush 2");
  const uint64_t runs_before = index->GetStats().runs;
  m4::Status compact = index->CompactNow();
  Check(!compact.ok, "budget compaction must fail");
  Check(compact.message.find("budget") != std::string::npos ||
            compact.message.find("memory") != std::string::npos,
        "budget error identifies memory");
  Check(index->GetStats().runs == runs_before,
        "failed budget compaction keeps old runs");
  m4::SearchResult r;
  Check(index->SearchFts("common", 5, &r).ok, "budget search after failure");
  Check(!r.hits.empty(), "budget index still searchable");
}


void TestSnapshotCompactionPrototype(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.max_delta_docs = 1000;
  opt.tombstone_compact_ratio = 2.0;
  std::unique_ptr<Index> index;
  Check(Index::Open(dir, opt, &index).ok, "snapshot prototype open");

  Document alpha;
  alpha.text = "alpha";
  m4::WriteDiagnostics write_diagnostics;
  Check(index->Upsert("a", alpha, &write_diagnostics).ok,
        "snapshot prototype upsert alpha");
  Check(std::isfinite(write_diagnostics.state_mutex_hold_ms) &&
            write_diagnostics.state_mutex_hold_ms > 0.0 &&
            write_diagnostics.state_mutex_hold_ms >=
                write_diagnostics.wal_append_ms &&
            std::isfinite(write_diagnostics.wal_write_ms) &&
            std::isfinite(write_diagnostics.wal_fsync_ms) &&
            write_diagnostics.wal_write_ms >= 0.0 &&
            write_diagnostics.wal_fsync_ms >= 0.0 &&
            write_diagnostics.wal_write_ms +
                    write_diagnostics.wal_fsync_ms <=
                write_diagnostics.wal_append_ms + 1e-3 &&
            std::isfinite(write_diagnostics.state_mutex_hold_cpu_ms) &&
            write_diagnostics.state_mutex_hold_cpu_ms >= 0.0,
        "writer diagnostics measure state-lock hold wall and CPU time");
  Check(index->Flush().ok, "snapshot prototype flush alpha");
  Document beta;
  beta.text = "beta";
  Check(index->Upsert("b", beta).ok, "snapshot prototype upsert beta");
  Check(index->Flush().ok, "snapshot prototype flush beta");
  Check(index->GetStats().runs == 2, "snapshot prototype starts with two runs");

  const fs::path barrier = fs::path(dir) / "stream-merge-barrier";
  fs::remove_all(barrier);
  Check(::setenv("M4_TEST_SNAPSHOT_MERGE_READ_BARRIER", barrier.c_str(), 1) == 0,
        "snapshot prototype set stream-merge barrier");
  std::atomic<bool> compaction_done{false};
  m4::Status compaction_status;
  m4::CompactionDiagnostics concurrent_compaction_diagnostics;
  concurrent_compaction_diagnostics.capture_timing = true;
  std::thread compactor([&] {
    compaction_status = index->CompactNowSnapshotPrototype(
        &concurrent_compaction_diagnostics);
    compaction_done.store(true);
  });
  auto wait_for = [](const fs::path& path, const std::atomic<bool>& done,
                     std::chrono::seconds timeout) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    std::error_code ec;
    while (!fs::exists(path, ec) && !done.load() &&
           std::chrono::steady_clock::now() < deadline) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return fs::exists(path, ec);
  };
  const bool run_ready = wait_for(barrier / "merge-read-ready", compaction_done,
                                  std::chrono::seconds(5));
  Check(run_ready,
        "snapshot prototype pauses after reading an FTS term with state lock released");
  if (run_ready) {
    const m4::Status overlapping = index->CompactNowSnapshotPrototype();
    Check(!overlapping.ok &&
              overlapping.message.find("another compaction build is active") !=
                  std::string::npos,
          "snapshot prototype bounds compaction builds to one per index");
    std::this_thread::sleep_for(std::chrono::milliseconds(40));
  }

  std::atomic<bool> writer_done{false};
  m4::Status writer_status;
  std::thread writer;
  if (run_ready) {
    writer = std::thread([&] {
      writer_status = index->Delete("b");
      writer_done.store(true);
    });
    const auto writer_deadline = std::chrono::steady_clock::now() +
                                 std::chrono::seconds(2);
    while (!writer_done.load() &&
           std::chrono::steady_clock::now() < writer_deadline) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    Check(writer_done.load(),
          "writer completes while snapshot compaction is building");
    if (writer_done.load()) {
      Check(writer_status.ok, "snapshot prototype concurrent delete succeeds");
      m4::SearchResult result;
      Check(index->SearchFts("alpha", 5, &result).ok &&
                result.hits.size() == 1 && result.hits[0].pk == "a",
            "unmodified input remains query-visible during snapshot build");
      Check(index->SearchFts("beta", 5, &result).ok && result.hits.empty(),
            "concurrent delete is query-visible before compaction publication");
    }
    std::ofstream release(barrier / "writer-finished", std::ios::binary);
    release << "done\n";
  }
  if (writer.joinable()) writer.join();
  compactor.join();
  ::unsetenv("M4_TEST_SNAPSHOT_MERGE_READ_BARRIER");
  Check(!compaction_status.ok &&
            compaction_status.message.find("superseded") != std::string::npos,
        "concurrent mutation aborts stale snapshot publication");
  if (run_ready) {
    Check(concurrent_compaction_diagnostics.merge_build_ms >= 30.0,
          "timed snapshot build includes the deliberate unlocked barrier hold");
    Check(concurrent_compaction_diagnostics.total_ms -
                  concurrent_compaction_diagnostics.state_mutex_hold_ms >=
              30.0,
          "state-mutex hold timing excludes the unlocked merge-build barrier");
  }
  Check(index->GetStats().runs == 2,
        "aborted snapshot compaction preserves the current runs");
  Check(!fs::exists(fs::path(dir) / "run-3.seg"),
        "aborted snapshot output is removed");

  m4::CompactionDiagnostics compaction_diagnostics;
  const m4::Status stable_compaction =
      index->CompactNowSnapshotPrototype(&compaction_diagnostics);
  Check(stable_compaction.ok,
        "snapshot prototype commits when the state remains stable");
  Check(compaction_diagnostics.capture_timing &&
            std::isfinite(compaction_diagnostics.total_ms) &&
            compaction_diagnostics.total_ms >=
                compaction_diagnostics.merge_build_ms &&
            compaction_diagnostics.merge_prepare_ms >= 0.0 &&
            compaction_diagnostics.merge_build_ms > 0.0 &&
            compaction_diagnostics.publication_ms > 0.0 &&
            compaction_diagnostics.state_mutex_hold_ms >=
                compaction_diagnostics.publication_ms &&
            compaction_diagnostics.state_mutex_hold_ms <=
                compaction_diagnostics.total_ms &&
            compaction_diagnostics.state_mutex_hold_cpu_ms >= 0.0,
        "snapshot compaction diagnostics cover prepare, unlocked build, and publication");
  Check(index->GetStats().runs == 1,
        "stable snapshot prototype publishes one merged run");
  m4::SearchResult result;
  Check(index->SearchFts("alpha", 5, &result).ok &&
            result.hits.size() == 1 && result.hits[0].pk == "a",
        "stable compaction preserves the remaining live document");
  Check(index->SearchFts("beta", 5, &result).ok && result.hits.empty(),
        "stable compaction preserves the concurrent deletion");

  index.reset();
  std::unique_ptr<Index> reopened;
  Check(Index::Open(dir, opt, &reopened).ok,
        "snapshot prototype reopen after commit");
  Check(reopened->SearchFts("alpha", 5, &result).ok &&
            result.hits.size() == 1 && result.hits[0].pk == "a",
        "snapshot prototype live posting survives reopen");
  Check(reopened->SearchFts("beta", 5, &result).ok && result.hits.empty(),
        "snapshot prototype deletion survives reopen");
}

void TestFlushPublicationAndRecovery(const std::string& dir) {
  auto seed_unflushed = [](const std::string& path, const m4::Options& options) {
    fs::remove_all(path);
    std::unique_ptr<Index> index;
    Check(Index::Open(path, options, &index).ok, "flush crash seed open");
    Document old_same;
    old_same.text = "oldword common";
    old_same.values.push_back({"kind", "obsolete"});
    Check(index->Upsert("same", old_same).ok, "flush seed old same pk");
    Document doomed;
    doomed.text = "deadword common";
    Check(index->Upsert("doomed", doomed).ok, "flush seed doomed pk");
    Document replacement;
    replacement.text = "newword common";
    replacement.values.push_back({"kind", "live"});
    Check(index->Upsert("same", replacement).ok,
          "flush seed replace same pk");
    Check(index->Delete("doomed").ok, "flush seed delete pk");
    Document keep;
    keep.text = "keepword common";
    keep.values.push_back({"kind", "live"});
    Check(index->Upsert("keep", keep).ok, "flush seed keep pk");
    index.reset();
  };
  auto verify_reopened = [](const std::string& path, const std::string& label,
                            const m4::Options& options) {
    std::unique_ptr<Index> reopened;
    Check(Index::Open(path, options, &reopened).ok, label + ": reopen");
    if (!reopened) return;
    RefState ref;
    ref.docs.emplace("same", RefDoc{"same", "newword common", {{"kind", "live"}}});
    ref.docs.emplace("keep", RefDoc{"keep", "keepword common", {{"kind", "live"}}});
    ref.total_tokens = Tok("newword common").size() + Tok("keepword common").size();
    const auto stats = reopened->GetStats();
    Check(stats.live_docs == 2, label + ": live docs");
    Check(stats.live_tokens == ref.total_tokens, label + ": live token count");
    CheckFts(*reopened, ref, "newword", MakeRefQuery({"newword"}, false), 10,
             label + ": replacement BM25");
    CheckFts(*reopened, ref, "keepword", MakeRefQuery({"keepword"}, false), 10,
             label + ": preserved doc BM25");
    m4::SearchResult result;
    Check(reopened->SearchFts("oldword", 10, &result).ok && result.hits.empty(),
          label + ": replaced version absent");
    Check(reopened->SearchFts("deadword", 10, &result).ok && result.hits.empty(),
          label + ": deleted doc absent");
  };

  // A failed in-process flush may be followed by new WAL writes. Persist only
  // the not-yet-appended stats suffix on retry; never duplicate earlier events.
  const std::string retry_dir = dir + "/same-process-retry";
  fs::remove_all(retry_dir);
  m4::Options retry_options;
  retry_options.coalesce_stats_events = true;
  std::unique_ptr<Index> retry_index;
  Check(Index::Open(retry_dir, retry_options, &retry_index).ok,
        "flush retry open");
  Document first;
  first.text = "alpha common";
  Check(retry_index->Upsert("first", first).ok, "flush retry first write");
  Check(::setenv("M4_FAULT", "flush_fail_after_stats_append", 1) == 0,
        "flush retry set failure point");
  const m4::Status failed_flush = retry_index->Flush();
  Check(!failed_flush.ok, "flush retry injected failure returned");
  ::unsetenv("M4_FAULT");
  Document second;
  second.text = "beta common";
  Check(retry_index->Upsert("second", second).ok,
        "flush retry write after failed flush");
  Check(retry_index->Flush().ok, "flush retry second attempt succeeds");
  RefState retry_ref;
  retry_ref.docs.emplace("first", RefDoc{"first", "alpha common", {}});
  retry_ref.docs.emplace("second", RefDoc{"second", "beta common", {}});
  retry_ref.total_tokens = Tok("alpha common").size() + Tok("beta common").size();
  Check(retry_index->GetStats().live_docs == 2,
        "flush retry stats not duplicated");
  CheckFts(*retry_index, retry_ref, "common", MakeRefQuery({"common"}, false),
           10, "flush retry BM25");
  retry_index.reset();
  std::unique_ptr<Index> retry_reopened;
  Check(Index::Open(retry_dir, retry_options, &retry_reopened).ok,
        "flush retry reopen");
  Check(retry_reopened && retry_reopened->GetStats().live_docs == 2,
        "flush retry durable stats");

  auto seed_delete_index = [&](const std::string& path, int count) {
    fs::remove_all(path);
    m4::Options seed_options;
    seed_options.max_delta_docs = 128;
    seed_options.max_delta_bytes = 1ull << 30;
    std::unique_ptr<Index> seed;
    Check(Index::Open(path, seed_options, &seed).ok,
          "delete-bound seed open");
    if (!seed) return;
    for (int i = 0; i < count; ++i) {
      Document document;
      document.text = "deleteonly common item" + std::to_string(i);
      Check(seed->Upsert("delete-" + std::to_string(i), document).ok,
            "delete-bound seed upsert");
    }
    Check(seed->Flush().ok, "delete-bound seed flush");
  };

  const std::string delete_count_dir = dir + "/delete-count-bound";
  seed_delete_index(delete_count_dir, 3);
  m4::Options delete_count_options;
  delete_count_options.max_delta_docs = 2;
  delete_count_options.max_delta_bytes = 1ull << 30;
  std::unique_ptr<Index> delete_count_index;
  Check(Index::Open(delete_count_dir, delete_count_options,
                    &delete_count_index).ok,
        "delete-count bound reopen");
  if (delete_count_index) {
    Check(delete_count_index->Delete("delete-0").ok,
          "first delete remains pending below count bound");
    const auto after_first_count_delete = delete_count_index->GetStats();
    Check(after_first_count_delete.wal_bytes > 0,
          "first delete retains WAL below count bound");
    Check(delete_count_index->Delete("delete-1").ok,
          "delete at count bound preserves durable acknowledgment");
    const auto at_delete_count_bound = delete_count_index->GetStats();
    Check(at_delete_count_bound.wal_bytes > after_first_count_delete.wal_bytes &&
              at_delete_count_bound.live_docs == 1,
          "delete count bound waits for the next mutation to flush");
    Check(delete_count_index->Delete("delete-2").ok,
          "next delete flushes the pending bound before appending");
    const auto after_count_bound_retry = delete_count_index->GetStats();
    Check(after_count_bound_retry.wal_bytes > 0 &&
              after_count_bound_retry.wal_bytes <
                  at_delete_count_bound.wal_bytes &&
              after_count_bound_retry.live_docs == 0,
          "count-bound retry flushes old WAL then durably appends next delete");
  }
  delete_count_index.reset();
  std::unique_ptr<Index> delete_count_reopened;
  Check(Index::Open(delete_count_dir, delete_count_options,
                    &delete_count_reopened).ok &&
            delete_count_reopened &&
            delete_count_reopened->GetStats().live_docs == 0,
        "delete-only count-bound state survives reopen");

  const std::string delete_bytes_dir = dir + "/delete-bytes-bound";
  seed_delete_index(delete_bytes_dir, 1);
  m4::Options delete_bytes_options;
  delete_bytes_options.max_delta_docs = 128;
  delete_bytes_options.max_delta_bytes = 1;
  std::unique_ptr<Index> delete_bytes_index;
  Check(Index::Open(delete_bytes_dir, delete_bytes_options,
                    &delete_bytes_index).ok,
        "delete-bytes bound reopen");
  if (delete_bytes_index) {
    Check(delete_bytes_index->Delete("delete-0").ok,
          "delete-byte threshold preserves durable delete acknowledgment");
    Check(delete_bytes_index->GetStats().wal_bytes > 0 &&
              delete_bytes_index->GetStats().live_docs == 0,
          "delete-byte threshold leaves one bounded durable WAL tail");
  }
  delete_bytes_index.reset();
  std::unique_ptr<Index> delete_bytes_reopened;
  Check(Index::Open(delete_bytes_dir, delete_bytes_options,
                    &delete_bytes_reopened).ok &&
            delete_bytes_reopened &&
            delete_bytes_reopened->GetStats().live_docs == 0,
        "delete-byte-threshold WAL survives reopen");

  const std::string failed_autoflush_dir = dir + "/failed-autoflush-bound";
  seed_delete_index(failed_autoflush_dir, 2);
  m4::Options failed_autoflush_options;
  failed_autoflush_options.max_delta_docs = 1;
  failed_autoflush_options.max_delta_bytes = 1ull << 30;
  std::unique_ptr<Index> failed_autoflush_index;
  Check(Index::Open(failed_autoflush_dir, failed_autoflush_options,
                    &failed_autoflush_index).ok,
        "failed auto-flush bound reopen");
  if (failed_autoflush_index) {
    Check(::setenv("M4_FAULT", "flush_fail_after_stats_append", 1) == 0,
          "set failed auto-flush bound fault");
    Check(failed_autoflush_index->Delete("delete-0").ok,
          "threshold delete preserves its durable per-call acknowledgment");
    const auto after_first_delete = failed_autoflush_index->GetStats();
    Check(after_first_delete.wal_bytes > 0 &&
              after_first_delete.live_docs == 1,
          "failed threshold delete remains durable and query-visible");
    Check(failed_autoflush_index->Delete("missing").ok,
          "no-op delete does not retry a pending auto-flush");
    const auto after_noop_delete = failed_autoflush_index->GetStats();
    Check(after_noop_delete.wal_bytes == after_first_delete.wal_bytes &&
              after_noop_delete.wal_bytes_appended ==
                  after_first_delete.wal_bytes_appended &&
              after_noop_delete.live_docs == after_first_delete.live_docs,
          "no-op delete does not write WAL or change pending state");
    const std::vector<m4::Mutation> no_op_delete_batch = {
        {m4::MutationKind::DELETE, "missing", {}},
    };
    Check(failed_autoflush_index->ApplyBatch(no_op_delete_batch).ok,
          "no-op delete batch does not retry a pending auto-flush");
    const auto after_noop_batch = failed_autoflush_index->GetStats();
    Check(after_noop_batch.wal_bytes == after_first_delete.wal_bytes &&
              after_noop_batch.wal_bytes_appended ==
                  after_first_delete.wal_bytes_appended &&
              after_noop_batch.live_docs == after_first_delete.live_docs,
          "no-op batch does not write WAL or change pending state");
    Check(!failed_autoflush_index->Delete("delete-1").ok,
          "over-limit pending delta retries flush before next delete");
    const auto after_blocked_delete = failed_autoflush_index->GetStats();
    Check(after_blocked_delete.wal_bytes == after_first_delete.wal_bytes &&
              after_blocked_delete.wal_bytes_appended ==
                  after_first_delete.wal_bytes_appended &&
              after_blocked_delete.live_docs == 1,
          "failed preflush rejects extra WAL and delta growth");
    Document blocked_document;
    blocked_document.text = "blocked mutation";
    Check(!failed_autoflush_index->Upsert("blocked-upsert", blocked_document)
               .ok,
          "over-limit pending delta rejects Upsert before WAL append");
    const auto after_blocked_upsert = failed_autoflush_index->GetStats();
    Check(after_blocked_upsert.wal_bytes == after_first_delete.wal_bytes &&
              after_blocked_upsert.wal_bytes_appended ==
                  after_first_delete.wal_bytes_appended &&
              after_blocked_upsert.live_docs == 1,
          "failed Upsert preflush does not grow WAL or delta");
    Check(!failed_autoflush_index
               ->ApplyBatch({m4::Mutation(m4::MutationKind::UPSERT,
                                          "blocked-batch", blocked_document)})
               .ok,
          "over-limit pending delta rejects ApplyBatch before WAL append");
    const auto after_blocked_batch = failed_autoflush_index->GetStats();
    Check(after_blocked_batch.wal_bytes == after_first_delete.wal_bytes &&
              after_blocked_batch.wal_bytes_appended ==
                  after_first_delete.wal_bytes_appended &&
              after_blocked_batch.live_docs == 1,
          "failed ApplyBatch preflush does not grow WAL or delta");
    ::unsetenv("M4_FAULT");
    Check(failed_autoflush_index->Delete("delete-1").ok,
          "later delete succeeds after pending delta flush recovers");
    Check(failed_autoflush_index->GetStats().wal_bytes > 0 &&
              failed_autoflush_index->GetStats().live_docs == 0,
          "recovered bound flushes old WAL before appending second delete");
    Check(failed_autoflush_index->Flush().ok,
          "explicit flush drains recovered delete WAL tail");
    Check(failed_autoflush_index->GetStats().wal_bytes == 0,
          "explicit flush clears recovered delete WAL");
  } else {
    ::unsetenv("M4_FAULT");
  }
  failed_autoflush_index.reset();
  std::unique_ptr<Index> failed_autoflush_reopened;
  Check(Index::Open(failed_autoflush_dir, failed_autoflush_options,
                    &failed_autoflush_reopened).ok &&
            failed_autoflush_reopened &&
            failed_autoflush_reopened->GetStats().live_docs == 0,
        "failed auto-flush recovery state survives reopen");

  // Crash on both sides of the durable manifest and WAL truncation boundary
  // under both lock boundaries and both stats-event encodings.
  for (bool coalesce_stats : {false, true}) {
    for (bool staged : {false, true}) {
      m4::Options crash_options;
      crash_options.coalesce_stats_events = coalesce_stats;
      crash_options.flush_metadata_outside_query_lock = staged;
      for (const char* fault : {"flush_after_stats_append", "flush_before_manifest",
                                "flush_after_manifest", "flush_after_wal_reset"}) {
        const std::string label =
            std::string(coalesce_stats ? "coalesced-" : "legacy-") +
            (staged ? "staged-" : "baseline-") + fault;
      const std::string path = dir + "/crash-" + label;
      seed_unflushed(path, crash_options);
      const pid_t child = ::fork();
    Check(child >= 0, std::string("fork ") + fault);
    if (child == 0) {
      ::setenv("M4_FAULT", fault, 1);
      std::unique_ptr<Index> child_index;
      if (!Index::Open(path, crash_options, &child_index).ok) ::_exit(120);
      const m4::Status status = child_index->Flush();
      ::_exit(status.ok ? 121 : 122);
    }
    if (child < 0) continue;
    int status = 0;
    Check(::waitpid(child, &status, 0) == child,
          std::string("wait flush crash child ") + fault);
    Check(WIFEXITED(status) && WEXITSTATUS(status) >= 92 &&
              WEXITSTATUS(status) <= 95,
          std::string("flush crash reached expected boundary ") + fault);
        verify_reopened(path, "flush recovery " + label, crash_options);
      }
    }
  }

  // A reader can finish while flush metadata is staged and query-visible state
  // remains old; a same-PK update and delete wait behind the writer-state lock.
  const std::string concurrent_dir = dir + "/reader-writer-publication";
  fs::remove_all(concurrent_dir);
  std::unique_ptr<Index> concurrent;
  Check(Index::Open(concurrent_dir, {}, &concurrent).ok,
        "flush publication open");
  Document base;
  base.text = "baseword common";
  Check(concurrent->Upsert("base", base).ok, "flush publication base upsert");
  Check(concurrent->Flush().ok, "flush publication base flush");
  Document version;
  version.text = "versionword common";
  Check(concurrent->Upsert("same", version).ok,
        "flush publication delta upsert");
  const fs::path barrier = fs::path(concurrent_dir) / "flush-barrier";
  fs::remove_all(barrier);
  Check(::setenv("M4_TEST_FLUSH_PUBLICATION_BARRIER", barrier.c_str(), 1) == 0,
        "flush publication set barrier");
  std::atomic<bool> flush_done{false};
  m4::Status flush_status;
  std::thread flusher([&] {
    flush_status = concurrent->Flush();
    flush_done.store(true);
  });
  auto wait_for = [](const fs::path& path, const std::atomic<bool>& done,
                     std::chrono::seconds timeout) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    std::error_code ec;
    while (!fs::exists(path, ec) && !done.load() &&
           std::chrono::steady_clock::now() < deadline) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return fs::exists(path, ec);
  };
  const bool staged = wait_for(barrier / "segment-ready", flush_done,
                               std::chrono::seconds(5));
  Check(staged, "flush reaches unlocked metadata staging barrier");
  std::atomic<bool> reader_done{false};
  bool reader_ok = false;
  std::thread reader;
  if (staged) {
    reader = std::thread([&] {
      m4::SearchResult result;
      reader_ok = concurrent->SearchFts("versionword", 5, &result).ok &&
                  result.hits.size() == 1 && result.hits[0].pk == "same";
      reader_done.store(true);
    });
    const auto reader_deadline = std::chrono::steady_clock::now() +
                                 std::chrono::seconds(2);
    while (!reader_done.load() && std::chrono::steady_clock::now() < reader_deadline)
      std::this_thread::yield();
    Check(reader_done.load() && reader_ok,
          "reader completes on old in-memory state during metadata staging");
  }
  std::atomic<bool> writer_attempted{false};
  std::atomic<bool> writer_done{false};
  m4::Status update_status;
  m4::Status delete_status;
  std::thread writer;
  std::atomic<bool> overlapping_flush_attempted{false};
  std::atomic<bool> overlapping_flush_done{false};
  m4::Status overlapping_flush_status;
  std::thread overlapping_flusher;
  if (staged) {
    overlapping_flusher = std::thread([&] {
      overlapping_flush_attempted.store(true);
      overlapping_flush_status = concurrent->Flush();
      overlapping_flush_done.store(true);
    });
    writer = std::thread([&] {
      writer_attempted.store(true);
      Document replacement;
      replacement.text = "finalword common";
      update_status = concurrent->Upsert("same", replacement);
      delete_status = concurrent->Delete("base");
      writer_done.store(true);
    });
    const auto writer_deadline = std::chrono::steady_clock::now() +
                                 std::chrono::seconds(2);
    while ((!writer_attempted.load() || !overlapping_flush_attempted.load()) &&
           std::chrono::steady_clock::now() < writer_deadline)
      std::this_thread::yield();
    Check(writer_attempted.load() && overlapping_flush_attempted.load(),
          "writer and overlapping flush reach the serialized boundary");
    const auto blocked_deadline = std::chrono::steady_clock::now() +
                                  std::chrono::milliseconds(50);
    while ((!writer_done.load() || !overlapping_flush_done.load()) &&
           std::chrono::steady_clock::now() < blocked_deadline)
      std::this_thread::yield();
    Check(writer_attempted.load() && !writer_done.load(),
          "writer stays serialized until flush publication and WAL reset");
    Check(overlapping_flush_attempted.load() && !overlapping_flush_done.load(),
          "overlapping flush waits for the current generation commit");
    std::ofstream release(barrier / "reader-finished", std::ios::binary);
    release << "done\n";
  }
  flusher.join();
  if (reader.joinable()) reader.join();
  if (writer.joinable()) writer.join();
  if (overlapping_flusher.joinable()) overlapping_flusher.join();
  ::unsetenv("M4_TEST_FLUSH_PUBLICATION_BARRIER");
  Check(flush_status.ok, "flush publication succeeds");
  if (staged) {
    Check(overlapping_flush_status.ok,
          "overlapping flush completes after the first commit");
    Check(update_status.ok && delete_status.ok,
          "same-PK update and delete after flush succeed");
    Check(concurrent->Flush().ok, "post-publication writer flush succeeds");
    m4::SearchResult result;
    Check(concurrent->SearchFts("versionword", 5, &result).ok && result.hits.empty(),
          "old same-PK version is absent");
    Check(concurrent->SearchFts("finalword", 5, &result).ok &&
              result.hits.size() == 1 && result.hits[0].pk == "same",
          "same-PK replacement is visible");
    Check(concurrent->SearchFts("baseword", 5, &result).ok && result.hits.empty(),
          "concurrent delete is visible");
  }
  concurrent.reset();
  std::unique_ptr<Index> concurrent_reopened;
  Check(Index::Open(concurrent_dir, {}, &concurrent_reopened).ok,
        "flush publication reopen");
  if (concurrent_reopened && staged) {
    m4::SearchResult result;
    Check(concurrent_reopened->SearchFts("finalword", 5, &result).ok &&
              result.hits.size() == 1 && result.hits[0].pk == "same",
          "same-PK replacement survives reopen");
    Check(concurrent_reopened->SearchFts("baseword", 5, &result).ok &&
              result.hits.empty(),
          "concurrent delete survives reopen");
  }
}

void TestSnapshotCompactionConcurrentFlush(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.tombstone_compact_ratio = 2.0;
  std::unique_ptr<Index> index;
  Check(Index::Open(dir, opt, &index).ok, "snapshot flush prototype open");
  Document alpha;
  alpha.text = "alpha";
  Check(index->Upsert("a", alpha).ok, "snapshot flush upsert alpha");
  Check(index->Flush().ok, "snapshot flush flush alpha");
  Document beta;
  beta.text = "beta";
  Check(index->Upsert("b", beta).ok, "snapshot flush upsert beta");
  Check(index->Flush().ok, "snapshot flush flush beta");

  // Exercise the optional forced-rebuild-on-flush path while a snapshot build
  // is active; it must defer that merge rather than fail the durable flush.
  index.reset();
  opt.rebuild_on_flush = true;
  const m4::Status reopen_status = Index::Open(dir, opt, &index);
  Check(reopen_status.ok, "snapshot flush reopen with forced rebuild enabled");
  if (!reopen_status.ok) return;

  const fs::path barrier = fs::path(dir) / "stream-merge-barrier";
  fs::remove_all(barrier);
  Check(::setenv("M4_TEST_SNAPSHOT_MERGE_READ_BARRIER", barrier.c_str(), 1) == 0,
        "snapshot flush set stream-merge barrier");
  std::atomic<bool> compaction_done{false};
  m4::Status compaction_status;
  std::thread compactor([&] {
    compaction_status = index->CompactNowSnapshotPrototype();
    compaction_done.store(true);
  });
  auto wait_for = [](const fs::path& path, const std::atomic<bool>& done,
                     std::chrono::seconds timeout) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    std::error_code ec;
    while (!fs::exists(path, ec) && !done.load() &&
           std::chrono::steady_clock::now() < deadline) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return fs::exists(path, ec);
  };
  const bool run_ready = wait_for(barrier / "merge-read-ready", compaction_done,
                                  std::chrono::seconds(5));
  Check(run_ready, "snapshot flush reaches the unlocked stream-merge barrier");

  std::atomic<size_t> writers_done{0};
  std::vector<m4::Status> writer_statuses(2);
  std::vector<std::thread> writers;
  if (run_ready) {
    for (size_t i = 0; i < writer_statuses.size(); ++i) {
      writers.emplace_back([&, i] {
        Document document;
        document.text = i == 0 ? "gamma" : "delta";
        writer_statuses[i] = index->Upsert(i == 0 ? "c" : "d", document);
        writers_done.fetch_add(1);
      });
    }
    const auto writer_deadline = std::chrono::steady_clock::now() +
                                 std::chrono::seconds(2);
    while (writers_done.load() != writer_statuses.size() &&
           std::chrono::steady_clock::now() < writer_deadline) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    const bool writers_finished_before_release =
        writers_done.load() == writer_statuses.size();
    Check(writers_finished_before_release,
          "competing upserts complete before snapshot publication");
    if (!writers_finished_before_release) {
      // Always release the compactor before joining, so a regression that
      // blocks writers cannot hang the test process.
      std::ofstream release(barrier / "writer-finished", std::ios::binary);
      release << "timeout\n";
    }
    for (auto& writer : writers) writer.join();
    if (writers_done.load() == writer_statuses.size()) {
      for (const auto& writer_status : writer_statuses) {
        Check(writer_status.ok, "snapshot concurrent upsert succeeds");
      }
      const m4::Status flush_status = index->Flush();
      Check(flush_status.ok, "concurrent upserts flush during snapshot build");
      m4::SearchResult result;
      for (const auto* term : {"alpha", "beta", "gamma", "delta"}) {
        Check(index->SearchFts(term, 5, &result).ok && result.hits.size() == 1,
              std::string("concurrent flush query-visible term ") + term);
      }
    }
    if (writers_finished_before_release) {
      std::ofstream release(barrier / "writer-finished", std::ios::binary);
      release << "done\n";
    }
  }
  compactor.join();
  ::unsetenv("M4_TEST_SNAPSHOT_MERGE_READ_BARRIER");
  Check(!compaction_status.ok &&
            compaction_status.message.find("superseded") != std::string::npos,
        "concurrent flush prevents stale snapshot publication: " +
            (compaction_status.ok ? "ok" : compaction_status.message));
  Check(index->GetStats().runs == 3,
        "stale compaction preserves both old runs and the concurrent flush run");
  Check(!fs::exists(fs::path(dir) / "run-3.seg") &&
            fs::exists(fs::path(dir) / "run-4.seg"),
        "stale output cleanup preserves the writer's reserved run ID");
  m4::SearchResult result;
  for (const auto* term : {"alpha", "beta", "gamma", "delta"}) {
    Check(index->SearchFts(term, 5, &result).ok && result.hits.size() == 1,
          std::string("post-abort query-visible term ") + term);
  }

  index.reset();
  std::unique_ptr<Index> reopened;
  Check(Index::Open(dir, opt, &reopened).ok,
        "snapshot concurrent flush reopen");
  Check(reopened->GetStats().runs == 3,
        "snapshot concurrent flush manifest survives reopen");
  for (const auto* term : {"alpha", "beta", "gamma", "delta"}) {
    Check(reopened->SearchFts(term, 5, &result).ok && result.hits.size() == 1,
          std::string("snapshot concurrent flush reopen term ") + term);
  }
}

void TestSnapshotCompactionRebasesConcurrentFlush(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.max_delta_docs = 3;
  opt.tombstone_compact_ratio = 2.0;
  std::unique_ptr<Index> index;
  Check(Index::Open(dir, opt, &index).ok, "snapshot rebase prototype open");
  Document alpha;
  alpha.text = "alpha";
  Check(index->Upsert("a", alpha).ok, "snapshot rebase upsert alpha");
  Check(index->Flush().ok, "snapshot rebase flush alpha into first run");
  Document beta;
  beta.text = "beta";
  Check(index->Upsert("b", beta).ok, "snapshot rebase upsert beta");
  Check(index->Flush().ok, "snapshot rebase flush beta into second run");
  Check(index->GetStats().runs == 2,
        "snapshot rebase starts with two independently flushed runs");
  Check(index->Delete("a").ok, "snapshot rebase pre-snapshot delete");
  Check(index->Flush().ok, "snapshot rebase persist pre-snapshot tombstone");
  Check(index->GetStats().dead_docs == 1,
        "snapshot rebase captures an existing input tombstone");

  const fs::path barrier = fs::path(dir) / "stream-merge-barrier";
  fs::remove_all(barrier);
  Check(::setenv("M4_TEST_SNAPSHOT_MERGE_READ_BARRIER", barrier.c_str(), 1) == 0,
        "snapshot rebase set stream-merge barrier");
  std::atomic<bool> compaction_done{false};
  m4::Status compaction_status;
  m4::CompactionDiagnostics rebase_diagnostics;
  rebase_diagnostics.capture_timing = true;
  std::thread compactor([&] {
    compaction_status = index->CompactNowSnapshotRebasePrototype(
        &rebase_diagnostics);
    compaction_done.store(true);
  });
  auto wait_for = [](const fs::path& path, const std::atomic<bool>& done,
                     std::chrono::seconds timeout) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    std::error_code ec;
    while (!fs::exists(path, ec) && !done.load() &&
           std::chrono::steady_clock::now() < deadline) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return fs::exists(path, ec);
  };
  const bool run_ready = wait_for(barrier / "merge-read-ready", compaction_done,
                                  std::chrono::seconds(5));
  Check(run_ready, "snapshot rebase reaches unlocked stream-merge barrier");

  std::atomic<bool> writer_done{false};
  m4::Status mutation_status;
  std::thread writer;
  if (run_ready) {
    writer = std::thread([&] {
      Document gamma;
      gamma.text = "gamma";
      const std::vector<m4::Mutation> batch = {
          {m4::MutationKind::UPSERT, "c", gamma},
          {m4::MutationKind::DELETE, "b", {}},
      };
      mutation_status = index->ApplyBatch(batch);
      writer_done.store(true);
    });
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::seconds(2);
    while (!writer_done.load() && std::chrono::steady_clock::now() < deadline) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    const bool completed_before_publication = writer_done.load();
    Check(completed_before_publication,
          "concurrent upsert and delete complete before rebase publication");
    if (!completed_before_publication) {
      std::ofstream release(barrier / "writer-finished", std::ios::binary);
      release << "timeout\n";
    }
    writer.join();
    if (completed_before_publication) {
      Check(mutation_status.ok,
            "snapshot rebase concurrent batch mutation succeeds");
    }
    std::ofstream release(barrier / "writer-finished", std::ios::binary);
    release << "done\n";
  }
  compactor.join();
  ::unsetenv("M4_TEST_SNAPSHOT_MERGE_READ_BARRIER");
  Check(compaction_status.ok,
        "snapshot rebase publishes after concurrent writer changes: " +
            (compaction_status.ok ? "ok" : compaction_status.message));
  if (compaction_status.ok) {
    const double flush_stage_sum =
        rebase_diagnostics.flush_segment_build_ms +
        rebase_diagnostics.flush_segment_write_ms +
        rebase_diagnostics.flush_segment_open_ms +
        rebase_diagnostics.flush_query_mutex_wait_ms +
        rebase_diagnostics.flush_stats_append_ms +
        rebase_diagnostics.flush_tombstone_write_ms +
        rebase_diagnostics.flush_manifest_write_ms +
        rebase_diagnostics.flush_wal_reset_ms +
        rebase_diagnostics.flush_gc_ms +
        rebase_diagnostics.flush_delta_reset_ms;
    Check(rebase_diagnostics.flush_segment_build_ms == 0.0 &&
              rebase_diagnostics.flush_segment_write_ms == 0.0 &&
              rebase_diagnostics.flush_segment_open_ms == 0.0 &&
              flush_stage_sum == 0.0 &&
              rebase_diagnostics.pending_flush_ms < 1.0,
          "WAL-preserving rebase avoids a synchronous pending-delta flush");
    Check(rebase_diagnostics.capture_timing &&
              rebase_diagnostics.publication_ms > 0.0 &&
              rebase_diagnostics.state_mutex_hold_ms + 1e-6 >=
                  rebase_diagnostics.publication_ms &&
              rebase_diagnostics.state_mutex_hold_ms <=
                  rebase_diagnostics.total_ms + 1e-6,
          "rebase state-lock timer resumes across the unlocked merge build");
  }
  Document delta;
  delta.text = "delta";
  Check(index->Upsert("d", delta).ok,
        "snapshot rebase adds a second unflushed writer-tail document");
  Check(index->GetStats().runs == 1 && index->GetStats().live_docs == 2 &&
            index->GetStats().dead_docs == 1,
        "snapshot rebase keeps concurrent mutations in the WAL-backed delta");
  std::error_code wal_size_error;
  Check(fs::file_size(fs::path(dir) / "wal.log", wal_size_error) > 0 &&
            !wal_size_error,
        "snapshot rebase preserves pending WAL records until a later flush");
  Check(fs::exists(fs::path(dir) / "run-3.seg") &&
            !fs::exists(fs::path(dir) / "run-4.seg"),
        "snapshot rebase publishes only its reserved run before WAL replay");
  m4::SearchResult result;
  Check(index->SearchFts("alpha", 5, &result).ok && result.hits.empty(),
        "snapshot rebase removes the pre-snapshot deleted document");
  Check(index->SearchFts("beta", 5, &result).ok && result.hits.empty(),
        "snapshot rebase tombstone hides the old deleted document");
  Check(index->SearchFts("gamma", 5, &result).ok && result.hits.size() == 1,
        "snapshot rebase retains the concurrent WAL-backed document");
  Check(index->SearchFts("delta", 5, &result).ok && result.hits.size() == 1,
        "snapshot rebase retains the second WAL-backed document");

  index.reset();
  std::unique_ptr<Index> reopened;
  Check(Index::Open(dir, opt, &reopened).ok,
        "snapshot rebase reopen after concurrent publication");
  Check(reopened->GetStats().runs == 1 && reopened->GetStats().live_docs == 2 &&
            reopened->GetStats().dead_docs == 1,
        "snapshot rebase manifest and uncommitted WAL tail survive reopen");
  Check(reopened->SearchFts("alpha", 5, &result).ok && result.hits.empty(),
        "snapshot rebase pre-snapshot deletion survives reopen");
  Check(reopened->SearchFts("beta", 5, &result).ok && result.hits.empty(),
        "snapshot rebase deletion survives reopen");
  Check(reopened->SearchFts("gamma", 5, &result).ok && result.hits.size() == 1,
        "snapshot rebase concurrent WAL mutation survives reopen");
  Check(reopened->SearchFts("delta", 5, &result).ok && result.hits.size() == 1,
        "snapshot rebase WAL tail replays after publication");
}

void TestSnapshotCompactionRebaseAfterWalReplay(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.max_delta_docs = 16;
  opt.tombstone_compact_ratio = 2.0;
  std::unique_ptr<Index> index;
  Check(Index::Open(dir, opt, &index).ok,
        "snapshot rebase replay fixture open");
  Document alpha;
  alpha.text = "alpha";
  Document beta;
  beta.text = "oldbeta";
  Check(index->Upsert("a", alpha).ok && index->Upsert("b", beta).ok,
        "snapshot rebase replay fixture seed docs");
  Check(index->Flush().ok, "snapshot rebase replay fixture persist seed");
  Document replacement;
  replacement.text = "newversion";
  Check(index->Upsert("b", replacement).ok,
        "snapshot rebase replay fixture leave uncommitted update");

  index.reset();
  Check(Index::Open(dir, opt, &index).ok,
        "snapshot rebase replay fixture recover pending WAL");
  Check(index->GetStats().live_docs == 2 && index->GetStats().dead_docs == 1,
        "snapshot rebase replay fixture applies pending update");
  Check(index->CompactNowSnapshotRebasePrototype().ok,
        "snapshot rebase compacts after WAL replay without checkpointing it");
  m4::SearchResult result;
  Check(index->SearchFts("oldbeta", 5, &result).ok && result.hits.empty(),
        "snapshot rebase keeps replayed update query-visible");
  Check(index->SearchFts("newversion", 5, &result).ok &&
            result.hits.size() == 1,
        "snapshot rebase keeps replayed replacement query-visible");

  index.reset();
  Check(Index::Open(dir, opt, &index).ok,
        "snapshot rebase reopens with original durable WAL checkpoint");
  Check(index->GetStats().runs == 1 && index->GetStats().live_docs == 2 &&
            index->GetStats().dead_docs == 1,
        "reopened rebase replays WAL instead of trusting the in-memory replay seq");
  Check(index->SearchFts("oldbeta", 5, &result).ok && result.hits.empty(),
        "reopened rebase does not resurrect the replaced document");
  Check(index->SearchFts("newversion", 5, &result).ok &&
            result.hits.size() == 1,
        "reopened rebase preserves the replacement document");
}

void TestSnapshotCompactionRebasePreManifestRecovery(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.max_delta_docs = 1;
  opt.tombstone_compact_ratio = 2.0;
  std::unique_ptr<Index> index;
  Check(Index::Open(dir, opt, &index).ok,
        "snapshot rebase recovery open");
  Document alpha;
  alpha.text = "alpha";
  Check(index->Upsert("a", alpha).ok, "snapshot rebase recovery upsert alpha");
  Document beta;
  beta.text = "beta";
  Check(index->Upsert("b", beta).ok, "snapshot rebase recovery upsert beta");
  Check(index->Delete("a").ok, "snapshot rebase recovery tombstone alpha");
  Check(index->Flush().ok, "snapshot rebase recovery persist old tombstone");

  const fs::path barrier = fs::path(dir) / "stream-merge-barrier";
  fs::remove_all(barrier);
  Check(::setenv("M4_TEST_SNAPSHOT_MERGE_READ_BARRIER", barrier.c_str(), 1) == 0,
        "snapshot rebase recovery set stream-merge barrier");
  Check(::setenv("M4_FAULT", "after_compaction_tombstone", 1) == 0,
        "snapshot rebase recovery inject pre-manifest fault");
  std::atomic<bool> compaction_done{false};
  m4::Status compaction_status;
  std::thread compactor([&] {
    compaction_status = index->CompactNowSnapshotRebasePrototype();
    compaction_done.store(true);
  });
  const auto ready_deadline = std::chrono::steady_clock::now() +
                              std::chrono::seconds(5);
  std::error_code ec;
  while (!fs::exists(barrier / "merge-read-ready", ec) &&
         !compaction_done.load() &&
         std::chrono::steady_clock::now() < ready_deadline) {
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }
  const bool run_ready = fs::exists(barrier / "merge-read-ready", ec);
  Check(run_ready, "snapshot rebase recovery reaches merge barrier");

  std::atomic<bool> writer_done{false};
  m4::Status upsert_status;
  m4::Status delete_status;
  std::thread writer;
  if (run_ready) {
    writer = std::thread([&] {
      Document gamma;
      gamma.text = "gamma";
      upsert_status = index->Upsert("c", gamma);
      if (upsert_status.ok) delete_status = index->Delete("b");
      writer_done.store(true);
    });
    const auto writer_deadline = std::chrono::steady_clock::now() +
                                 std::chrono::seconds(2);
    while (!writer_done.load() &&
           std::chrono::steady_clock::now() < writer_deadline) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    const bool writer_finished = writer_done.load();
    Check(writer_finished,
          "recovery fixture flush and delete complete during merge build");
    if (!writer_finished) {
      std::ofstream release(barrier / "writer-finished", std::ios::binary);
      release << "timeout\n";
    } else {
      Check(upsert_status.ok && delete_status.ok,
            "recovery fixture concurrent mutations succeed");
    }
    writer.join();
    std::ofstream release(barrier / "writer-finished", std::ios::binary);
    release << "done\n";
  }
  compactor.join();
  ::unsetenv("M4_TEST_SNAPSHOT_MERGE_READ_BARRIER");
  ::unsetenv("M4_FAULT");
  Check(!compaction_status.ok &&
            compaction_status.message.find(
                "after compaction tombstone snapshot") != std::string::npos,
        "snapshot rebase reports injected pre-manifest tombstone fault: " +
            (compaction_status.ok ? "ok" : compaction_status.message));

  // The concurrent upsert auto-flush committed the previous manifest while
  // the merge build was unlocked. Rebase publication does not flush the pending
  // delete; if its replacement tombstone file is written but its manifest is
  // not, recovery must load that prior manifest and replay the unchanged WAL.
  index.reset();
  std::unique_ptr<Index> reopened;
  Check(Index::Open(dir, opt, &reopened).ok,
        "snapshot rebase pre-manifest recovery reopen");
  Check(reopened->GetStats().runs == 3 && reopened->GetStats().live_docs == 1 &&
            reopened->GetStats().dead_docs == 2,
        "pre-manifest recovery loads the previously committed state");
  m4::SearchResult result;
  Check(reopened->SearchFts("alpha", 5, &result).ok && result.hits.empty(),
        "pre-manifest recovery does not resurrect captured tombstone");
  Check(reopened->SearchFts("beta", 5, &result).ok && result.hits.empty(),
        "pre-manifest recovery retains concurrent tombstone");
  Check(reopened->SearchFts("gamma", 5, &result).ok && result.hits.size() == 1,
        "pre-manifest recovery retains flushed concurrent upsert");
  Check(!fs::exists(fs::path(dir) / "run-3.seg"),
        "recovery garbage-collects the unmanifested compaction output");
}

void TestSnapshotCompactionBudget(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.max_delta_docs = 1000;
  opt.tombstone_compact_ratio = 2.0;
  opt.compaction_memory_limit_bytes =
      m4::kStreamMergeFixedIoBufferChargeBytes + (8ull << 10);
  std::unique_ptr<Index> index;
  Check(Index::Open(dir, opt, &index).ok, "snapshot budget open");
  for (int i = 0; i < 100; ++i) {
    Document document;
    document.text = "common";
    Check(index->Upsert("k" + std::to_string(i), document).ok,
          "snapshot budget upsert");
  }
  Check(index->Flush().ok, "snapshot budget initial flush");
  for (int i = 0; i < 80; ++i) {
    Check(index->Delete("k" + std::to_string(i)).ok,
          "snapshot budget delete");
  }

  const m4::Status status = index->CompactNowSnapshotPrototype();
  Check(!status.ok && status.message.find("memory budget") != std::string::npos,
        "snapshot budget is rejected before compaction publication");
  Check(index->GetStats().runs == 1 && index->GetStats().live_docs == 20,
        "snapshot budget refusal preserves the run and live-doc state");
  m4::SearchResult result;
  Check(index->SearchFts("common", 100, &result).ok &&
            result.hits.size() == 20,
        "snapshot budget refusal leaves the index searchable");
  Check(!fs::exists(fs::path(dir) / "run-2.seg"),
        "snapshot budget refusal creates no output segment");
}

void TestSnapshotCompactionFaults(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.tombstone_compact_ratio = 2.0;
  std::unique_ptr<Index> index;
  m4::Status status = Index::Open(dir, opt, &index);
  Check(status.ok, "snapshot fault open");
  if (!status.ok) return;
  Document alpha;
  alpha.text = "alpha";
  Document beta;
  beta.text = "beta";
  Check(index->Upsert("a", alpha).ok, "snapshot fault upsert alpha");
  Check(index->Flush().ok, "snapshot fault flush alpha");
  Check(index->Upsert("b", beta).ok, "snapshot fault upsert beta");
  Check(index->Flush().ok, "snapshot fault flush beta");

  ::setenv("M4_FAULT", "before_manifest", 1);
  status = index->CompactNowSnapshotPrototype();
  ::unsetenv("M4_FAULT");
  Check(!status.ok && status.message.find("before manifest") != std::string::npos,
        "snapshot compaction pre-manifest fault is reported");
  Check(index->GetStats().runs == 2,
        "snapshot pre-manifest failure keeps old runs active");
  m4::SearchResult result;
  Check(index->SearchFts("alpha", 5, &result).ok && result.hits.size() == 1,
        "snapshot pre-manifest failure keeps old postings searchable");
  Check(fs::exists(fs::path(dir) / "run-3.seg"),
        "snapshot pre-manifest failure leaves only an unreferenced output");

  index.reset();
  status = Index::Open(dir, opt, &index);
  Check(status.ok, "snapshot reopen after pre-manifest fault");
  if (!status.ok) return;
  Check(index->GetStats().runs == 2,
        "snapshot reopen after pre-manifest fault restores old manifest");
  Check(!fs::exists(fs::path(dir) / "run-3.seg"),
        "snapshot reopen garbage-collects the unreferenced output");

  ::setenv("M4_FAULT", "after_manifest", 1);
  status = index->CompactNowSnapshotPrototype();
  ::unsetenv("M4_FAULT");
  Check(!status.ok && status.message.find("after manifest") != std::string::npos,
        "snapshot post-manifest fault is reported");
  index.reset();
  status = Index::Open(dir, opt, &index);
  Check(status.ok, "snapshot reopen after post-manifest fault");
  if (!status.ok) return;
  Check(index->GetStats().runs == 1,
        "snapshot post-manifest reopen keeps the published replacement");
  Check(index->SearchFts("alpha", 5, &result).ok && result.hits.size() == 1,
        "snapshot post-manifest reopen preserves alpha");
  Check(index->SearchFts("beta", 5, &result).ok && result.hits.size() == 1,
        "snapshot post-manifest reopen preserves beta");
}

void TestEmptyMergeTombstoneCrash(const std::string &dir) {
  fs::remove_all(dir);
  m4::Options options;
  options.max_delta_docs = 1000;
  options.tombstone_compact_ratio = 2.0;
  std::unique_ptr<Index> index;
  Check(Index::Open(dir, options, &index).ok,
        "empty-merge tombstone crash open");
  Document document;
  document.text = "deleted-marker";
  Check(index->Upsert("deleted", document).ok,
        "empty-merge tombstone crash upsert");
  Check(index->Flush().ok, "empty-merge tombstone crash initial flush");
  Check(index->Delete("deleted").ok, "empty-merge tombstone crash delete");
  Check(index->Flush().ok, "empty-merge tombstone crash persist delete");
  index.reset();

  const pid_t child = ::fork();
  if (child == 0) {
    ::setenv("M4_FAULT", "compact_after_tombstone_write", 1);
    std::unique_ptr<Index> child_index;
    if (!Index::Open(dir, options, &child_index).ok)
      ::_exit(90);
    const m4::Status status = child_index->CompactNow();
    ::_exit(status.ok ? 91 : 92);
  }
  int child_status = 0;
  Check(child > 0 && ::waitpid(child, &child_status, 0) == child &&
            WIFEXITED(child_status) && WEXITSTATUS(child_status) == 94,
        "empty-merge compaction crashes after writing tombstones");

  std::unique_ptr<Index> recovered;
  const m4::Status open_status = Index::Open(dir, options, &recovered);
  Check(open_status.ok, "empty-merge tombstone crash recovery open");
  if (!open_status.ok)
    return;
  m4::SearchResult result;
  Check(recovered->GetStats().live_docs == 0 &&
            recovered->SearchFts("deleted-marker", 10, &result).ok &&
            result.hits.empty(),
        "empty-merge crash recovery does not resurrect acknowledged delete");
}

void TestWandUsesListWideUpperBound(const std::string &dir) {
  fs::remove_all(dir);
  m4::Options options;
  options.max_delta_docs = 1000;
  options.block_size = 128;
  std::unique_ptr<Index> index;
  const m4::Status open_status = Index::Open(dir, options, &index);
  Check(open_status.ok, "WAND bound open");
  if (!open_status.ok)
    return;

  Document common;
  common.text = "alpha";
  for (uint32_t i = 0; i < 256; ++i) {
    Document document = common;
    if (i == 255) {
      for (int repeat = 1; repeat < 20; ++repeat) {
        document.text += " alpha";
      }
    }
    Check(index->Upsert("doc-" + std::to_string(i), document).ok,
          "WAND bound upsert");
  }
  Check(index->Flush().ok, "WAND bound flush");
  m4::SearchResult result;
  Check(index->SearchFts("alpha", 1, &result).ok && result.hits.size() == 1 &&
            result.hits[0].pk == "doc-255",
        "WAND sees a high-scoring hit in a later posting block");
}

void TestCompactionPositionBudget(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.compaction_memory_limit_bytes = (4ull << 20) + (16ull << 10);
  std::unique_ptr<Index> index;
  m4::Status s = Index::Open(dir, opt, &index);
  Check(s.ok, "position budget open");
  std::string text;
  text.reserve(20'000 * 7);
  for (size_t i = 0; i < 20'000; ++i) {
    if (!text.empty()) text.push_back(' ');
    text += "needle";
  }
  Document large;
  large.text = std::move(text);
  Check(index->Upsert("large", large).ok, "position budget large upsert");
  Check(index->Flush().ok, "position budget flush large");
  Document small;
  small.text = "marker";
  Check(index->Upsert("small", small).ok, "position budget small upsert");
  Check(index->Flush().ok, "position budget flush small");

  const uint64_t runs_before = index->GetStats().runs;
  s = index->CompactNow();
  Check(!s.ok && s.message.find("memory") != std::string::npos,
        "position buffer is charged to the compaction memory budget");
  Check(index->GetStats().runs == runs_before,
        "position-budget abort leaves the old runs published");
  m4::SearchResult result;
  Check(index->SearchFts("needle", 2, &result).ok && result.hits.size() == 1,
        "position-budget abort leaves old postings searchable");
}

void TestCompactionFaults(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.max_delta_docs = 1000;
  opt.max_total_runs = 12;
  opt.max_runs_per_level = 4;
  std::unique_ptr<Index> index;
  m4::Status s = Index::Open(dir, opt, &index);
  Check(s.ok, "fault open");
  auto add = [&](const std::string& prefix, const std::string& term,
                 int n) {
    for (int i = 0; i < n; ++i) {
      Document d;
      d.text = term + " " + std::to_string(i);
      Check(index->Upsert(prefix + std::to_string(i), d).ok, "fault upsert");
    }
  };
  add("a", "alpha", 40);
  Check(index->Flush().ok, "fault flush alpha");
  add("b", "beta", 40);
  Check(index->Flush().ok, "fault flush beta");

  auto count = [&](const char* term) {
    m4::SearchResult r;
    Check(index->SearchFts(term, 100, &r).ok, "fault search");
    return r.hits.size();
  };

  ::setenv("M4_FAULT", "after_rename", 1);
  s = index->CompactNow();
  ::unsetenv("M4_FAULT");
  Check(!s.ok, "fault after rename fails");
  Check(index->GetStats().runs == 2, "fault after rename keeps old runs");
  Check(count("alpha") == 40 && count("beta") == 40,
        "fault after rename old state searchable");

  ::setenv("M4_FAULT", "before_manifest", 1);
  s = index->CompactNow();
  ::unsetenv("M4_FAULT");
  Check(!s.ok, "fault before manifest fails");
  Check(index->GetStats().runs == 2, "fault before manifest keeps old runs");
  Check(count("alpha") == 40 && count("beta") == 40,
        "fault before manifest old state searchable");

  ::setenv("M4_FAULT", "after_manifest", 1);
  s = index->CompactNow();
  ::unsetenv("M4_FAULT");
  Check(!s.ok, "fault after manifest reports failure");
  index.reset();

  std::unique_ptr<Index> reopened;
  s = Index::Open(dir, opt, &reopened);
  Check(s.ok, "fault reopen after manifest: " + s.message);
  if (!s.ok) return;
  Check(reopened->GetStats().runs == 1, "fault after manifest publishes run");
  m4::SearchResult r;
  Check(reopened->SearchFts("alpha", 100, &r).ok, "fault reopen search");
  Check(r.hits.size() == 40, "fault reopen keeps alpha docs");
  Check(reopened->SearchFts("beta", 100, &r).ok, "fault reopen beta search");
  Check(r.hits.size() == 40, "fault reopen keeps beta docs");
}


void TestChurnOracle(const std::string& dir, bool debt_policy = false) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.max_delta_docs = 64;
  opt.max_delta_bytes = 4 << 20;
  opt.max_runs_per_level = 3;
  opt.max_total_runs = 8;
  opt.tombstone_compact_ratio = 0.25;
  opt.stats_log_threshold = 1024;
  opt.fsync_wal = false;
  opt.debt_policy_enabled = debt_policy;
  opt.debt_compact_ratio = 0.02;
  opt.debt_min_runs = 3;
  opt.debt_max_merge_docs = 100000;
  std::unique_ptr<Index> index;
  m4::Status s = Index::Open(dir, opt, &index);
  Check(s.ok, "churn open: " + s.message);
  if (!s.ok) return;

  RefState ref;
  std::mt19937 rng(424242);
  std::vector<std::string> live;
  auto put = [&](const std::string& pk, const Document& d) {
    AddDoc(&ref, pk, d);
    Check(index->Upsert(pk, d).ok, "churn upsert");
  };
  auto make_doc = [&]() {
    Document d = RandomDoc(&rng, 160);
    // Ensure every live doc is discoverable by FTS and INVERT liveness checks.
    d.text = "common " + d.text;
    d.values.clear();
    d.values.push_back(ScalarValue{"status", "active"});
    d.values.push_back(ScalarValue{"region", (rng() % 2) ? "a" : "b"});
    return d;
  };
  auto add_new = [&](const std::string& pk) {
    Document d = make_doc();
    put(pk, d);
    live.push_back(pk);
  };

  for (int i = 0; i < 3000; ++i) {
    add_new("c-" + std::to_string(i));
  }
  // A stable phrase fixture; it is updated later, so phrase lookups also cover
  // the update path rather than only freshly written postings.
  {
    Document d;
    d.text = "common alpha beta gamma";
    d.values.push_back(ScalarValue{"status", "active"});
    d.values.push_back(ScalarValue{"region", "a"});
    put("phrase", d);
    live.push_back("phrase");
  }
  Check(index->Flush().ok, "churn base flush");

  auto check_state = [&](const std::string& label) {
    const auto st = index->GetStats();
    Check(st.live_docs == ref.docs.size(),
          label + ": live docs stats " + std::to_string(st.live_docs) +
              " vs " + std::to_string(ref.docs.size()));
    Check(st.live_tokens == ref.total_tokens,
          label + ": live tokens stats " + std::to_string(st.live_tokens) +
              " vs " + std::to_string(ref.total_tokens));
    CheckFts(*index, ref, "common", MakeRefQuery({"common"}, false), 10,
             label + " common");
    m4::SearchResult all_common;
    Check(index->SearchFts("common", static_cast<uint32_t>(ref.docs.size() + 10),
                           &all_common).ok,
          label + " common status");
    Check(all_common.hits.size() == ref.docs.size(),
          label + " common exact count " +
              std::to_string(all_common.hits.size()) + " vs " +
              std::to_string(ref.docs.size()));
    CheckFts(*index, ref, "w7", MakeRefQuery({"w7"}, false), 10,
             label + " w7");
    CheckFts(*index, ref, "w7 AND common",
             MakeRefQuery({"w7", "common"}, true), 10, label + " AND");
    CheckFts(*index, ref, "\"alpha beta\"",
             MakeRefQuery({}, false, {{"alpha", "beta"}}), 10,
             label + " phrase");
    CheckInvert(*index, ref, "status", "active", CompareOp::EQ,
                label + " invert active");
    CheckInvert(*index, ref, "region", "b", CompareOp::GE,
                label + " invert range");
    m4::SearchResult h;
    std::unordered_map<std::string, float> vec;
    for (size_t i = 0; i < live.size(); i += 7) {
      vec[live[i]] = static_cast<float>((i % 11) / 11.0);
    }
    Check(index->SearchHybrid("common", 12, 0.6, vec, &h).ok,
          label + " hybrid status");
    Check(h.hits.size() <= 12, label + " hybrid top-k");
    for (size_t i = 1; i < h.hits.size(); ++i) {
      Check(h.hits[i - 1].score >= h.hits[i].score, label + " hybrid sorted");
    }
    for (const auto& hit : h.hits) {
      Check(vec.count(hit.pk) != 0 || ref.docs.count(hit.pk) != 0,
            label + " hybrid pk");
    }
  };

  for (int cycle = 0; cycle < 9; ++cycle) {
    // 80 updates, 50 deletes with replacement inserts, and 25 reinserts of the
    // just-deleted keys. Live cardinality stays constant; dead DocIds grow.
    for (int i = 0; i < 80; ++i) {
      const std::string pk = live[rng() % live.size()];
      put(pk, make_doc());
    }
    for (int i = 0; i < 50; ++i) {
      const size_t idx = rng() % live.size();
      const std::string old_pk = live[idx];
      DeleteDoc(&ref, old_pk);
      Check(index->Delete(old_pk).ok, "churn delete");
      live[idx] = live.back();
      live.pop_back();
      if (i % 2 == 0) {
        add_new("r-" + std::to_string(cycle) + "-" + std::to_string(i));
      } else {
        // Reinsert the same key with a new DocId.
        Document d = make_doc();
        put(old_pk, d);
        live.push_back(old_pk);
      }
    }
    if (cycle % 3 == 0) {
      const std::string fixture = "phrase";
      Document d;
      d.text = "common alpha beta gamma c" + std::to_string(cycle);
      d.values.push_back(ScalarValue{"status", "active"});
      d.values.push_back(ScalarValue{"region", "a"});
      put(fixture, d);
    }
    Check(index->Flush().ok, "churn flush " + std::to_string(cycle));
    if (cycle % 2 == 0) check_state("churn cycle " + std::to_string(cycle));
  }

  // Force a transaction-boundary failure after substantial churn; the old
  // runs and tombstones must remain consistent and searchable.
  ::setenv("M4_FAULT", "before_manifest", 1);
  s = index->CompactNow();
  ::unsetenv("M4_FAULT");
  Check(!s.ok, "churn injected pre-manifest failure");
  check_state("after churn fault");

  Check(index->CompactNow().ok, "churn final compact");
  const auto final_st = index->GetStats();
  Check(final_st.dead_docs == 0, "churn final tombstones collected");
  Check(final_st.runs == 1, "churn final one run");
  check_state("after churn compact");

  // No unpublished temp files; only one segment and one live tombstone
  // snapshot should remain after the final GC.
  int segment_files = 0;
  int tombstone_files = 0;
  for (const auto& e : fs::directory_iterator(dir)) {
    const std::string name = e.path().filename().string();
    if (name.rfind("run-", 0) == 0 && e.path().extension() == ".seg") {
      ++segment_files;
    }
    if (name.rfind("tombstones-", 0) == 0) ++tombstone_files;
    Check(name.find(".tmp.") == std::string::npos,
          "churn no unpublished temp file");
  }
  Check(segment_files == 1, "churn old segments collected");
  Check(tombstone_files <= 1, "churn old tombstone snapshots collected");
  index.reset();

  std::unique_ptr<Index> reopened;
  s = Index::Open(dir, opt, &reopened);
  Check(s.ok, "churn reopen: " + s.message);
  if (!s.ok) return;
  const auto reopen_st = reopened->GetStats();
  Check(reopen_st.live_docs == ref.docs.size(), "churn reopen live docs");
  Check(reopen_st.dead_docs == 0, "churn reopen dead docs");
  CheckFts(*reopened, ref, "common", MakeRefQuery({"common"}, false), 10,
           "churn reopen common");
  CheckInvert(*reopened, ref, "status", "active", CompareOp::EQ,
              "churn reopen invert");

  // WAL replay after churn: mutate without flushing, destroy the index, and
  // reopen from the last manifest plus the uncommitted WAL tail.
  for (int i = 0; i < 30; ++i) {
    const std::string& pk = live[rng() % live.size()];
    Document d = make_doc();
    AddDoc(&ref, pk, d);
    Check(reopened->Upsert(pk, d).ok, "churn WAL update");
  }
  for (int i = 0; i < 15; ++i) {
    const size_t idx = rng() % live.size();
    const std::string old_pk = live[idx];
    DeleteDoc(&ref, old_pk);
    Check(reopened->Delete(old_pk).ok, "churn wal delete");
    live[idx] = live.back();
    live.pop_back();
    const std::string pk = "wal-" + std::to_string(i);
    Document d = make_doc();
    AddDoc(&ref, pk, d);
    Check(reopened->Upsert(pk, d).ok, "churn WAL insert");
    live.push_back(pk);
  }
  reopened.reset();
  std::unique_ptr<Index> wal_reopened;
  s = Index::Open(dir, opt, &wal_reopened);
  Check(s.ok, "churn WAL reopen: " + s.message);
  if (!s.ok) return;
  Check(wal_reopened->GetStats().live_docs == ref.docs.size(),
        "churn WAL live docs");
  CheckFts(*wal_reopened, ref, "common", MakeRefQuery({"common"}, false), 10,
           "churn WAL common");
  CheckInvert(*wal_reopened, ref, "status", "active", CompareOp::EQ,
              "churn WAL invert");
  Check(wal_reopened->Flush().ok, "churn WAL flush");
}

void TestManifestFallback(const std::string& dir) {
  fs::remove_all(dir);
  m4::Options opt;
  opt.max_delta_docs = 1000;
  std::unique_ptr<Index> index;
  m4::Status s = Index::Open(dir, opt, &index);
  Check(s.ok, "fallback open");
  for (int i = 0; i < 40; ++i) {
    Document d;
    d.text = "alpha " + std::to_string(i);
    Check(index->Upsert("f" + std::to_string(i), d).ok, "fallback upsert");
  }
  Check(index->Flush().ok, "fallback flush");
  Check(index->CompactNow().ok, "fallback compact");
  {
    std::ofstream bad(dir + "/manifest", std::ios::binary | std::ios::trunc);
    bad << "corrupt";
  }
  {
    std::unique_ptr<Index> reopened;
    s = Index::Open(dir, opt, &reopened);
    Check(s.ok, "fallback reopen from bak: " + s.message);
    if (!s.ok)
      return;
    m4::SearchResult r;
    Check(reopened->SearchFts("alpha", 50, &r).ok, "fallback search");
    Check(r.hits.size() == 40, "fallback restored all docs");
  }
}

} // namespace

int main(int argc, char *argv[]) {
  TestSha256BlockBoundaries();
  const std::string root =
      argc > 1 ? argv[1]
               : (fs::current_path() / ".tmp" / "m4-research-tests").string();
  fs::remove_all(root);
  RunRandomized(root + "/random");
  TestTypedUint32Scalar(root + "/typed-u32");
  TestTypedArrayUint32(root + "/typed-array-u32");
  TestBooleanFilters(root + "/boolean-filters");
  TestStringPrefixRangeSearch(root + "/string-prefix-range");
  TestPhrase(root + "/phrase");
  TestCrashWal(root + "/wal");
  TestWalRecordBounds(root + "/wal-record-bounds");
  TestWalSequenceChecksumBoundary(root + "/wal-sequence-boundary");
  TestLz4WalFrames(root + "/wal-lz4");
  TestAtomicPublicationSyncFailures(root + "/atomic-publication-sync-failures");
  TestFlushPublicationAndRecovery(root + "/flush-publication-recovery");
  TestAtomicMutationBatch(root + "/batch");
  TestStatsEventCoalescing(root + "/stats-event-coalescing");
  TestBoundedRuns(root + "/bounded");
  TestStatIndependentBounds(root + "/bounds");
  TestHybrid(root + "/hybrid");
  TestCompactionBudget(root + "/budget");
  TestSnapshotCompactionPrototype(root + "/snapshot-prototype");
  TestSnapshotCompactionConcurrentFlush(root + "/snapshot-concurrent-flush");
  TestSnapshotCompactionRebasesConcurrentFlush(
      root + "/snapshot-rebase-concurrent-flush");
  TestSnapshotCompactionRebaseAfterWalReplay(
      root + "/snapshot-rebase-after-wal-replay");
  TestSnapshotCompactionRebasePreManifestRecovery(
      root + "/snapshot-rebase-pre-manifest-recovery");
  TestSnapshotCompactionBudget(root + "/snapshot-budget");
  TestSnapshotCompactionFaults(root + "/snapshot-faults");
  TestEmptyMergeTombstoneCrash(root + "/empty-merge-tombstone-crash");
  TestWandUsesListWideUpperBound(root + "/wand-list-bound");
  TestCompactionPositionBudget(root + "/position-budget");
  TestManifestFallback(root + "/manifest-fallback");
  TestCompactionFaults(root + "/faults");
  TestChurnOracle(root + "/churn");
  TestChurnOracle(root + "/churn-policy", true);

  if (g_failures == 0)
    fs::remove_all(root);
  std::printf("checks=%d failures=%d\n", g_checks, g_failures);
  return g_failures == 0 ? 0 : 1;
}
