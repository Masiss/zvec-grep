use std::collections::VecDeque;

use crate::{
    EngineError,
    domain::{Content, Range},
    utils::{byte_offset_at_utf16_ceil, byte_offset_at_utf16_floor, line_byte_offsets, utf16_len},
};

use super::{
    ChunkOptions, ExtractedEntity, ExtractedEntityFragment, TextRange, TextSource,
    chunking::find_line_cut, validate_formats,
};

const DEFAULT_TEXT_CHUNK_CHARS: usize = 3_600;
const DEFAULT_TEXT_CHUNK_OVERLAP_CHARS: usize = 540;

pub(super) fn extract(
    source: &TextSource,
    options: ChunkOptions,
) -> Result<Vec<ExtractedEntity>, EngineError> {
    validate_formats(&source.formats)?;
    let (max_chars, overlap_chars) = resolve_options(options)?;
    Ok(extract_plain_text_entities(
        source,
        max_chars,
        overlap_chars,
    ))
}

pub(super) fn extract_plain_text_entities(
    source: &TextSource,
    max_chars: usize,
    overlap_chars: usize,
) -> Vec<ExtractedEntity> {
    chunk_text(&source.text, max_chars, overlap_chars)
        .into_iter()
        .enumerate()
        .map(|(index, chunk)| ExtractedEntity {
            index,
            source_range: Range::Text(chunk.range),
            content: Content::Text(chunk.text),
            metadata: None,
            fragments: vec![ExtractedEntityFragment { range: Range::Full }],
        })
        .collect()
}

fn resolve_options(options: ChunkOptions) -> Result<(usize, usize), EngineError> {
    let max_chars = options.max_chunk_chars.unwrap_or(DEFAULT_TEXT_CHUNK_CHARS);
    let overlap_chars = options
        .chunk_overlap_chars
        .unwrap_or(DEFAULT_TEXT_CHUNK_OVERLAP_CHARS);
    if max_chars == 0 {
        return Err(EngineError::invalid_argument(
            "text extractor requires a positive integer chunk size",
        ));
    }
    if overlap_chars >= max_chars {
        return Err(EngineError::invalid_argument(
            "text extractor requires overlap to be smaller than chunk size",
        ));
    }
    Ok((max_chars, overlap_chars))
}

#[derive(Debug)]
struct TextChunk {
    text: String,
    range: TextRange,
}

struct BufferedLine {
    text: String,
    start_byte: usize,
    line_number: usize,
}

/// Incremental equivalent of `chunk_text`, retaining only the current line and
/// the bounded line window needed for chunk overlap.
pub(crate) struct TextStreamChunker {
    max_chars: usize,
    overlap_chars: usize,
    current_line: String,
    line_start_byte: usize,
    line_column_offset: usize,
    line_number: usize,
    long_line: bool,
    lines: VecDeque<BufferedLine>,
    total_bytes: usize,
    saw_any_input: bool,
    last_was_newline: bool,
    next_entity_index: usize,
}

impl TextStreamChunker {
    pub(crate) fn from_options(options: ChunkOptions) -> Result<Self, EngineError> {
        let (max_chars, overlap_chars) = resolve_options(options)?;
        Self::new(max_chars, overlap_chars)
    }

    pub(crate) fn new(max_chars: usize, overlap_chars: usize) -> Result<Self, EngineError> {
        if max_chars == 0 || overlap_chars >= max_chars {
            return Err(EngineError::invalid_argument(
                "streaming text extractor requires positive chunk size and smaller overlap",
            ));
        }
        Ok(Self {
            max_chars,
            overlap_chars,
            current_line: String::new(),
            line_start_byte: 0,
            line_column_offset: 0,
            line_number: 1,
            long_line: false,
            lines: VecDeque::new(),
            total_bytes: 0,
            saw_any_input: false,
            last_was_newline: false,
            next_entity_index: 0,
        })
    }

