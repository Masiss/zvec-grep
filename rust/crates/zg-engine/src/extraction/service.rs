//! Extraction routing and shared implementation helpers.

use std::borrow::Cow;

#[cfg(test)]
use super::TextSource;
use super::{
    ChunkOptions, ExtractedEntity, ExtractionOutput, Source, SourceKind, code, image, markdown,
    text,
};
use crate::{
    EngineError,
    domain::{
        CodeMetadata, Content, EntityMetadata, FileCategory, FileFormat, MarkdownMetadata, Range,
    },
    utils::{collapse_whitespace, take_utf16, utf16_len},
};

pub(super) fn extract<'source>(
    source: impl Into<Source<'source>>,
    options: ChunkOptions,
) -> Result<Vec<ExtractedEntity>, EngineError> {
    Ok(extract_for_indexing(source, options)?.fragments)
}

pub(super) fn extract_for_indexing<'source>(
    source: impl Into<Source<'source>>,
    options: ChunkOptions,
) -> Result<ExtractionOutput, EngineError> {
    let source = source.into();
    let source_text = match &source {
        Source::Text(source) => Some(source.text.as_str()),
        Source::Image(_) => None,
    };
    let output = match source {
        Source::Text(source) if is_code_source(&source.formats) => {
            code::extract_for_indexing(source, options)
        }
        Source::Image(source) => Ok(ExtractionOutput {
            fragments: image::extract(source),
            graph: None,
        }),
        Source::Text(source) if source.formats.contains(&FileFormat::Markdown) => {
            Ok(ExtractionOutput {
                fragments: markdown::extract(source, options)?,
                graph: None,
            })
        }
        Source::Text(source) => Ok(ExtractionOutput {
            fragments: text::extract(source, options)?,
            graph: None,
        }),
    }?;
    if let Some(source_text) = source_text {
        for entity in &output.fragments {
            if let Range::Text(range) = &entity.source_range {
                let original = crate::utils::slice_text(
                    source_text,
                    range.start_byte_offset(),
                    range.end_byte_offset(),
                )?;
                if !matches!(&entity.content, Content::Text(content) | Content::Code(content) if content == original)
                {
                    return Err(EngineError::internal(
                        "entity content differs from its source range",
                    ));
                }
            }
        }
    }
    Ok(output)
}

pub(super) fn source_kind(formats: &[FileFormat]) -> Option<SourceKind> {
    if let Some(format) = formats
        .iter()
        .find(|format| format.categories().contains(&FileCategory::Image))
    {
        return Some(SourceKind::Image(*format));
    }
    if has_category(formats, FileCategory::Code)
        || has_category(formats, FileCategory::Data)
        || formats.iter().any(|format| {
            matches!(
                format,
                FileFormat::AsciiDoc
                    | FileFormat::Eml
                    | FileFormat::Markdown
                    | FileFormat::Mhtml
                    | FileFormat::Org
                    | FileFormat::Rst
                    | FileFormat::Rtf
                    | FileFormat::Srt
                    | FileFormat::Text
                    | FileFormat::WebVtt
            )
        })
    {
        Some(SourceKind::Text)
    } else {
        None
    }
}

pub(super) fn is_code_source(formats: &[FileFormat]) -> bool {
    !has_category(formats, FileCategory::Data) && has_category(formats, FileCategory::Code)
}

fn has_category(formats: &[FileFormat], category: FileCategory) -> bool {
    formats
        .iter()
        .any(|format| format.categories().contains(&category))
}

pub(super) fn vector_content_for_fragment(
    content: Cow<'_, Content>,
    metadata: Option<&EntityMetadata>,
    max_chars: Option<usize>,
) -> Vec<Content> {
    let metadata = vector_metadata_text(metadata, metadata_budget(max_chars));
    // A borrowed input needs one owned copy; an owned chunk is moved as-is so the
    // caller's already-allocated text is not cloned again.
    let content = match content {
        Cow::Borrowed(content) => content.clone(),
        Cow::Owned(content) => content,
    };
    match content {
        Content::Text(text) => vec![Content::Text(prefixed_metadata(metadata, text))],
        Content::Code(text) => vec![Content::Code(prefixed_metadata(metadata, text))],
        image if metadata.is_empty() => vec![image],
        image => vec![Content::Text(metadata), image],
    }
}

