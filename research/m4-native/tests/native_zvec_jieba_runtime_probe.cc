#include <zvec/c_api.h>
#include <filesystem>
#include <iostream>
#include <string>
#include <unistd.h>
#include <vector>

static void Error(const char* step, zvec_error_code_t code) {
  zvec_error_details_t detail{};
  zvec_get_last_error_details(&detail);
  std::cerr << "FAIL step=" << step << " code=" << zvec_error_code_to_string(code)
            << " message=" << (detail.message ? detail.message : "")
            << " file=" << (detail.file ? detail.file : "")
            << " line=" << detail.line
            << " function=" << (detail.function ? detail.function : "") << "\n";
}
#define TRY(expr, label) do { const auto c=(expr); if(c!=ZVEC_OK){Error(label,c); return 10;} } while(0)
int main(int argc, char** argv) {
  if(argc != 3) return 2;
  const std::string dict=argv[1];
  const bool set_dict=std::string(argv[2])=="configured";
  if(set_dict) zvec_set_default_jieba_dict_dir(dict.c_str());
  TRY(zvec_initialize(nullptr), "initialize");
  std::string pattern=(std::filesystem::temp_directory_path()/"m4-p31-jieba-XXXXXX").string();
  std::vector<char> tmp(pattern.begin(),pattern.end()); tmp.push_back('\0');
  char* dir=mkdtemp(tmp.data()); if(!dir) return 3;
  const std::string path=std::string(dir)+"/collection";
  auto* params=zvec_index_params_create(ZVEC_INDEX_TYPE_FTS);
  auto* filters=zvec_string_array_create(1);
  zvec_string_array_add(filters,0,"lowercase");
  TRY(zvec_index_params_set_fts_params(params,"jieba",filters,nullptr),"set tokenizer=jieba filters=[lowercase] extra_params=null");
  auto* field=zvec_field_schema_create("text",ZVEC_DATA_TYPE_STRING,false,0);
  TRY(zvec_field_schema_set_index_params(field,params),"set FTS field params");
  auto* schema=zvec_collection_schema_create("p31_jieba_probe");
  TRY(zvec_collection_schema_add_field(schema,field),"add text field");
  zvec_collection_t* collection=nullptr;
  const auto create=zvec_collection_create_and_open(path.c_str(),schema,nullptr,&collection);
  if(create!=ZVEC_OK){Error("create jieba collection",create);return 11;}
  auto* doc=zvec_doc_create(); zvec_doc_set_pk(doc,"doc-cn");
  const std::string text="深度学习模型";
  TRY(zvec_doc_add_field_by_value(doc,"text",ZVEC_DATA_TYPE_STRING,text.data(),text.size()),"set Chinese text");
  const zvec_doc_t* docs[]={doc}; zvec_write_result_t* wr=nullptr; size_t wrn=0;
  TRY(zvec_collection_insert_with_results(collection,docs,1,&wr,&wrn),"insert Jieba document");
  const bool write_ok=wrn==1 && wr && wr[0].code==ZVEC_OK;
  zvec_write_results_free(wr,wrn); zvec_doc_destroy(doc);
  if(!write_ok){std::cerr<<"FAIL per-doc insert result\n";return 12;}
  TRY(zvec_collection_flush(collection),"flush");
  auto* query=zvec_vector_query_create();
  TRY(zvec_vector_query_set_field_name(query,"text"),"query field");
  TRY(zvec_vector_query_set_topk(query,10),"topk");
  auto* fts=zvec_fts_create();
  const std::string term="学习";
  TRY(zvec_fts_set_query_string(fts,term.c_str()),"set query string");
  TRY(zvec_vector_query_set_fts(query,fts),"attach FTS query");
  auto* qparams=zvec_query_params_fts_create("OR");
  TRY(zvec_vector_query_set_fts_params(query,qparams),"attach FTS params");
  zvec_doc_t** out=nullptr; size_t count=0;
  TRY(zvec_collection_query(collection,query,&out,&count),"query Jieba subword");
  std::cout<<"PASS mode="<<argv[2]<<" tokenizer=jieba filters=lowercase extra_params=<null> query=学习 hits="<<count;
  for(size_t i=0;i<count;++i){const char* pk=zvec_doc_get_pk_pointer(out[i]);std::cout<<" pk="<<(pk?pk:"");}
  std::cout<<"\n";
  zvec_docs_free(out,count);
  zvec_fts_destroy(fts); zvec_vector_query_destroy(query); // query owns qparams
  zvec_collection_close(collection); zvec_collection_schema_destroy(schema);
  zvec_field_schema_destroy(field); zvec_index_params_destroy(params); zvec_string_array_destroy(filters);
  std::filesystem::remove_all(dir);
  return count==1?0:13;
}