    pub(crate) fn push_text(&mut self, text: &str) -> Vec<ExtractedEntity> {
        let mut chunks = Vec::new();
        let mut cursor = 0;
        while cursor < text.len() {
            if let Some(relative_newline) = text[cursor..].find('\n') {
                let end = cursor + relative_newline;
                let segment = &text[cursor..end];
                self.saw_any_input = true;
                self.append_line_segment(segment, &mut chunks);
                self.total_bytes = self.total_bytes.saturating_add(segment.len());
                self.complete_line(&mut chunks);
                self.total_bytes = self.total_bytes.saturating_add(1);
                self.line_start_byte = self.total_bytes;
                self.line_column_offset = 0;
                self.line_number = self.line_number.saturating_add(1);
                self.long_line = false;
                self.last_was_newline = true;
                cursor = end + 1;
            } else {
                let segment = &text[cursor..];
                self.saw_any_input = true;
                self.append_line_segment(segment, &mut chunks);
                self.total_bytes = self.total_bytes.saturating_add(segment.len());
                self.last_was_newline = false;
                cursor = text.len();
            }
        }
        self.into_entities(chunks)
    }

    pub(crate) fn finish(&mut self) -> Vec<ExtractedEntity> {
        let mut chunks = Vec::new();
        if !self.saw_any_input {
            return Vec::new();
        }
        if self.last_was_newline {
            self.lines.push_back(BufferedLine {
                text: String::new(),
                start_byte: self.total_bytes,
                line_number: self.line_number,
            });
        } else {
            self.complete_line(&mut chunks);
        }
        self.drain_lines(true, false, &mut chunks);
        self.into_entities(chunks)
    }

    fn append_line_segment(&mut self, segment: &str, output: &mut Vec<TextChunk>) {
        self.current_line.push_str(segment);
        if !self.long_line && utf16_len(&self.current_line).saturating_add(1) > self.max_chars {
            self.long_line = true;
            self.drain_lines(false, true, output);
        }
        if self.long_line {
            self.drain_long_line_prefix(output);
        }
    }

    fn complete_line(&mut self, output: &mut Vec<TextChunk>) {
        if self.long_line {
            self.drain_long_line_prefix(output);
            if !self.current_line.is_empty() {
                self.emit_long_line_piece(self.current_line.len(), output);
            }
            self.current_line.clear();
        } else {
            self.lines.push_back(BufferedLine {
                text: std::mem::take(&mut self.current_line),
                start_byte: self.line_start_byte,
                line_number: self.line_number,
            });
            self.drain_lines(false, false, output);
        }
    }

    fn drain_long_line_prefix(&mut self, output: &mut Vec<TextChunk>) {
        while utf16_len(&self.current_line) > self.max_chars {
            let utf16_cut = find_line_cut(&self.current_line, self.max_chars);
            let mut byte_cut = byte_offset_at_utf16_floor(&self.current_line, utf16_cut);
            if byte_cut == 0 {
                byte_cut = byte_offset_at_utf16_ceil(&self.current_line, utf16_cut);
            }
            self.emit_long_line_piece(byte_cut, output);
        }
    }

    fn emit_long_line_piece(&mut self, byte_count: usize, output: &mut Vec<TextChunk>) {
        if byte_count == 0 {
            return;
        }
        let rest = self.current_line.split_off(byte_count);
        let piece = std::mem::replace(&mut self.current_line, rest);
        let start = self.line_start_byte;
        let column = self.line_column_offset;
        let byte_count = piece.len();
        if !piece.trim().is_empty() {
            output.push(TextChunk {
                text: piece,
                range: TextRange::from_coordinates(
                    start,
                    start + byte_count,
                    self.line_number,
                    self.line_number,
                    column,
                    column + byte_count,
                )
                .expect("streamed chunk coordinates refer to source text"),
            });
        }
        self.line_start_byte = self.line_start_byte.saturating_add(byte_count);
        self.line_column_offset = self.line_column_offset.saturating_add(byte_count);
    }

    fn drain_lines(&mut self, eof: bool, force_boundary: bool, output: &mut Vec<TextChunk>) {
        while !self.lines.is_empty() {
            let mut used_chars = 0_usize;
            let mut end = 0;
            while end < self.lines.len() {
                let line_length = utf16_len(&self.lines[end].text).saturating_add(1);
                if used_chars.saturating_add(line_length) > self.max_chars && end > 0 {
                    break;
                }
                used_chars = used_chars.saturating_add(line_length);
                end += 1;
            }
            let reached_end = end == self.lines.len();
            if reached_end && !eof && !force_boundary {
                break;
            }

            let first = &self.lines[0];
            let last = &self.lines[end - 1];
            let mut text = String::new();
            for (index, line) in self.lines.iter().take(end).enumerate() {
                if index > 0 {
                    text.push('\n');
                }
                text.push_str(&line.text);
            }
            if !text.trim().is_empty() {
                output.push(TextChunk {
                    text,
                    range: TextRange::from_coordinates(
                        first.start_byte,
                        last.start_byte + last.text.len(),
                        first.line_number,
                        last.line_number,
                        0,
                        last.text.len(),
                    )
                    .expect("streamed chunk coordinates refer to source lines"),
                });
            }

            if reached_end && eof {
                self.lines.clear();
                break;
            }
            let next_start = stream_next_start_line(&self.lines, end, self.overlap_chars);
            self.lines.drain(..next_start);
        }
    }

