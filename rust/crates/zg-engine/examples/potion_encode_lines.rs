use std::env;
use std::io::{self, BufRead, Write};

use tokenizers::Tokenizer;

fn main() {
    let mut args = env::args().skip(1);
    let tokenizer_path = args.next().expect("tokenizer.json path");
    let tokenizer = Tokenizer::from_file(&tokenizer_path).expect("load tokenizer");
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = stdout.lock();
    for line in stdin.lock().lines() {
        let text = line.expect("read line");
        let ids = tokenizer.encode(text.as_str(), false).expect("encode").get_ids().to_vec();
        let ids: Vec<String> = ids.iter().map(u32::to_string).collect();
        writeln!(out, "[{}]", ids.join(",")).expect("write ids");
    }
}
