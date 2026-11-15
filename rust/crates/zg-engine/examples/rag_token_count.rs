//! Count tokens with the frozen code-model tokenizer.
//! Usage: rag_token_count TOKENIZER_JSON INPUT_JSON
//! Input: {"texts": ["...", "..."]}; output: {"counts": [n, ...]}.

use std::{fs, path::PathBuf};

use serde::Deserialize;
use serde_json::json;
use tokenizers::Tokenizer;

#[derive(Deserialize)]
struct Input {
    texts: Vec<String>,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let tokenizer_path = PathBuf::from(args.next().expect("TOKENIZER_JSON"));
    let input_path = PathBuf::from(args.next().expect("INPUT_JSON"));
    let mut tokenizer = Tokenizer::from_file(tokenizer_path).map_err(|e| e.to_string())?;
    tokenizer.with_truncation(None).map_err(|e| e.to_string())?;
    let input: Input = serde_json::from_slice(&fs::read(input_path)?)?;
    let mut counts = Vec::with_capacity(input.texts.len());
    for text in input.texts {
        let encoding = tokenizer.encode(text, false).map_err(|e| e.to_string())?;
        counts.push(encoding.len());
    }
    println!("{}", serde_json::to_string(&json!({"counts": counts}))?);
    Ok(())
}