    fn into_entities(&mut self, chunks: Vec<TextChunk>) -> Vec<ExtractedEntity> {
        chunks
            .into_iter()
            .map(|chunk| {
                let index = self.next_entity_index;
                self.next_entity_index = self.next_entity_index.saturating_add(1);
                ExtractedEntity {
                    index,
                    source_range: Range::Text(chunk.range),
                    content: Content::Text(chunk.text),
                    metadata: None,
                    fragments: vec![ExtractedEntityFragment { range: Range::Full }],
                }
            })
            .collect()
    }
}

fn stream_next_start_line(
    lines: &VecDeque<BufferedLine>,
    end: usize,
    overlap_chars: usize,
) -> usize {
    if overlap_chars == 0 {
        return end;
    }
    let mut overlap_lines = 0;
    let mut overlap_count = 0;
    for line in lines.iter().take(end).rev() {
        if overlap_count >= overlap_chars {
            break;
        }
        overlap_count = overlap_count.saturating_add(utf16_len(&line.text).saturating_add(1));
        overlap_lines += 1;
    }
    let next_start = end.saturating_sub(overlap_lines);
    if next_start > 0 { next_start } else { end }
}

fn chunk_text(text: &str, max_chars: usize, overlap_chars: usize) -> Vec<TextChunk> {
    if text.trim().is_empty() {
        return Vec::new();
    }

    let lines = text.split('\n').collect::<Vec<_>>();
    let line_offsets = line_byte_offsets(text);
    let mut chunks = Vec::new();
    let mut start_index = 0;

    while start_index < lines.len() {
        if utf16_len(lines[start_index]) + 1 > max_chars {
            split_long_line(
                lines[start_index],
                start_index,
                line_offsets[start_index],
                max_chars,
                &mut chunks,
            );
            start_index += 1;
            continue;
        }

        let mut used_chars = 0;
        let mut end_index = start_index;
        while end_index < lines.len() {
            let line_length = utf16_len(lines[end_index]) + 1;
            if used_chars + line_length > max_chars && end_index > start_index {
                break;
            }
            used_chars += line_length;
            end_index += 1;
        }

        let chunk = lines[start_index..end_index].join("\n");
        if !chunk.trim().is_empty() {
            let end_line_index = end_index - 1;
            chunks.push(TextChunk {
                text: chunk,
                range: TextRange::from_coordinates(
                    line_offsets[start_index],
                    line_offsets[end_line_index] + lines[end_line_index].len(),
                    start_index + 1,
                    end_index,
                    0,
                    lines[end_line_index].len(),
                )
                .expect("chunk coordinates refer to source lines"),
            });
        }

        if end_index >= lines.len() {
            break;
        }
        start_index = compute_next_start_line(&lines, start_index, end_index, overlap_chars);
    }

    chunks
}

fn split_long_line(
    line: &str,
    line_index: usize,
    line_offset: usize,
    max_chars: usize,
    chunks: &mut Vec<TextChunk>,
) {
    let mut byte_offset = 0;
    while byte_offset < line.len() {
        let rest = &line[byte_offset..];
        let slice_chars = find_line_cut(rest, max_chars);
        let mut slice_bytes = byte_offset_at_utf16_floor(rest, slice_chars);
        if slice_bytes == 0 {
            slice_bytes = byte_offset_at_utf16_ceil(rest, slice_chars);
        }
        let slice = &rest[..slice_bytes];
        if !slice.trim().is_empty() {
            chunks.push(TextChunk {
                text: slice.to_owned(),
                range: TextRange::from_coordinates(
                    line_offset + byte_offset,
                    line_offset + byte_offset + slice_bytes,
                    line_index + 1,
                    line_index + 1,
                    byte_offset,
                    byte_offset + slice_bytes,
                )
                .expect("chunk coordinates refer to a source line"),
            });
        }
        byte_offset += slice_bytes;
    }
}

