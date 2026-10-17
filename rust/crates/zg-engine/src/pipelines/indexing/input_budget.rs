use std::collections::VecDeque;

use crate::extraction::ChunkOptions;

const DEFAULT_CHARS_PER_100_TOKENS: usize = 185;
const TOKEN_DENSE_CHARS_PER_100_TOKENS: usize = 100;
const TOKEN_DENSE_WINDOW_CHARS: usize = 16 * 1024;
const TOKEN_DENSE_WINDOW_STEP_CHARS: usize = 8 * 1024;
const TOKEN_DENSE_PERCENT: usize = 30;
const CHUNK_OVERLAP_PERCENT: usize = 15;

pub(super) fn index_chunk_options(
    max_input_tokens: Option<usize>,
    text: Option<&str>,
) -> ChunkOptions {
    let Some(max_input_tokens) = max_input_tokens else {
        return ChunkOptions::default();
    };
    index_chunk_options_for_density(
        max_input_tokens,
        is_token_dense_text(text, max_input_tokens),
    )
}

pub(super) fn index_chunk_options_for_density(
    max_input_tokens: usize,
    token_dense: bool,
) -> ChunkOptions {
    let chars_per_100_tokens = if token_dense {
        TOKEN_DENSE_CHARS_PER_100_TOKENS
    } else {
        DEFAULT_CHARS_PER_100_TOKENS
    };
    let max_chunk_chars = max_input_tokens.saturating_mul(chars_per_100_tokens) / 100;
    ChunkOptions {
        max_chunk_chars: Some(max_chunk_chars.max(1)),
        chunk_overlap_chars: Some(max_chunk_chars.saturating_mul(CHUNK_OVERLAP_PERCENT) / 100),
    }
}

fn is_token_dense_text(text: Option<&str>, max_input_tokens: usize) -> bool {
    let Some(text) = text else {
        return false;
    };
    let mut detector = TokenDenseDetector::new(max_input_tokens);
    detector.push_text(text);
    detector.is_token_dense()
}

pub(super) struct TokenDenseDetector {
    max_input_tokens: usize,
    window: VecDeque<u16>,
    total_chars: usize,
    dense_window_seen: bool,
}

impl TokenDenseDetector {
    pub(super) fn new(max_input_tokens: usize) -> Self {
        Self {
            max_input_tokens,
            window: VecDeque::with_capacity(TOKEN_DENSE_WINDOW_CHARS),
            total_chars: 0,
            dense_window_seen: false,
        }
    }

    pub(super) fn push_text(&mut self, text: &str) {
        for unit in text.encode_utf16() {
            self.total_chars = self.total_chars.saturating_add(1);
            self.window.push_back(unit);
            if self.window.len() > TOKEN_DENSE_WINDOW_CHARS {
                self.window.pop_front();
            }
            if self.window.len() == TOKEN_DENSE_WINDOW_CHARS {
                let start = self.total_chars - TOKEN_DENSE_WINDOW_CHARS;
                if start.is_multiple_of(TOKEN_DENSE_WINDOW_STEP_CHARS)
                    && is_token_dense_window(self.window.make_contiguous())
                {
                    self.dense_window_seen = true;
                }
            }
        }
    }

    pub(super) fn is_token_dense(&mut self) -> bool {
        if self.total_chars
            <= self
                .max_input_tokens
                .saturating_mul(TOKEN_DENSE_CHARS_PER_100_TOKENS)
                / 100
        {
            return false;
        }
        if self.total_chars <= TOKEN_DENSE_WINDOW_CHARS {
            return is_token_dense_window(self.window.make_contiguous());
        }
        let last_window_start = self.total_chars - TOKEN_DENSE_WINDOW_CHARS;
        if !last_window_start.is_multiple_of(TOKEN_DENSE_WINDOW_STEP_CHARS) {
            self.dense_window_seen |= is_token_dense_window(self.window.make_contiguous());
        }
        self.dense_window_seen
    }
}

fn is_token_dense_window(window: &[u16]) -> bool {
    let required = window
        .len()
        .saturating_mul(TOKEN_DENSE_PERCENT)
        .div_ceil(100);
    window
        .iter()
        .filter(|&&unit| {
            let ascii_letter = (u16::from(b'A')..=u16::from(b'Z')).contains(&unit)
                || (u16::from(b'a')..=u16::from(b'z')).contains(&unit);
            !ascii_letter && unit != 0x20 && unit != 0x09
        })
        .take(required)
        .count()
        >= required
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_main_chunk_budget_for_normal_and_token_dense_text() {
        assert_eq!(
            index_chunk_options(Some(100), Some("ordinary words and spaces")),
            ChunkOptions {
                max_chunk_chars: Some(185),
                chunk_overlap_chars: Some(27),
            }
        );
        assert_eq!(
            index_chunk_options(Some(100), Some(&"你".repeat(101))),
            ChunkOptions {
                max_chunk_chars: Some(100),
                chunk_overlap_chars: Some(15),
            }
        );
    }

    #[test]
    fn no_model_token_limit_means_no_chunk_override() {
        assert_eq!(
            index_chunk_options(None, Some("anything")),
            ChunkOptions::default()
        );
    }
}
