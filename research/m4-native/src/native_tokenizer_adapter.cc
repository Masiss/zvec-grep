// Research-only adapter compiling the pinned native Zvec standard tokenizer
// and lowercase filter directly from the checked-out native source.

#include "tokenizer.h"

#include <stdexcept>

#include "db/index/column/fts_column/tokenizer/standard_tokenizer.h"
#include "db/index/column/fts_column/tokenizer/token_filter.h"

namespace m4 {

std::vector<Token> TokenizeWithNativeZvec(const std::string& text) {
  static const zvec::fts::StandardTokenizer tokenizer = [] {
    zvec::fts::StandardTokenizer instance;
    const zvec::ailego::JsonObject empty_config;
    const auto status = instance.init(empty_config);
    if (!status.ok()) {
      throw std::runtime_error("native standard tokenizer setup failed: " +
                               status.message());
    }
    return instance;
  }();
  static const zvec::fts::LowercaseTokenFilter lowercase;

  const auto native_tokens = lowercase.filter(tokenizer.tokenize(text));
  std::vector<Token> result;
  result.reserve(native_tokens.size());
  for (const auto& token : native_tokens) {
    result.push_back(Token{token.text, token.position, token.offset});
  }
  return result;
}

}  // namespace m4