fn compute_next_start_line(
    lines: &[&str],
    start_index: usize,
    end_index: usize,
    overlap_chars: usize,
) -> usize {
    if overlap_chars == 0 {
        return end_index;
    }

    let mut overlap_lines = 0;
    let mut overlap_count = 0;
    for index in (start_index..end_index).rev() {
        if overlap_count >= overlap_chars {
            break;
        }
        overlap_count += utf16_len(lines[index]) + 1;
        overlap_lines += 1;
    }
    let next_start = end_index - overlap_lines;
    if next_start > start_index {
        next_start
    } else {
        end_index
    }
}

#[cfg(test)]
mod tests {
    use crate::domain::{Content, FileFormat, Range, TextRange};

    use super::super::test_content;

    use super::super::{ChunkOptions, test_source};
    use super::{TextStreamChunker, chunk_text, extract};

    #[test]
    fn streaming_chunks_match_full_text_across_input_boundaries() {
        let long_line = format!("prefix {} suffix", "中😀,word. ".repeat(12));
        let inputs = [
            "",
            "alpha beta\ngamma delta\nepsilon zeta\n",
            "\t中😀 \r\n\r\n",
            "one\ntwo\nthree\nfour\nfive\n",
            long_line.as_str(),
            "  \n punctuation!?; /path/name\n終わり",
        ];
        for text in inputs {
            let boundaries = text
                .char_indices()
                .map(|(offset, _)| offset)
                .chain(std::iter::once(text.len()))
                .collect::<Vec<_>>();
            for max_chars in [1_usize, 2, 5, 10, 18] {
                let overlap = max_chars.saturating_sub(1).min(3);
                let expected = chunk_text(text, max_chars, overlap)
                    .into_iter()
                    .map(|chunk| chunk_signature(&chunk.text, &chunk.range))
                    .collect::<Vec<_>>();
                for step in [1, 2, 7] {
                    let mut chunker = TextStreamChunker::new(max_chars, overlap).expect("chunker");
                    let mut actual = Vec::new();
                    for start in (0..boundaries.len().saturating_sub(1)).step_by(step) {
                        let end = (start + step).min(boundaries.len() - 1);
                        let produced = chunker.push_text(&text[boundaries[start]..boundaries[end]]);
                        actual.extend(produced.iter().map(|entity| {
                            let Content::Text(value) = &entity.content else {
                                panic!("streamed text content expected");
                            };
                            let Range::Text(range) = &entity.source_range else {
                                panic!("streamed text range expected");
                            };
                            chunk_signature(value, range)
                        }));
                    }
                    actual.extend(chunker.finish().iter().map(|entity| {
                        let Content::Text(value) = &entity.content else {
                            panic!("streamed text content expected");
                        };
                        let Range::Text(range) = &entity.source_range else {
                            panic!("streamed text range expected");
                        };
                        chunk_signature(value, range)
                    }));
                    assert_eq!(
                        actual, expected,
                        "text={text:?}, max={max_chars}, step={step}"
                    );
                }
            }
        }
    }

    fn chunk_signature(
        text: &str,
        range: &TextRange,
    ) -> (String, (usize, usize, usize, usize, usize, usize)) {
        (
            text.to_owned(),
            (
                range.start_byte_offset(),
                range.end_byte_offset(),
                range.start_line(),
                range.end_line(),
                range.start_byte_column(),
                range.end_byte_column(),
            ),
        )
    }

    #[test]
    fn unicode_chunks_obey_the_budget_and_advance_at_tiny_limits() {
        for (text, max_chars, expected) in [
            ("aaa😀z", 4, vec!["aaa", "😀z"]),
            ("a😀z", 2, vec!["a", "😀", "z"]),
            ("😀a😀", 1, vec!["😀", "a", "😀"]),
        ] {
            let chunks = chunk_text(text, max_chars, 0);
            assert_eq!(
                chunks
                    .iter()
                    .map(|chunk| chunk.text.as_str())
                    .collect::<Vec<_>>(),
                expected
            );
            let mut end = 0;
            for chunk in chunks {
                assert!(
                    crate::utils::utf16_len(&chunk.text) <= max_chars
                        || (max_chars == 1 && chunk.text.chars().count() == 1)
                );
                assert_eq!(chunk.range.start_byte_offset(), end);
                end = chunk.range.end_byte_offset();
                assert_eq!(&text[chunk.range.start_byte_offset()..end], chunk.text);
            }
            assert_eq!(end, text.len());
        }
    }

