use std::io;

use sha2::{Digest, Sha256};

pub(crate) fn sha256_parts<'a>(parts: impl IntoIterator<Item = &'a [u8]>) -> [u8; 32] {
    let mut digest = Sha256::new();
    for part in parts {
        digest.update(part);
    }
    digest.finalize().into()
}

/// Feeds serialized bytes straight into SHA-256 without an intermediate buffer.
pub(crate) fn sha256_hex_with<E>(
    write: impl FnOnce(&mut Sha256Writer<'_>) -> Result<(), E>,
) -> Result<String, E> {
    let mut digest = Sha256::new();
    {
        let mut writer = Sha256Writer {
            digest: &mut digest,
        };
        write(&mut writer)?;
    }
    Ok(hex::encode(digest.finalize()))
}

/// `io::Write` adapter so serde can hash while it serializes.
pub(crate) struct Sha256Writer<'a> {
    digest: &'a mut Sha256,
}

impl io::Write for Sha256Writer<'_> {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        self.digest.update(buffer);
        Ok(buffer.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

/// Hashes `f32` values exactly as their little-endian FP16 bit patterns.
pub(crate) fn sha256_hex_fp16_le(values: &[f32]) -> String {
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 512];
    let mut used = 0;
    for value in values {
        let bits = half::f16::from_f32(*value).to_bits().to_le_bytes();
        buffer[used..used + 2].copy_from_slice(&bits);
        used += 2;
        if used == buffer.len() {
            digest.update(buffer);
            used = 0;
        }
    }
    if used > 0 {
        digest.update(&buffer[..used]);
    }
    hex::encode(digest.finalize())
}

pub(crate) fn sha256_hex(bytes: &[u8]) -> String {
    sha256_hex_parts([bytes])
}

pub(crate) fn sha256_hex_parts<'a>(parts: impl IntoIterator<Item = &'a [u8]>) -> String {
    hex::encode(sha256_parts(parts))
}

#[cfg(test)]
mod tests {
    use super::{sha256_hex, sha256_hex_fp16_le, sha256_hex_with};

    /// The buffered implementation, kept as the digest oracle.
    fn legacy_fp16_le(values: &[f32]) -> String {
        let mut bytes = Vec::with_capacity(values.len() * 2);
        for value in values {
            bytes.extend_from_slice(&half::f16::from_f32(*value).to_bits().to_le_bytes());
        }
        sha256_hex(&bytes)
    }

    #[test]
    fn streaming_digest_matches_the_buffered_json_digest() {
        let model = crate::domain::model::EmbeddingModelInfo::new(
            crate::domain::model::ModelInfo::new(
                "fixture",
                "fixture",
                [
                    crate::domain::ContentKind::Text,
                    crate::domain::ContentKind::Code,
                ],
                8,
            )
            .expect("model info"),
            3,
            crate::domain::model::EmbeddingMetric::Cosine,
            None,
            None,
        )
        .expect("embedding info");
        let inputs = [
            vec![crate::domain::Content::Text("plain".into())],
            vec![crate::domain::Content::Code(
                "class A { string s = \"quote\\ \\u{1}\"; }\n😀".into(),
            )],
            vec![
                crate::domain::Content::Text("symbol: function f".into()),
                crate::domain::Content::Code("f() {}".into()),
            ],
        ];
        for input in inputs {
            let tuple = (1_u32, &model, &input);
            let legacy = sha256_hex(&serde_json::to_vec(&tuple).expect("legacy bytes"));
            let streamed = sha256_hex_with(|writer| serde_json::to_writer(writer, &tuple))
                .expect("streamed digest");
            assert_eq!(streamed, legacy, "digest changed for {input:?}");
        }
    }

    #[test]
    fn streaming_fp16_digest_matches_the_buffered_digest() {
        let specials = [
            -0.0_f32,
            0.0,
            1.0,
            -1.0,
            1.0e-8,
            6.103_515_625e-5,
            65_504.0,
            f32::INFINITY,
            f32::NEG_INFINITY,
            0.333_333,
        ];
        // Cross the 512-byte stack buffer boundary and end on a partial write.
        let mut values = specials.to_vec();
        values.extend((0..600).map(|index| (index as f32) * 0.1));
        for length in [0, 1, 5, specials.len(), 255, 256, 257, values.len()] {
            let slice = &values[..length];
            assert_eq!(
                sha256_hex_fp16_le(slice),
                legacy_fp16_le(slice),
                "fp16 digest changed for {length} values"
            );
        }
        // Signed zero keeps its distinct FP16 bit pattern, so its digest differs.
        assert_eq!(sha256_hex_fp16_le(&[-0.0_f32]), legacy_fp16_le(&[-0.0_f32]));
        assert_ne!(
            sha256_hex_fp16_le(&[-0.0_f32]),
            sha256_hex_fp16_le(&[0.0_f32])
        );
    }

    #[test]
    fn matches_known_sha256_digests() {
        assert_eq!(
            sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
