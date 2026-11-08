use std::env;
use std::fs;

fn main() {
    let mut args = env::args().skip(1);
    let tokenizer_path = args.next().expect("tokenizer.json path");
    let text_path = args.next().expect("text path");
    let tokenizer = tokenizers::Tokenizer::from_file(&tokenizer_path).expect("load tokenizer.json");
    let text = fs::read_to_string(&text_path).expect("read text");
    let encoding = tokenizer.encode(text.as_str(), false).expect("encode");
    println!("{{\"tokens\":{},\"bytes\":{}}}", encoding.len(), text.len());
}
