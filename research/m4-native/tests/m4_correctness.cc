// Correctness oracle for the M4 research prototype.
//
// The reference is an exhaustive full-scan of the same live document set with
// the same tokenizer and BM25 formula. It intentionally does not share any
// index code with the implementation.

#include <algorithm>
#include <atomic>
#include <cmath>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <map>
#include <random>
#include <limits>
#include <optional>
#include <set>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <sys/wait.h>
#include <unistd.h>

#include "m4/index.h"
#include "../src/format.h"
#include "../src/tokenizer.h"

namespace fs = std::filesystem;
using m4::CompareOp;
using m4::Document;
using m4::Index;
using m4::ScalarValue;
using m4::Token;

namespace {

int g_failures = 0;
int g_checks = 0;

void Check(bool ok, const std::string& what) {
  ++g_checks;
  if (!ok) {
    ++g_failures;
    std::fprintf(stderr, "FAIL: %s\n", what.c_str());
  }
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

void TestCrashWal(const std::string& dir) {
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
    if (!s.ok) return;
    CheckFts(*index, ref, "alpha", MakeRefQuery({"alpha"}, false), 50,
             "wal replay alpha");
    Check(index->GetStats().live_docs == ref.docs.size(), "wal replay count");
    Check(index->Flush().ok, "wal flush after replay");
  }
  {
    std::unique_ptr<Index> index;
    m4::Status s = Index::Open(dir, opt, &index);
    Check(s.ok, "wal reopen after flush: " + s.message);
    if (!s.ok) return;
    CheckFts(*index, ref, "beta", MakeRefQuery({"beta"}, false), 50,
             "wal persisted beta");
  }
}

void TestAtomicMutationBatch(const std::string& root) {
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
      wal.write("M4W", 3);
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
    Check(index->GetStats().wal_fsyncs == 3,
          "full durable batch uses one WAL sync");
    done.store(true, std::memory_order_release);
    reader.join();
    Check(bad_snapshot.load() == 0, "readers see only batch boundaries");
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
  Check(run_ready,
        "snapshot prototype pauses after reading an FTS term with state lock released");
  if (run_ready) {
    const m4::Status overlapping = index->CompactNowSnapshotPrototype();
    Check(!overlapping.ok &&
              overlapping.message.find("another compaction build is active") !=
                  std::string::npos,
          "snapshot prototype bounds compaction builds to one per index");
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
  opt.max_delta_docs = 1;
  opt.tombstone_compact_ratio = 2.0;
  std::unique_ptr<Index> index;
  Check(Index::Open(dir, opt, &index).ok, "snapshot rebase prototype open");
  Document alpha;
  alpha.text = "alpha";
  Check(index->Upsert("a", alpha).ok, "snapshot rebase upsert alpha");
  Document beta;
  beta.text = "beta";
  Check(index->Upsert("b", beta).ok, "snapshot rebase upsert beta");
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
  std::thread compactor([&] {
    compaction_status = index->CompactNowSnapshotRebasePrototype();
    compaction_done.store(true);
  });
  auto wait_for = [](const fs::path& path, const std::atomic<bool>& done) {
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::seconds(5);
    std::error_code ec;
    while (!fs::exists(path, ec) && !done.load() &&
           std::chrono::steady_clock::now() < deadline) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return fs::exists(path, ec);
  };
  const bool run_ready = wait_for(barrier / "merge-read-ready", compaction_done);
  Check(run_ready, "snapshot rebase reaches unlocked stream-merge barrier");

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
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::seconds(2);
    while (!writer_done.load() && std::chrono::steady_clock::now() < deadline) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    const bool completed_before_publication = writer_done.load();
    Check(completed_before_publication,
          "concurrent auto-flush and delete complete before rebase publication");
    if (!completed_before_publication) {
      std::ofstream release(barrier / "writer-finished", std::ios::binary);
      release << "timeout\n";
    }
    writer.join();
    if (completed_before_publication) {
      Check(upsert_status.ok, "snapshot rebase concurrent auto-flush succeeds");
      Check(delete_status.ok, "snapshot rebase concurrent delete succeeds");
    }
    std::ofstream release(barrier / "writer-finished", std::ios::binary);
    release << "done\n";
  }
  compactor.join();
  ::unsetenv("M4_TEST_SNAPSHOT_MERGE_READ_BARRIER");
  Check(compaction_status.ok,
        "snapshot rebase publishes after concurrent writer changes: " +
            (compaction_status.ok ? "ok" : compaction_status.message));
  Check(index->GetStats().runs == 2 && index->GetStats().live_docs == 1 &&
            index->GetStats().dead_docs == 1,
        "snapshot rebase drops captured tombstone but retains the new one");
  Check(fs::exists(fs::path(dir) / "run-3.seg") &&
            fs::exists(fs::path(dir) / "run-4.seg"),
        "snapshot rebase keeps distinct reserved compaction and writer run IDs");
  m4::SearchResult result;
  Check(index->SearchFts("alpha", 5, &result).ok && result.hits.empty(),
        "snapshot rebase removes the pre-snapshot deleted document");
  Check(index->SearchFts("beta", 5, &result).ok && result.hits.empty(),
        "snapshot rebase tombstone hides the old deleted document");
  Check(index->SearchFts("gamma", 5, &result).ok && result.hits.size() == 1,
        "snapshot rebase retains the concurrent flushed document");

  index.reset();
  std::unique_ptr<Index> reopened;
  Check(Index::Open(dir, opt, &reopened).ok,
        "snapshot rebase reopen after concurrent publication");
  Check(reopened->GetStats().runs == 2 && reopened->GetStats().live_docs == 1 &&
            reopened->GetStats().dead_docs == 1,
        "snapshot rebase manifest and tombstones survive reopen");
  Check(reopened->SearchFts("alpha", 5, &result).ok && result.hits.empty(),
        "snapshot rebase pre-snapshot deletion survives reopen");
  Check(reopened->SearchFts("beta", 5, &result).ok && result.hits.empty(),
        "snapshot rebase deletion survives reopen");
  Check(reopened->SearchFts("gamma", 5, &result).ok && result.hits.size() == 1,
        "snapshot rebase concurrent flush survives reopen");
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

  // The prior manifest was written by the publication-time FlushInternal.
  // Recovery must retain its tombstones if a later tombstone snapshot exists
  // but its replacement manifest was not committed.
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
    if (!s.ok) return;
    m4::SearchResult r;
    Check(reopened->SearchFts("alpha", 50, &r).ok, "fallback search");
    Check(r.hits.size() == 40, "fallback restored all docs");
  }
}

}  // namespace