    #[test]
    fn long_lines_keep_punctuation_cuts_and_exact_source_coverage() {
        let prefix = "aaaaaaa.;-";
        let short = chunk_text(prefix, 10, 0);
        assert_eq!(short.len(), 1);
        assert_eq!(short[0].text, prefix);

        let text = format!("{prefix}{}", "中😀z".repeat(16 * 1024));
        let chunks = chunk_text(&text, 10, 0);
        assert_eq!(chunks[0].text, "aaaaaaa.");
        let mut end = 0;
        for chunk in chunks {
            assert!(crate::utils::utf16_len(&chunk.text) <= 10);
            assert_eq!(chunk.range.start_byte_offset(), end);
            end = chunk.range.end_byte_offset();
            assert_eq!(&text[chunk.range.start_byte_offset()..end], chunk.text);
        }
        assert_eq!(end, text.len());
    }

    #[test]
    fn validates_chunks_ranges_and_overlap_like_typescript() {
        let source = test_source(
            FileFormat::Text,
            "fixture.txt",
            "alpha beta\ngamma delta\nepsilon zeta\n",
        );
        let chunks = extract(
            &source,
            ChunkOptions {
                max_chunk_chars: Some(18),
                chunk_overlap_chars: Some(6),
            },
        )
        .expect("text extraction");
        assert!(chunks.len() >= 2);
        assert_eq!(chunks[0].index(), 0);
        assert_eq!(
            *chunks[0].source_range(),
            Range::Text(TextRange::from_coordinates(0, 10, 1, 1, 0, 10).expect("first line"))
        );
        for chunk in &chunks {
            let Content::Text(text) = &test_content(chunk) else {
                panic!("text fragment expected");
            };
            assert!(text.chars().count() <= 18);
        }
    }

    #[test]
    fn splits_long_unicode_lines_on_character_boundaries() {
        let text = format!("前言\r\nprefix {} suffix\r\n尾声", "😀".repeat(20));
        let source = test_source(FileFormat::Text, "unicode.txt", &text);
        let line_starts = [0, "前言\r\n".len(), text.len() - "尾声".len()];
        for max_chars in [1, 10] {
            let chunks = extract(
                &source,
                ChunkOptions {
                    max_chunk_chars: Some(max_chars),
                    chunk_overlap_chars: Some(0),
                },
            )
            .expect("unicode extraction");
            assert!(chunks.len() >= 3);
            for chunk in chunks {
                let Content::Text(content) = test_content(&chunk) else {
                    panic!("text fragment expected");
                };
                assert!(content.chars().count() <= max_chars);
                let Range::Text(range) = *chunk.source_range() else {
                    panic!("text range expected");
                };
                assert_eq!(
                    source
                        .text
                        .get(range.start_byte_offset()..range.end_byte_offset()),
                    Some(content.as_str())
                );
                assert_eq!(
                    range.start_byte_offset(),
                    line_starts[range.start_line() - 1] + range.start_byte_column()
                );
                assert_eq!(
                    range.end_byte_offset(),
                    line_starts[range.end_line() - 1] + range.end_byte_column()
                );
            }
        }

        let source = test_source(FileFormat::Text, "whitespace.txt", "\t中😀 \r\n\r\n");
        let chunks = extract(&source, ChunkOptions::default()).expect("whitespace extraction");
        assert_eq!(chunks.len(), 1);
        assert_eq!(test_content(&chunks[0]), Content::Text(source.text.clone()));
        let Range::Text(range) = chunks[0].source_range() else {
            panic!("text range expected");
        };
        assert_eq!(
            crate::utils::slice_text(
                &source.text,
                range.start_byte_offset(),
                range.end_byte_offset()
            )
            .expect("full source span"),
            source.text
        );
        assert_eq!((range.end_line(), range.end_byte_column()), (3, 0));
    }

    #[test]
    fn rejects_invalid_options_and_empty_metadata() {
        let source = test_source(FileFormat::Text, "fixture.txt", "value");
        assert!(
            extract(
                &source,
                ChunkOptions {
                    max_chunk_chars: Some(0),
                    chunk_overlap_chars: None,
                }
            )
            .is_err()
        );
        assert!(
            extract(
                &source,
                ChunkOptions {
                    max_chunk_chars: Some(10),
                    chunk_overlap_chars: Some(10),
                }
            )
            .is_err()
        );

        let blank = test_source(FileFormat::Text, "blank.txt", " \n\t");
        assert!(
            extract(&blank, ChunkOptions::default())
                .expect("blank extraction")
                .is_empty()
        );
    }
}
