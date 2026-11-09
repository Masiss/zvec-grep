//! Diagnostic probe for zvec FTS query shapes (`match_string` vs `query_string`).
use std::{fs, path::PathBuf};
use zvec_rust::{Collection, CollectionOptions, Fts, FtsQueryParams, SearchQuery};

fn run(
    collection: &Collection,
    label: &str,
    build: impl Fn() -> Result<SearchQuery, Box<dyn std::error::Error>>,
) {
    match build() {
        Ok(query) => match collection.query(&query) {
            Ok(docs) => println!("{label}: {}", docs.len()),
            Err(error) => println!("{label}: ERR {}", error.message),
        },
        Err(error) => println!("{label}: BUILDERR {error}"),
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let index = PathBuf::from(args.next().expect("index dir"));
    let query = args.next().expect("query");
    let topk: i32 = args.next().unwrap_or_else(|| "10".to_owned()).parse()?;
    let terms = query.split_whitespace().collect::<Vec<_>>();
    let mut fragments = None;
    for entry in fs::read_dir(&index)? {
        let entry = entry?;
        let name = entry.file_name().into_string().unwrap_or_default();
        if name.starts_with("fragments_") {
            fragments = Some(entry.path());
        }
    }
    let fragments = fragments.expect("fragments dir");
    let mut options = CollectionOptions::new()?;
    options.set_read_only(true)?;
    let path = fragments.to_str().ok_or("fragments path is not UTF-8")?;
    let collection = Collection::open(path, Some(&options))?;
    run(&collection, "match raw", || {
        let mut fts = Fts::new()?;
        fts.set_match_string(&query)?;
        Ok(SearchQuery::fts("text", &fts, topk)?)
    });
    run(&collection, "match OR", || {
        let mut fts = Fts::new()?;
        fts.set_match_string(&query)?;
        let mut request = SearchQuery::fts("text", &fts, topk)?;
        request.set_fts_params(FtsQueryParams::new(Some("OR"))?)?;
        Ok(request)
    });
    run(&collection, "match AND", || {
        let mut fts = Fts::new()?;
        fts.set_match_string(&query)?;
        let mut request = SearchQuery::fts("text", &fts, topk)?;
        request.set_fts_params(FtsQueryParams::new(Some("AND"))?)?;
        Ok(request)
    });
    run(&collection, "qstring raw", || {
        let mut fts = Fts::new()?;
        fts.set_query_string(&query)?;
        Ok(SearchQuery::fts("text", &fts, topk)?)
    });
    let or_terms = terms
        .iter()
        .map(|term| format!("\"{term}\""))
        .collect::<Vec<_>>()
        .join(" OR ");
    run(&collection, &format!("qstring OR [{or_terms}]"), || {
        let mut fts = Fts::new()?;
        fts.set_query_string(&or_terms)?;
        Ok(SearchQuery::fts("text", &fts, topk)?)
    });
    let and_terms = terms
        .iter()
        .map(|term| format!("\"{term}\""))
        .collect::<Vec<_>>()
        .join(" AND ");
    run(&collection, &format!("qstring AND [{and_terms}]"), || {
        let mut fts = Fts::new()?;
        fts.set_query_string(&and_terms)?;
        Ok(SearchQuery::fts("text", &fts, topk)?)
    });
    collection.close()?;
    Ok(())
}
