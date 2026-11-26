use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use tokenizers::Tokenizer;

fn collect(dir: &Path, exts: &[&str], out: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        let p = entry.path();
        if p.is_dir() {
            if p.file_name().and_then(|s| s.to_str()) == Some(".git") { continue; }
            collect(&p, exts, out);
        } else if p.extension().and_then(|s| s.to_str()).is_some_and(|e| exts.contains(&e)) {
            out.push(p);
        }
    }
}

fn main() {
    let mut args = env::args().skip(1);
    let tokenizer_path = args.next().expect("tokenizer.json path");
    let root = args.next().expect("corpus root");
    let exts: Vec<String> = args.next().unwrap_or_else(|| "rs,cs".to_owned()).split(',').map(str::to_owned).collect();
    let out_path = args.next().expect("output json");

    let tokenizer = Tokenizer::from_file(&tokenizer_path).expect("load tokenizer");
    let mut files = Vec::new();
    collect(Path::new(&root), &exts.iter().map(String::as_str).collect::<Vec<_>>(), &mut files);
    let mut counts: BTreeMap<u32, u64> = BTreeMap::new();
    let mut total_tokens = 0u64;
    let mut truncated_files = 0u64;
    let mut longest = 0usize;
    for p in &files {
        let text = fs::read_to_string(p).unwrap_or_default();
        let enc = tokenizer.encode(text.as_str(), false).expect("encode");
        let ids = enc.get_ids();
        if ids.len() > longest { longest = ids.len(); }
        if ids.len() >= 512 { truncated_files += 1; }
        total_tokens += ids.len() as u64;
        for id in ids { *counts.entry(*id).or_default() += 1; }
    }
    let result = serde_json::json!({
        "root": root,
        "files": files.len(),
        "extensions": exts,
        "total_tokens": total_tokens,
        "unique_token_ids": counts.len(),
        "files_at_or_over_512_tokens": truncated_files,
        "longest_encoded_tokens": longest,
        "token_counts": counts,
    });
    fs::write(out_path, serde_json::to_string_pretty(&result).expect("serialize")).expect("write output");
    println!("files={} tokens={} unique={} over512={} longest={}", files.len(), total_tokens, counts.len(), truncated_files, longest);
}