int main() {
  const std::string root = (fs::temp_directory_path() / "m4-research-tests").string();
  fs::remove_all(root);
  RunRandomized(root + "/random");
  TestTypedUint32Scalar(root + "/typed-u32");
  TestTypedArrayUint32(root + "/typed-array-u32");
  TestBooleanFilters(root + "/boolean-filters");
  TestPhrase(root + "/phrase");
  TestCrashWal(root + "/wal");
  TestAtomicMutationBatch(root + "/batch");
  TestBoundedRuns(root + "/bounded");
  TestStatIndependentBounds(root + "/bounds");
  TestHybrid(root + "/hybrid");
  TestCompactionBudget(root + "/budget");
  TestSnapshotCompactionPrototype(root + "/snapshot-prototype");
  TestSnapshotCompactionConcurrentFlush(root + "/snapshot-concurrent-flush");
  TestSnapshotCompactionRebasesConcurrentFlush(
      root + "/snapshot-rebase-concurrent-flush");
  TestSnapshotCompactionRebasePreManifestRecovery(
      root + "/snapshot-rebase-pre-manifest-recovery");
  TestSnapshotCompactionBudget(root + "/snapshot-budget");
  TestSnapshotCompactionFaults(root + "/snapshot-faults");
  TestCompactionPositionBudget(root + "/position-budget");
  TestManifestFallback(root + "/manifest-fallback");
  TestCompactionFaults(root + "/faults");
  TestChurnOracle(root + "/churn");
  TestChurnOracle(root + "/churn-policy", true);

  std::printf("checks=%d failures=%d\n", g_checks, g_failures);
  return g_failures == 0 ? 0 : 1;
}