/// Prepends the metadata block exactly as the previous `format!` call did.
fn prefixed_metadata(metadata: String, text: String) -> String {
    if metadata.is_empty() {
        return text;
    }
    let mut output = String::with_capacity(metadata.len() + 1 + text.len());
    output.push_str(&metadata);
    output.push('\n');
    output.push_str(&text);
    output
}

pub(super) fn validate_formats(formats: &[FileFormat]) -> Result<(), EngineError> {
    if formats.is_empty()
        || (formats.len() > 1 && formats.contains(&FileFormat::Unknown))
        || formats
            .iter()
            .enumerate()
            .any(|(index, format)| formats[..index].contains(format))
    {
        return Err(EngineError::invalid_argument(
            "source formats must be non-empty and unique; unknown must stand alone",
        ));
    }
    Ok(())
}

pub(super) fn chunk_options_for_metadata(
    max_chunk_chars: usize,
    chunk_overlap_chars: usize,
    metadata: Option<&EntityMetadata>,
) -> (usize, usize) {
    let metadata_text = vector_metadata_text(metadata, metadata_budget(Some(max_chunk_chars)));
    let separator_chars = usize::from(!metadata_text.is_empty());
    let content_max = max_chunk_chars
        .saturating_sub(utf16_len(&metadata_text) + separator_chars)
        .max(1);
    let overlap = chunk_overlap_chars.min(content_max.saturating_sub(1));
    (content_max, overlap)
}

pub(super) fn fit_text_to_chars(value: &str, max_chars: usize) -> String {
    if utf16_len(value) <= max_chars {
        return value.to_owned();
    }
    if max_chars <= 3 {
        return ".".repeat(max_chars);
    }
    let prefix = take_utf16(value, max_chars - 3).trim_end();
    format!("{prefix}...")
}

fn vector_metadata_text(metadata: Option<&EntityMetadata>, max_chars: Option<usize>) -> String {
    let Some(metadata) = metadata else {
        return String::new();
    };

    let lines = match metadata {
        EntityMetadata::Code(CodeMetadata {
            symbol_type,
            symbol_name,
            scope,
            signature,
            documentation,
            language,
            ..
        }) => vec![
            match (symbol_type, symbol_name) {
                (Some(kind), Some(name)) => Some(format!("symbol: {} {name}", kind.as_str())),
                (Some(kind), None) => Some(format!("symbol: {}", kind.as_str())),
                (None, Some(name)) => Some(format!("symbol: {name}")),
                (None, None) => None,
            },
            (*language != Some(crate::domain::Language::CSharp))
                .then(|| scope.as_ref().map(|value| format!("scope: {value}")))
                .flatten(),
            signature
                .as_ref()
                .map(|value| format!("signature: {}", collapse_whitespace(value))),
            documentation
                .as_ref()
                .map(|value| format!("doc: {}", collapse_whitespace(value))),
        ],
        EntityMetadata::Markdown(MarkdownMetadata {
            heading,
            level,
            scope,
        }) => vec![
            heading.as_ref().map(|value| format!("heading: {value}")),
            level.map(|value| format!("heading_level: {value}")),
            scope.as_ref().map(|value| format!("scope: {value}")),
        ],
    };

    let text = lines.into_iter().flatten().collect::<Vec<_>>().join("\n");
    max_chars.map_or(text.clone(), |limit| fit_text_to_chars(&text, limit))
}

fn metadata_budget(max_chars: Option<usize>) -> Option<usize> {
    max_chars.map(|value| value / 4)
}

#[cfg(test)]
pub(super) fn test_source(format: FileFormat, relative_path: &str, text: &str) -> TextSource {
    TextSource {
        relative_path: crate::domain::SourcePath::new(relative_path).expect("source path"),
        formats: vec![format],
        text: text.to_owned(),
    }
}

#[cfg(test)]
pub(super) fn test_metadata(entity: &ExtractedEntity) -> Option<&EntityMetadata> {
    entity.metadata.as_ref()
}

