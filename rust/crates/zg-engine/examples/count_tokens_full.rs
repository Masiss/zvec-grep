use std::env;
use std::fs;

fn main() {
    let mut args = env::args().skip(1);
    let tokenizer_path = args.next().expect("tokenizer.json path");
    let text_path = args.next().expect("text path");
    let mut tokenizer = tokenizers::Tokenizer::from_file(&tokenizer_path).expect("load tokenizer.json");
    tokenizer.with_truncation(None).expect("disable truncation");
    let text = fs::read_to_string(&text_path).expect("read text");
    let encoding = tokenizer.encode(text.as_str(), false).expect("encode");
    println!("{{\"tokens\":{},\"bytes\":{},\"truncated\":false}}", encoding.len(), text.len());
}
