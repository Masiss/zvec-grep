// Research-only end-to-end fragment-projection workload derived from zg-engine.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <memory>
#include <set>
#include <sstream>
#include <string>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

#include "m4/index.h"
#include <zvec/c_api.h>

namespace fs = std::filesystem;
namespace {
int checks = 0;
int failures = 0;
void Check(bool ok, const std::string& what) {
  ++checks;
  if (!ok) { ++failures; std::cerr << "FAIL: " << what << '\n'; }
}
bool Code(zvec_error_code_t code, const std::string& what) {
  const bool ok = code == ZVEC_OK;
  Check(ok, what + (ok ? "" : ": " + std::string(zvec_error_code_to_string(code))));
  return ok;
}
struct Native { zvec_collection_t* p{}; ~Native(){ if(p) zvec_collection_close(p); } };
struct Row {
  std::string pk, entity, text, name, symbol_name, symbol_type;
  uint32_t file{};
  std::vector<uint32_t> dirs;
};
const std::string kNeedleFn = "c85f38f0b9e1c4a7c0c27457dbcce5836fd9cc2f76d52775db9597dbcb123d14";
const std::string kHelperFn = "abcb620e0a8c22003eb83afe96945dc014f8f1c7071abe78bada309b548703bb";

zvec_collection_t* OpenNative(const std::string& path, bool create) {
  auto* schema = zvec_collection_schema_create("zg_fragment_projection_research");
  const struct { const char* name; zvec_data_type_t type; bool nullable; bool indexed; bool range; bool wildcard; }
      fields[] = {
        {"file_id", ZVEC_DATA_TYPE_UINT32, false, true, true, false},
        {"entity_id", ZVEC_DATA_TYPE_STRING, false, true, true, false},
        {"document_id", ZVEC_DATA_TYPE_STRING, false, false, false, false},
        {"ancestor_directory_ids", ZVEC_DATA_TYPE_ARRAY_UINT32, false, true, true, false},
        {"file_name", ZVEC_DATA_TYPE_STRING, false, true, false, true},
        {"symbol_name", ZVEC_DATA_TYPE_STRING, true, true, true, false},
        {"symbol_type", ZVEC_DATA_TYPE_STRING, true, true, true, false},
      };
  for (const auto& f : fields) {
    auto* field = zvec_field_schema_create(f.name, f.type, f.nullable, 0);
    if (f.indexed) {
      auto* params = zvec_index_params_create(ZVEC_INDEX_TYPE_INVERT);
      Code(zvec_index_params_set_invert_params(params, f.range, f.wildcard), std::string("configure ")+f.name);
      Code(zvec_field_schema_set_index_params(field, params), std::string("index ")+f.name);
      zvec_index_params_destroy(params);
    }
    Code(zvec_collection_schema_add_field(schema, field), std::string("add ")+f.name);
    zvec_field_schema_destroy(field);
  }
  auto* fts_params = zvec_index_params_create(ZVEC_INDEX_TYPE_FTS);
  auto* filters = zvec_string_array_create(1); zvec_string_array_add(filters, 0, "lowercase");
  // zg-engine pins jieba + lowercase; this native profile exposes standard only.
  // ASCII-only fixture is chosen for token equivalence, while the analyzer gap is reported.
  Code(zvec_index_params_set_fts_params(fts_params, "standard", filters, ""), "configure standard FTS");
  auto* text = zvec_field_schema_create("text", ZVEC_DATA_TYPE_STRING, false, 0);
  Code(zvec_field_schema_set_index_params(text, fts_params), "index text");
  Code(zvec_collection_schema_add_field(schema, text), "add text");
  zvec_field_schema_destroy(text); zvec_index_params_destroy(fts_params); zvec_string_array_destroy(filters);
  zvec_collection_t* result = nullptr;
  zvec_error_code_t status;
  if (create) status = zvec_collection_create_and_open(path.c_str(), schema, nullptr, &result);
  else status = zvec_collection_open(path.c_str(), nullptr, &result);
  Code(status, create ? "create native collection" : "reopen native collection");
  zvec_collection_schema_destroy(schema);
  return result;
}

bool NativeUpsert(zvec_collection_t* db, const Row& r) {
  auto* d = zvec_doc_create(); zvec_doc_set_pk(d, r.pk.c_str());
  const auto str = [&](const char* name, const std::string& v) {
    return Code(zvec_doc_add_field_by_value(d, name, ZVEC_DATA_TYPE_STRING, v.data(), v.size()), std::string("set ")+name);
  };
  bool ok = str("entity_id", r.entity) && str("document_id", r.pk) && str("file_name", r.name) &&
            str("symbol_name", r.symbol_name) && str("symbol_type", r.symbol_type) && str("text", r.text);
  ok = Code(zvec_doc_add_field_by_value(d, "file_id", ZVEC_DATA_TYPE_UINT32, &r.file, sizeof(r.file)), "set file_id") && ok;
  uint32_t empty = 0;
  ok = Code(zvec_doc_add_field_by_value(d, "ancestor_directory_ids", ZVEC_DATA_TYPE_ARRAY_UINT32,
       r.dirs.empty() ? static_cast<const void*>(&empty) : r.dirs.data(), r.dirs.size()*sizeof(uint32_t)), "set ancestor dirs") && ok;
  const zvec_doc_t* docs[] = {d}; zvec_write_result_t* wr = nullptr; size_t n = 0;
  const auto status = zvec_collection_upsert_with_results(db, docs, 1, &wr, &n);
  ok = (status == ZVEC_OK && n == 1 && wr && wr[0].code == ZVEC_OK) && ok;
  Check(ok, "native upsert " + r.pk); zvec_write_results_free(wr, n); zvec_doc_destroy(d); return ok;
}
bool NativeDeleteFile(zvec_collection_t* db, uint32_t file_id) {
  const std::string filter = "file_id = " + std::to_string(file_id);
  const auto s = zvec_collection_delete_by_filter(db, filter.c_str());
  const bool ok = s == ZVEC_OK;
  Check(ok, "native delete file_id=" + std::to_string(file_id)); return ok;
}
void M4DeleteFile(m4::Index& db, uint32_t file_id) {
  m4::SearchResult matches;
  const auto s = db.SearchFilter(m4::FilterExpr::Uint32Eq("file_id", file_id), &matches);
  Check(s.ok, "M4 enumerate file for delete: " + s.message);
  for (const auto& hit : matches.hits) {
    const auto deleted = db.Delete(hit.pk);
    Check(deleted.ok, "M4 delete file fragment " + hit.pk + ": " + deleted.message);
  }
}
void PutM4(m4::Index& db, const Row& r) {
  m4::Document d; d.text = r.text;
  d.values = {m4::ScalarValue::Unsigned32("file_id", r.file), {"entity_id", r.entity}, {"document_id", r.pk},
              {"file_name", r.name}, {"symbol_name", r.symbol_name}, {"symbol_type", r.symbol_type}};
  d.array_uint32_values.push_back({"ancestor_directory_ids", r.dirs});
  const auto s = db.Upsert(r.pk, d); Check(s.ok, "M4 upsert " + r.pk + ": " + s.message);
}

struct Hit { std::string pk; float score; };
std::vector<Hit> NativeSearch(zvec_collection_t* db, const std::string& query, const std::string& filter, uint32_t k) {
  auto* q = zvec_vector_query_create(); zvec_vector_query_set_field_name(q, "text");
  zvec_vector_query_set_topk(q, static_cast<int>(k)); zvec_vector_query_set_filter(q, filter.c_str());
  auto* f = zvec_fts_create(); zvec_fts_set_query_string(f, query.c_str()); zvec_vector_query_set_fts(q, f);
  auto* p = zvec_query_params_fts_create("OR");
  const auto pc = zvec_vector_query_set_fts_params(q, p);
  if (pc == ZVEC_OK) p = nullptr;
  Code(pc, "attach FTS query params");
  zvec_doc_t** docs=nullptr; size_t n=0; const auto s=zvec_collection_query(db,q,&docs,&n);
  Check(s==ZVEC_OK, "native FTS query `"+query+"` / "+filter);
  std::vector<Hit> hits; std::set<std::string> unique;
  if(s==ZVEC_OK) for(size_t i=0;i<n;++i){ const char* pk=zvec_doc_get_pk_pointer(docs[i]); std::string key=pk?pk:""; Check(unique.insert(key).second,"native duplicate PK "+key); hits.push_back({key,zvec_doc_get_score(docs[i])}); }
  zvec_docs_free(docs,n); zvec_vector_query_destroy(q); if(p) zvec_query_params_fts_destroy(p); zvec_fts_destroy(f);
  // zg-engine's Fragments::search_fts applies this tie-break after the native
  // per-collection query; compare the externally visible order, not raw C API ties.
  std::stable_sort(hits.begin(), hits.end(), [](const Hit& a, const Hit& b) {
    return a.score != b.score ? a.score > b.score : a.pk < b.pk;
  });
  return hits;
}
std::vector<Hit> M4Search(const m4::Index& db,const std::string& query,const m4::FilterExpr& filter,uint32_t k){
  m4::SearchResult r; const auto s=db.SearchFtsFiltered(query,k,filter,&r); Check(s.ok,"M4 FTS query `"+query+"`: "+s.message);
  std::vector<Hit> hits; std::set<std::string> unique; for(const auto& h:r.hits){Check(unique.insert(h.pk).second,"M4 duplicate PK "+h.pk);hits.push_back({h.pk,h.score});}
  std::stable_sort(hits.begin(), hits.end(), [](const Hit& a, const Hit& b) {
    return a.score != b.score ? a.score > b.score : a.pk < b.pk;
  });
  return hits;
}
void Compare(zvec_collection_t* native,const m4::Index& m4,const std::string& phase){
  using F=m4::FilterExpr;
  struct Case{const char* name; std::string sql; std::string query; F filter;};
  const std::vector<Case> cases={
    {"directory+filename+symbol type","ancestor_directory_ids CONTAIN_ANY (10) AND file_name LIKE 'main%' AND symbol_type = 'function'","needle",F::And({F::ArrayContainsAny("ancestor_directory_ids",{10}),F::StringPrefix("file_name","main"),F::StringEq("symbol_type","function")})},
    {"directory OR root-prefix","ancestor_directory_ids CONTAIN_ANY (20) OR file_name LIKE 'main%'","needle",F::Or({F::ArrayContainsAny("ancestor_directory_ids",{20}),F::StringPrefix("file_name","main")})},
    {"file/entity/symbol hash","file_id = 1 AND entity_id = 'entity-A' AND symbol_name = '"+kNeedleFn+"'","needle",F::And({F::Uint32Eq("file_id",1),F::StringEq("entity_id","entity-A"),F::StringEq("symbol_name",kNeedleFn)})},
    {"filename suffix plus directory","file_name LIKE '%.rs' AND ancestor_directory_ids CONTAIN_ANY (10)","needle",F::And({F::StringSuffix("file_name",".rs"),F::ArrayContainsAny("ancestor_directory_ids",{10})})},
    {"symbol function filter","symbol_type = 'function'","needle",F::StringEq("symbol_type","function")},
    {"root-level file","file_id = 3 AND file_name LIKE 'root%'","needle",F::And({F::Uint32Eq("file_id",3),F::StringPrefix("file_name","root")})},
    {"nested file after recovery","ancestor_directory_ids CONTAIN_ANY (30) AND file_name LIKE 'nested%' AND symbol_type = 'function'","needle",F::And({F::ArrayContainsAny("ancestor_directory_ids",{30}),F::StringPrefix("file_name","nested"),F::StringEq("symbol_type","function")})},
  };
  for(const auto& c:cases){
    auto a=NativeSearch(native,c.query,c.sql,100); auto b=M4Search(m4,c.query,c.filter,100);
    auto ids=[](const std::vector<Hit>& v){std::set<std::string>s;for(const auto&h:v)s.insert(h.pk);return s;};
    const bool same=ids(a)==ids(b); Check(same,phase+" set parity: "+c.name);
    if(!same)std::cerr<<"DIFF phase="<<phase<<" case="<<c.name<<" native="<<ids(a).size()<<" m4="<<ids(b).size()<<'\n';
    const size_t common=std::min(a.size(),b.size()); bool same_order=true;
    for(size_t i=0;i<common;++i) if(a[i].pk!=b[i].pk) same_order=false;
    float max_score_delta=0, native_at_max=0, m4_at_max=0; std::string score_pk;
    for(const auto& nh:a){auto it=std::find_if(b.begin(),b.end(),[&](const Hit& mh){return mh.pk==nh.pk;});
      if(it!=b.end() && std::abs(nh.score-it->score)>max_score_delta){max_score_delta=std::abs(nh.score-it->score);native_at_max=nh.score;m4_at_max=it->score;score_pk=nh.pk;}}
    if(max_score_delta>1e-5) std::cout<<"score_divergence phase="<<phase<<" case=\""<<c.name<<"\" max_abs_delta="<<max_score_delta<<" pk="<<score_pk<<" native="<<native_at_max<<" m4="<<m4_at_max<<"\n";
    std::cout<<"case phase="<<phase<<" name=\""<<c.name<<"\" hits="<<a.size()<<" order="<<(same_order?"same":"DIFF")<<"\n";
    if(!same_order){
      const auto show=[](const std::vector<Hit>& hits){std::ostringstream out;for(size_t i=0;i<hits.size();++i){if(i)out<<",";out<<hits[i].pk<<":"<<hits[i].score;}return out.str();};
      std::cout<<"rank_order_divergence phase="<<phase<<" case=\""<<c.name<<"\" native=["<<show(a)<<"] m4=["<<show(b)<<"]\n";
    }
    // Bounded top-k membership is separately compared, rather than silently requiring stale BM25 parity.
    auto at=NativeSearch(native,c.query,c.sql,2); auto bt=M4Search(m4,c.query,c.filter,2);
    if(ids(at)!=ids(bt)) std::cout<<"ranking_divergence phase="<<phase<<" case=\""<<c.name<<"\" native_top2="<<(at.empty()?"":at[0].pk)<<","<<(at.size()<2?"":at[1].pk)<<" m4_top2="<<(bt.empty()?"":bt[0].pk)<<","<<(bt.size()<2?"":bt[1].pk)<<"\n";
  }
}

} // namespace
int main(int argc, char** argv){
  if (argc == 4 && std::string(argv[1]) == "--crash-writer") {
    auto* db = OpenNative(argv[2], false);
    m4::Options options; options.fts_field = "text"; options.max_delta_docs = 1000; options.fsync_wal = true;
    std::unique_ptr<m4::Index> index;
    const auto opened = m4::Index::Open(argv[3], options, &index);
    const Row wal{"frag-D1", "entity-D", "needle recovered wal fragment", "nested.rs", kNeedleFn, "function", 4, {30}};
    if (!opened.ok || !db || !NativeUpsert(db, wal)) std::_Exit(21);
    m4::Document document; document.text = wal.text;
    document.values = {m4::ScalarValue::Unsigned32("file_id", wal.file), {"entity_id", wal.entity},
      {"document_id", wal.pk}, {"file_name", wal.name}, {"symbol_name", wal.symbol_name}, {"symbol_type", wal.symbol_type}};
    document.array_uint32_values.push_back({"ancestor_directory_ids", wal.dirs});
    if (!index->Upsert(wal.pk, document).ok) std::_Exit(22);
    std::_Exit(0);  // Simulate process death without destructors/flush.
  }
  std::string temp_pattern=(fs::temp_directory_path()/"m4-zg-workload-XXXXXX").string();
  std::vector<char> tmp(temp_pattern.begin(),temp_pattern.end()); tmp.push_back('\0');
  char* root=mkdtemp(tmp.data()); if(!root){std::cerr<<"mkdtemp failed\n";return 2;}
  const fs::path base(root), np=base/"native", mp=base/"m4";
  auto* ndb=OpenNative(np.string(),true); Native native{ndb};
  m4::Options opts; opts.fts_field="text"; opts.max_delta_docs=1000; opts.fsync_wal=true;
  std::unique_ptr<m4::Index> m4db; auto ms=m4::Index::Open(mp.string(),opts,&m4db); Check(ms.ok,"M4 open: "+ms.message);
  const std::vector<Row> initial={
    {"frag-A1","entity-A","needle quick parser symbol alpha","main.rs",kNeedleFn,"function",1,{10}},
    {"frag-A2","entity-A","needle slow parser helper beta","main.rs",kHelperFn,"function",1,{10}},
    {"frag-B1","entity-B","needle quick parser class gamma","lib.rs",kHelperFn,"class",2,{20}},
    {"frag-C1","entity-C","needle ordinary helper without target","root.txt",kHelperFn,"function",3,{}}, 
  };
  for(const auto&r:initial){NativeUpsert(native.p,r);PutM4(*m4db,r);}
  Code(zvec_collection_flush(native.p),"native initial flush"); ms=m4db->Flush();Check(ms.ok,"M4 initial flush: "+ms.message);
  Compare(native.p,*m4db,"initial");
  // Replace a fragment (same logical PK) with changed searchable text and metadata.
  Row update={"frag-A2","entity-A","needle refreshed parser symbol delta","main.rs",kNeedleFn,"function",1,{10}};
  NativeUpsert(native.p,update);PutM4(*m4db,update);
  Code(zvec_collection_flush(native.p),"native update flush"); ms=m4db->Flush();Check(ms.ok,"M4 update flush: "+ms.message);
  Compare(native.p,*m4db,"after-update");
  NativeDeleteFile(native.p,2); M4DeleteFile(*m4db,2);
  Code(zvec_collection_flush(native.p),"native delete flush"); ms=m4db->Flush();Check(ms.ok,"M4 delete flush: "+ms.message);
  Compare(native.p,*m4db,"after-delete");
  ms=m4db->CompactNow();Check(ms.ok,"M4 compaction: "+ms.message); Compare(native.p,*m4db,"after-compaction");
  // Exec a fresh writer process, append one record to each engine, and kill it
  // without destructors/flush; the parent then verifies durable WAL recovery.
  (void)zvec_collection_close(native.p); native.p=nullptr; m4db.reset();
  const pid_t child = fork();
  if (child == 0) { execl(argv[0], argv[0], "--crash-writer", np.c_str(), mp.c_str(), static_cast<char*>(nullptr)); _Exit(127); }
  int child_status = 0;
  const bool waited = child > 0 && waitpid(child, &child_status, 0) == child;
  Check(waited && WIFEXITED(child_status) && WEXITSTATUS(child_status) == 0, "crash-writer child completed before forced exit");
  native.p=OpenNative(np.string(),false); ms=m4::Index::Open(mp.string(),opts,&m4db);Check(ms.ok,"M4 WAL recovery reopen: "+ms.message);
  Compare(native.p,*m4db,"after-wal-reopen");
  ms=m4db->CompactNow();Check(ms.ok,"post-recovery compaction: "+ms.message);Compare(native.p,*m4db,"after-recovery-compaction");
  std::cout<<"zg_workload checks="<<checks<<" failures="<<failures<<" fixture_docs="<<initial.size()+1<<"\n";
  m4db.reset(); (void)zvec_collection_close(native.p); native.p=nullptr;
  fs::remove_all(base);
  return failures?1:0;
}