#[cfg(test)]
pub(super) fn test_content(entity: &ExtractedEntity) -> Content {
    entity.content.clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn routes_supported_sources_without_confusing_formats_and_reader_capabilities() {
        for (format, expected) in [
            (FileFormat::Rust, Some(SourceKind::Text)),
            (FileFormat::Markdown, Some(SourceKind::Text)),
            (FileFormat::Json, Some(SourceKind::Text)),
            (FileFormat::Png, Some(SourceKind::Image(FileFormat::Png))),
            (FileFormat::Svg, Some(SourceKind::Image(FileFormat::Svg))),
            (FileFormat::Pdf, None),
            (FileFormat::Word, None),
            (FileFormat::Unknown, None),
            (FileFormat::Binary, None),
        ] {
            assert_eq!(source_kind(&[format]), expected);
        }
        let mut source = test_source(
            FileFormat::Json,
            "tsconfig.json",
            "{\"compilerOptions\": {}}",
        );
        source.formats.push(FileFormat::TypeScript);
        assert!(!is_code_source(&source.formats));
        let fragments = extract(&source, ChunkOptions::default()).expect("data extraction");
        assert_eq!(fragments.len(), 1);
        assert_eq!(test_content(&fragments[0]), Content::Text(source.text));
        assert!(test_metadata(&fragments[0]).is_none());
    }

    #[test]
    fn rejects_invalid_format_sets_without_requiring_file_records() {
        let mut source = test_source(FileFormat::Text, "fixture.txt", "source text");
        for formats in [
            vec![],
            vec![FileFormat::Rust, FileFormat::Rust],
            vec![FileFormat::Markdown, FileFormat::Unknown],
        ] {
            source.formats = formats;
            assert!(extract(&source, ChunkOptions::default()).is_err());
        }
    }

    /// The previous implementation, kept verbatim as the byte-equality oracle.
    fn legacy_vector_contents(
        content: &Content,
        metadata: Option<&EntityMetadata>,
        max_chars: Option<usize>,
    ) -> Vec<Content> {
        let mut contents = vec![content.clone()];
        let metadata = vector_metadata_text(metadata, metadata_budget(max_chars));
        if !metadata.is_empty() {
            if let Some(Content::Text(text) | Content::Code(text)) = contents.first_mut() {
                *text = format!("{metadata}\n{text}");
            } else {
                contents.insert(0, Content::Text(metadata));
            }
        }
        contents
    }

    #[test]
    fn embedding_inputs_are_byte_identical_for_borrowed_and_owned_content() {
        use crate::domain::{CodeMetadata, Content, ImageContent, Language, MarkdownMetadata};

        let code = EntityMetadata::Code(CodeMetadata {
            symbol_type: Some(crate::domain::SymbolType::Function),
            symbol_name: Some("RunAsync".into()),
            scope: Some("Bench.N0::Type0".into()),
            signature: Some("public async Task RunAsync(string 名前)".into()),
            documentation: Some("/// Doc với 😀 and \"quotes\"\\".into()),
            visibility: None,
            language: Some(Language::CSharp),
        });
        let markdown = EntityMetadata::Markdown(MarkdownMetadata {
            heading: Some("Heading 😀".into()),
            level: Some(2),
            scope: Some("Parent::Child".into()),
        });
        let image = Content::Image(
            ImageContent::new(vec![0x89, b'P', b'N', b'G'], FileFormat::Png).expect("image"),
        );
        let contents = [
            Content::Code("class A {\r\n  void M() {}\r\n}".into()),
            Content::Text("plain text\nsecond line".into()),
            image,
        ];
        let metadatas = [None, Some(&code), Some(&markdown)];
        for content in &contents {
            for metadata in metadatas {
                for max_chars in [None, Some(0), Some(24), Some(4_000)] {
                    let expected = legacy_vector_contents(content, metadata, max_chars);
                    let borrowed = vector_content_for_fragment(
                        Cow::Borrowed(content),
                        metadata,
                        max_chars,
                    );
                    assert_eq!(borrowed, expected, "borrowed mismatch: {content:?}");
                    let owned =
                        vector_content_for_fragment(Cow::Owned(content.clone()), metadata, max_chars);
                    assert_eq!(owned, expected, "owned mismatch: {content:?}");
                }
            }
        }
    }
}
