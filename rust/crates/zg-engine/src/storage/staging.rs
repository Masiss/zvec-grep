//! Disk-backed bounded staging for large file replacements.
//!
//! Embedding outputs are appended to a bounded temporary file instead of
//! accumulating every vector in RAM. `IndexStore::replace_file_from_staged`
//! validates the complete staging file before applying it, then streams batches
//! into the native collections under the store writer lock. Staging is not a
//! cross-collection transaction: a process crash during apply is recovered by
//! the file's non-indexed status and a later retry.

use std::{
    collections::VecDeque,
    fs::{File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    time::{Duration, SystemTime},
};

use crate::{
    EngineError, EngineResult,
    domain::{Entity, EntityId, FileRecord, FragmentId},
    storage::types::IndexedFragment,
};

const STAGING_MAGIC: &[u8; 4] = b"ZGST";
const STAGING_ENTITIES_MAGIC: &[u8; 4] = b"ZGEN";
const STAGING_VERSION: u32 = 2;
const STAGING_PREFIX: &str = "zg-staging-";
const STAGING_MAX_ENTITY_RECORD_BYTES: usize = 64 * 1024 * 1024;
const STAGING_ENTITY_BATCH_BYTES: usize = 1024 * 1024;
pub(crate) const STAGING_MAX_BYTES: u64 = 512 * 1024 * 1024;
pub(crate) const STAGING_BATCH_ENTITIES: usize = 128;
pub(crate) const STAGING_BATCH_FRAGMENTS: usize = 128;

pub(crate) struct DiskStagedBatches {
    file: FileRecord,
    entities_path: PathBuf,
    path: PathBuf,
    entities: usize,
    fragments: usize,
    bytes: u64,
}

impl DiskStagedBatches {
    pub(crate) fn create(file: FileRecord, entities: Vec<Entity>) -> EngineResult<Self> {
        cleanup_stale_staging_best_effort();
        let mut path = std::env::temp_dir();
        path.push(format!(
            "{}{}-{}.bin",
            STAGING_PREFIX,
            std::process::id(),
            uuid_simple()
        ));
        let entities_path = path.with_extension("entities");
        write_stage_header(&path, STAGING_MAGIC)?;
        if let Err(error) = write_stage_header(&entities_path, STAGING_ENTITIES_MAGIC) {
            remove_file_best_effort(&path);
            return Err(error);
        }
        let mut staged = Self {
            file,
            entities_path,
            path,
            entities: 0,
            fragments: 0,
            bytes: 16,
        };
        // DiskStagedBatches::append rejects batches above STAGING_BATCH_ENTITIES,
        // so chunk large entity sets here and keep the staging write bounded.
        for chunk in entities.chunks(STAGING_BATCH_ENTITIES) {
            staged.append_entities(chunk.to_vec())?;
        }
        Ok(staged)
    }

    pub(crate) fn append_entities(&mut self, batch: Vec<Entity>) -> EngineResult<()> {
        if batch.is_empty() {
            return Ok(());
        }
        if batch.len() > STAGING_BATCH_ENTITIES {
            return Err(EngineError::invalid_argument(format!(
                "staging batch has {} entities; limit is {}",
                batch.len(),
                STAGING_BATCH_ENTITIES
            )));
        }

        let mut handle = OpenOptions::new()
            .append(true)
            .open(&self.entities_path)
            .map_err(|error| {
                EngineError::storage_failure(format!("open entity staging for append: {error}"))
            })?;
        for entity in &batch {
            let (payload, metadata) = super::entities::encode_staged_entity(entity)?;
            let record_bytes = 4_usize
                .saturating_add(payload.len())
                .saturating_add(1)
                .saturating_add(metadata.as_ref().map_or(0, |value| 4 + value.len()));
            let next = next_staging_size(self.bytes, record_bytes)?;
            write_bytes(&mut handle, payload.as_bytes())?;
            match metadata {
                Some(metadata) => {
                    handle.write_all(&[1]).map_err(|error| {
                        EngineError::storage_failure(format!("write staged metadata tag: {error}"))
                    })?;
                    write_bytes(&mut handle, metadata.as_bytes())?;
                }
                None => handle.write_all(&[0]).map_err(|error| {
                    EngineError::storage_failure(format!("write staged metadata tag: {error}"))
                })?,
            }
            self.entities = self.entities.saturating_add(1);
            self.bytes = next;
        }
        handle.flush().map_err(|error| {
            EngineError::storage_failure(format!("flush entity staging: {error}"))
        })?;
        Ok(())
    }

    pub(crate) fn append(&mut self, batch: Vec<IndexedFragment>) -> EngineResult<()> {
        if batch.is_empty() {
            return Ok(());
        }
        if batch.len() > STAGING_BATCH_FRAGMENTS {
            return Err(EngineError::invalid_argument(format!(
                "staging batch has {} fragments; limit is {}",
                batch.len(),
                STAGING_BATCH_FRAGMENTS
            )));
        }

        let encoded = encode_batch(&batch)?;
        let next = next_staging_size(self.bytes, encoded.len())?;

        let mut handle = OpenOptions::new()
            .append(true)
            .open(&self.path)
            .map_err(|error| {
                EngineError::storage_failure(format!("open staging for append: {error}"))
            })?;
        handle.write_all(&encoded).map_err(|error| {
            EngineError::storage_failure(format!("append staging batch: {error}"))
        })?;
        handle.flush().map_err(|error| {
            EngineError::storage_failure(format!("flush staging batch: {error}"))
        })?;
        drop(handle);

        self.fragments += batch.len();
        self.bytes = next;

        Ok(())
    }

    /// Visit canonical entities in bounded count/byte batches.
    pub(crate) fn for_each_entity_batch(
        &self,
        mut visit: impl FnMut(&[Entity]) -> EngineResult<()>,
    ) -> EngineResult<usize> {
        let mut handle = File::open(&self.entities_path).map_err(|error| {
            EngineError::storage_failure(format!("open entity staging for commit: {error}"))
        })?;
        read_stage_header(&mut handle, STAGING_ENTITIES_MAGIC, "entity")?;

        let mut total = 0_usize;
        let mut batch = Vec::with_capacity(STAGING_BATCH_ENTITIES);
        let mut batch_bytes = 0_usize;
        while let Some(payload) = read_optional_bytes(&mut handle, STAGING_MAX_ENTITY_RECORD_BYTES)?
        {
            let mut metadata_tag = [0_u8; 1];
            handle.read_exact(&mut metadata_tag).map_err(|error| {
                EngineError::storage_failure(format!("read staged entity metadata tag: {error}"))
            })?;
            let metadata = match metadata_tag[0] {
                0 => None,
                1 => Some(
                    String::from_utf8(read_bytes_limited(
                        &mut handle,
                        STAGING_MAX_ENTITY_RECORD_BYTES,
                    )?)
                    .map_err(|_| {
                        EngineError::storage_failure("invalid staged metadata encoding")
                    })?,
                ),
                _ => return Err(EngineError::storage_failure("invalid staged metadata tag")),
            };
            let payload_bytes = payload.len();
            let payload = String::from_utf8(payload)
                .map_err(|_| EngineError::storage_failure("invalid staged entity encoding"))?;
            let entity = super::entities::decode_staged_entity(&payload, metadata.as_deref())?;
            batch_bytes = batch_bytes.saturating_add(payload_bytes);
            batch.push(entity);
            total = total.saturating_add(1);
            if batch.len() >= STAGING_BATCH_ENTITIES || batch_bytes >= STAGING_ENTITY_BATCH_BYTES {
                visit(&batch)?;
                batch.clear();
                batch_bytes = 0;
            }
        }
        if !batch.is_empty() {
            visit(&batch)?;
        }
        if total != self.entities {
            return Err(EngineError::storage_failure(format!(
                "staging entity count mismatch: recorded={} read={total}",
                self.entities
            )));
        }
        Ok(total)
    }

    /// Visit one decoded projection batch at a time; the caller must not retain batches.
    #[cfg(test)]
    pub(crate) fn for_each_batch(
        &self,
        mut visit: impl FnMut(&[IndexedFragment]) -> EngineResult<()>,
    ) -> EngineResult<usize> {
        let mut handle = File::open(&self.path).map_err(|error| {
            EngineError::storage_failure(format!("open staging for commit: {error}"))
        })?;
        let mut magic = [0u8; 4];
        handle.read_exact(&mut magic).map_err(|error| {
            EngineError::storage_failure(format!("read staging header: {error}"))
        })?;
        if &magic != STAGING_MAGIC {
            return Err(EngineError::storage_failure("invalid staging file magic"));
        }

        let version = read_u32(&mut handle).map_err(|error| {
            EngineError::storage_failure(format!("read staging version: {error}"))
        })?;
        if version != STAGING_VERSION {
            return Err(EngineError::storage_failure(format!(
                "unsupported staging version {version}"
            )));
        }

        let mut total = 0_usize;
        loop {
            let mut count_buf = [0u8; 4];
            match handle.read(&mut count_buf[..1]) {
                Ok(0) => break,
                Ok(1) => {}
                Ok(_) => unreachable!("one-byte read returned more than one byte"),
                Err(error) => {
                    return Err(EngineError::storage_failure(format!(
                        "read staging batch: {error}"
                    )));
                }
            }
            handle.read_exact(&mut count_buf[1..]).map_err(|error| {
                EngineError::storage_failure(format!("read staging batch header: {error}"))
            })?;

            let count = u32::from_le_bytes(count_buf) as usize;
            if count == 0 || count > STAGING_BATCH_FRAGMENTS {
                return Err(EngineError::storage_failure(format!(
                    "invalid staging batch size {count}"
                )));
            }
            let mut batch = Vec::with_capacity(count);
            for _ in 0..count {
                batch.push(read_fragment(&mut handle)?);
            }
            visit(&batch)?;
            total = total.saturating_add(count);
        }

        if total != self.fragments {
            return Err(EngineError::storage_failure(format!(
                "staging fragment count mismatch: recorded={} read={total}",
                self.fragments
            )));
        }
        Ok(total)
    }

    pub(crate) fn file(&self) -> &FileRecord {
        &self.file
    }

    pub(crate) fn fragment_reader(&self) -> EngineResult<StagedFragmentReader> {
        let mut handle = File::open(&self.path).map_err(|error| {
            EngineError::storage_failure(format!("open staging for commit: {error}"))
        })?;
        read_stage_header(&mut handle, STAGING_MAGIC, "projection")?;
        Ok(StagedFragmentReader {
            handle,
            expected: self.fragments,
            loaded: 0,
            buffered: VecDeque::new(),
            eof: false,
        })
    }

    pub(crate) fn entity_count(&self) -> usize {
        self.entities
    }

    #[cfg(any(test, feature = "m4-research-integration"))]
    pub(crate) fn fragment_count(&self) -> usize {
        self.fragments
    }

    pub(crate) fn discard(self) -> EngineResult<()> {
        let vector_result = remove_file(&self.path);
        let entity_result = remove_file(&self.entities_path);
        vector_result.and(entity_result)
    }
}

impl Drop for DiskStagedBatches {
    fn drop(&mut self) {
        remove_file_best_effort(&self.path);
        remove_file_best_effort(&self.entities_path);
    }
}

pub(crate) struct StagedFragmentReader {
    handle: File,
    expected: usize,
    loaded: usize,
    buffered: VecDeque<IndexedFragment>,
    eof: bool,
}

impl StagedFragmentReader {
    /// Read no more than one bounded vector batch. Fewer entries means EOF.
    pub(crate) fn next_batch(&mut self, maximum: usize) -> EngineResult<Vec<IndexedFragment>> {
        if maximum == 0 || maximum > STAGING_BATCH_FRAGMENTS {
            return Err(EngineError::invalid_argument(format!(
                "staged projection batch limit must be between 1 and {STAGING_BATCH_FRAGMENTS}"
            )));
        }
        while self.buffered.len() < maximum && !self.eof {
            self.read_next_batch()?;
        }
        let count = maximum.min(self.buffered.len());
        Ok(self.buffered.drain(..count).collect())
    }

    pub(crate) fn finish(mut self) -> EngineResult<()> {
        if !self.buffered.is_empty() {
            return Err(EngineError::invalid_argument(
                "staging contains projections without canonical fragments",
            ));
        }
        while !self.eof {
            self.read_next_batch()?;
        }
        Ok(())
    }

    fn read_next_batch(&mut self) -> EngineResult<()> {
        let Some(count) = read_optional_u32(&mut self.handle)? else {
            self.eof = true;
            if self.loaded != self.expected {
                return Err(EngineError::storage_failure(format!(
                    "staging fragment count mismatch: recorded={} read={}",
                    self.expected, self.loaded
                )));
            }
            return Ok(());
        };
        let count = count as usize;
        if count == 0 || count > STAGING_BATCH_FRAGMENTS {
            return Err(EngineError::storage_failure(format!(
                "invalid staging batch size {count}"
            )));
        }
        for _ in 0..count {
            self.buffered.push_back(read_fragment(&mut self.handle)?);
        }
        self.loaded = self.loaded.saturating_add(count);
        Ok(())
    }
}

fn next_staging_size(current: u64, additional_bytes: usize) -> EngineResult<u64> {
    let additional = u64::try_from(additional_bytes).unwrap_or(u64::MAX);
    let next = current.saturating_add(additional);
    if next > STAGING_MAX_BYTES {
        return Err(EngineError::storage_failure(format!(
            "staging quota exceeded: {next} bytes > {STAGING_MAX_BYTES} bytes"
        )));
    }
    Ok(next)
}

fn write_stage_header(path: &Path, magic: &[u8; 4]) -> EngineResult<()> {
    let mut handle = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|error| {
            EngineError::storage_failure(format!("create staging file {}: {error}", path.display()))
        })?;
    let result = (|| {
        handle.write_all(magic).map_err(|error| {
            EngineError::storage_failure(format!("write staging header: {error}"))
        })?;
        write_u32(&mut handle, STAGING_VERSION).map_err(|error| {
            EngineError::storage_failure(format!("write staging version: {error}"))
        })?;
        handle
            .flush()
            .map_err(|error| EngineError::storage_failure(format!("flush staging header: {error}")))
    })();
    if result.is_err() {
        drop(handle);
        remove_file_best_effort(path);
    }
    result
}

fn read_stage_header(handle: &mut File, expected_magic: &[u8; 4], kind: &str) -> EngineResult<()> {
    let mut magic = [0_u8; 4];
    handle.read_exact(&mut magic).map_err(|error| {
        EngineError::storage_failure(format!("read {kind} staging header: {error}"))
    })?;
    if &magic != expected_magic {
        return Err(EngineError::storage_failure(format!(
            "invalid {kind} staging file magic"
        )));
    }
    let version = read_u32(handle)?;
    if version != STAGING_VERSION {
        return Err(EngineError::storage_failure(format!(
            "unsupported {kind} staging version {version}"
        )));
    }
    Ok(())
}

fn write_bytes(handle: &mut File, bytes: &[u8]) -> EngineResult<()> {
    let length = u32::try_from(bytes.len())
        .map_err(|_| EngineError::storage_failure("staging field length exceeds u32"))?;
    write_u32(handle, length)
        .map_err(|error| EngineError::storage_failure(format!("write staging length: {error}")))?;
    handle
        .write_all(bytes)
        .map_err(|error| EngineError::storage_failure(format!("write staging field: {error}")))
}

fn read_optional_bytes(handle: &mut File, maximum: usize) -> EngineResult<Option<Vec<u8>>> {
    let Some(length) = read_optional_u32(handle)? else {
        return Ok(None);
    };
    let length = length as usize;
    if length > maximum {
        return Err(EngineError::storage_failure(
            "staged entity record exceeds size limit",
        ));
    }
    let mut bytes = vec![0_u8; length];
    handle
        .read_exact(&mut bytes)
        .map_err(|error| EngineError::storage_failure(format!("read staging record: {error}")))?;
    Ok(Some(bytes))
}

fn read_optional_u32(handle: &mut File) -> EngineResult<Option<u32>> {
    let mut bytes = [0_u8; 4];
    match handle.read(&mut bytes[..1]) {
        Ok(0) => return Ok(None),
        Ok(1) => {}
        Ok(_) => unreachable!("one-byte read returned more than one byte"),
        Err(error) => {
            return Err(EngineError::storage_failure(format!(
                "read staging u32: {error}"
            )));
        }
    }
    handle
        .read_exact(&mut bytes[1..])
        .map_err(|error| EngineError::storage_failure(format!("read staging u32: {error}")))?;
    Ok(Some(u32::from_le_bytes(bytes)))
}

fn read_bytes_limited(handle: &mut File, maximum: usize) -> EngineResult<Vec<u8>> {
    let length = read_u32(handle)? as usize;
    if length > maximum {
        return Err(EngineError::storage_failure(
            "staging field exceeds size limit",
        ));
    }
    let mut bytes = vec![0_u8; length];
    handle
        .read_exact(&mut bytes)
        .map_err(|error| EngineError::storage_failure(format!("read staging bytes: {error}")))?;
    Ok(bytes)
}

fn remove_file(path: &Path) -> EngineResult<()> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(EngineError::storage_failure(format!(
            "remove staging file {}: {error}",
            path.display()
        ))),
    }
}

fn encode_batch(batch: &[IndexedFragment]) -> EngineResult<Vec<u8>> {
    let mut output = Vec::new();
    write_u32_to(&mut output, batch.len() as u32);
    for entry in batch {
        write_bytes_to(&mut output, entry.entity_id.as_str().as_bytes());
        write_bytes_to(&mut output, entry.fragment_id.as_str().as_bytes());
        write_bytes_to(&mut output, entry.model.as_bytes());
        match &entry.embedding_fingerprint {
            Some(fingerprint) => {
                write_u32_to(&mut output, 1);
                write_bytes_to(&mut output, fingerprint.as_bytes());
            }
            None => write_u32_to(&mut output, 0),
        }
        write_u32_to(&mut output, entry.vector.len() as u32);
        for value in &entry.vector {
            output.extend_from_slice(&value.to_le_bytes());
        }
        write_bytes_to(&mut output, entry.fts_text.as_bytes());
    }
    Ok(output)
}

fn read_fragment(handle: &mut File) -> EngineResult<IndexedFragment> {
    let entity_id = EntityId::from_string(
        String::from_utf8(read_bytes(handle)?)
            .map_err(|_| EngineError::storage_failure("invalid storage entity id encoding"))?,
    );

    let fragment_id = FragmentId::from_string(
        String::from_utf8(read_bytes(handle)?)
            .map_err(|_| EngineError::storage_failure("invalid storage fragment id encoding"))?,
    );

    let model = String::from_utf8(read_bytes(handle)?)
        .map_err(|_| EngineError::storage_failure("invalid storage model encoding"))?;

    let embedding_fingerprint =
        match read_u32(handle)? {
            0 => None,
            1 => Some(String::from_utf8(read_bytes(handle)?).map_err(|_| {
                EngineError::storage_failure("invalid embedding fingerprint encoding")
            })?),
            _ => {
                return Err(EngineError::storage_failure(
                    "invalid embedding fingerprint flag",
                ));
            }
        };

    let vector_len = read_u32(handle)? as usize;
    if vector_len > 20_000 {
        return Err(EngineError::storage_failure(
            "invalid storage vector length",
        ));
    }

    let mut vector = Vec::with_capacity(vector_len);
    for _ in 0..vector_len {
        let mut buf = [0u8; 4];
        handle.read_exact(&mut buf).map_err(|error| {
            EngineError::storage_failure(format!("read storage vector: {error}"))
        })?;
        vector.push(f32::from_le_bytes(buf));
    }

    let fts_text = String::from_utf8(read_bytes(handle)?)
        .map_err(|_| EngineError::storage_failure("invalid storage text encoding"))?;

    Ok(IndexedFragment {
        entity_id,
        fragment_id,
        model,
        embedding_fingerprint,
        vector,
        fts_text,
    })
}

fn write_u32_to(output: &mut Vec<u8>, value: u32) {
    output.extend_from_slice(&value.to_le_bytes());
}

fn write_u32(handle: &mut File, value: u32) -> Result<(), std::io::Error> {
    handle.write_all(&value.to_le_bytes())
}

fn write_bytes_to(output: &mut Vec<u8>, bytes: &[u8]) {
    output.extend_from_slice(&(bytes.len() as u32).to_le_bytes());
    output.extend_from_slice(bytes);
}

fn read_u32(handle: &mut File) -> EngineResult<u32> {
    let mut buf = [0u8; 4];
    handle
        .read_exact(&mut buf)
        .map_err(|error| EngineError::storage_failure(format!("read storage u32: {error}")))?;
    Ok(u32::from_le_bytes(buf))
}

fn read_bytes(handle: &mut File) -> EngineResult<Vec<u8>> {
    let len = read_u32(handle)? as usize;
    if len > 16 * 1024 * 1024 {
        return Err(EngineError::storage_failure(
            "storage field exceeds size limit",
        ));
    }
    let mut buf = vec![0u8; len];
    handle
        .read_exact(&mut buf)
        .map_err(|error| EngineError::storage_failure(format!("read storage bytes: {error}")))?;
    Ok(buf)
}

fn uuid_simple() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    format!("{:x}{:x}", nanos, std::process::id())
}

fn remove_file_best_effort(path: &Path) {
    let _ = std::fs::remove_file(path);
}

fn cleanup_stale_staging_best_effort() {
    let dir = std::env::temp_dir();
    let Ok(entries) = std::fs::read_dir(&dir) else {
        return;
    };
    let cutoff = SystemTime::now()
        .checked_sub(Duration::from_hours(24))
        .unwrap_or(SystemTime::UNIX_EPOCH);
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        if !name.starts_with(STAGING_PREFIX) {
            continue;
        }
        let Ok(metadata) = entry.metadata() else {
            continue;
        };
        let Ok(modified) = metadata.modified() else {
            continue;
        };
        if modified < cutoff {
            let _ = std::fs::remove_file(entry.path());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::{Content, EntityFragment, FileId, Range};

    fn entity(text: &str) -> Entity {
        let file_id = FileId::new(1);
        let content = Content::Text(text.to_owned());
        let id = EntityId::new(file_id, &content, Range::Full).expect("entity id");
        Entity {
            id: id.clone(),
            file_id,
            source_range: Range::Full,
            content,
            metadata: None,
            fragments: vec![EntityFragment {
                id: FragmentId::new(&id, 0),
                range: Range::Full,
            }],
        }
    }

    fn entry(text: &str) -> IndexedFragment {
        IndexedFragment {
            entity_id: EntityId::from_string("entity".to_owned()),
            fragment_id: FragmentId::from_string("fragment".to_owned()),
            model: "test/model".to_owned(),
            embedding_fingerprint: None,
            vector: vec![1.0, 0.0],
            fts_text: text.to_owned(),
        }
    }

    #[test]
    fn staging_quota_rejects_bytes_above_the_limit() {
        assert_eq!(
            next_staging_size(STAGING_MAX_BYTES - 8, 8).expect("at quota"),
            STAGING_MAX_BYTES
        );
        let error = next_staging_size(STAGING_MAX_BYTES - 8, 9).expect_err("over quota");
        assert!(error.to_string().contains("staging quota exceeded"));
    }

    #[test]
    fn round_trip_disk_staged_batches() {
        let record = FakeRecord;
        let mut staged = DiskStagedBatches::create(record.file(), Vec::new()).expect("create");
        staged
            .append(vec![entry("hello"), entry("world")])
            .expect("append");
        let mut batches = 0;
        let count = staged
            .for_each_batch(|batch| {
                batches += 1;
                assert_eq!(batch.len(), 2);
                Ok(())
            })
            .expect("read batches");
        assert_eq!(batches, 1);
        assert_eq!(count, 2);
        let path = staged.path.clone();
        let entities_path = staged.entities_path.clone();
        staged.discard().expect("discard");
        assert!(!path.exists());
        assert!(!entities_path.exists());
    }

    #[test]
    fn staged_entities_round_trip_in_bounded_batches() {
        let mut staged = DiskStagedBatches::create(FakeRecord.file(), Vec::new()).expect("create");
        staged
            .append_entities(vec![entity("hello"), entity("world")])
            .expect("append entities");
        let mut seen = Vec::new();
        let count = staged
            .for_each_entity_batch(|batch| {
                seen.extend(batch.iter().map(|item| item.content.clone()));
                Ok(())
            })
            .expect("read entities");
        assert_eq!(count, 2);
        assert_eq!(
            seen,
            vec![Content::Text("hello".into()), Content::Text("world".into())]
        );
    }

    #[test]
    fn staging_create_accepts_more_than_one_batch_of_entities() {
        // A single prepared file used to overflow the 128-entity staged batch
        // limit at DiskStagedBatches::create before the create-side chunking
        // fix. Reproduces the production STORAGE_FAILURE ("create staging:
        // staging batch has 260 entities; limit is 128") when 260 entities go
        // through the staged (large-file) path at once.
        let entities: Vec<Entity> = (0..260)
            .map(|index| entity(&format!("entity-{index}")))
            .collect();
        let staged = DiskStagedBatches::create(FakeRecord.file(), entities).expect("create");
        assert_eq!(staged.entity_count(), 260);
        let mut seen = Vec::new();
        let count = staged
            .for_each_entity_batch(|batch| {
                for item in batch {
                    if let Content::Text(text) = &item.content {
                        seen.push(text.clone());
                    }
                }
                Ok(())
            })
            .expect("read entities");
        assert_eq!(count, 260);
        assert_eq!(seen.len(), 260);
        staged.discard().expect("discard");
    }

    #[test]
    fn drop_cleans_unpublished_staging() {
        let mut staged =
            DiskStagedBatches::create(FakeRecord.file(), vec![entity("partial")]).expect("create");
        staged.append(vec![entry("partial")]).expect("append");
        let path = staged.path.clone();
        let entities_path = staged.entities_path.clone();
        drop(staged);
        assert!(!path.exists());
        assert!(!entities_path.exists());
    }

    #[test]
    fn truncated_batch_header_is_reported_as_corruption() {
        let mut staged = DiskStagedBatches::create(FakeRecord.file(), Vec::new()).expect("create");
        staged.append(vec![entry("partial")]).expect("append");
        let path = staged.path.clone();
        OpenOptions::new()
            .write(true)
            .open(&path)
            .expect("open staged file")
            .set_len(9)
            .expect("truncate staged header");
        assert!(staged.for_each_batch(|_| Ok(())).is_err());
        drop(staged);
        assert!(!path.exists());
    }

    #[allow(dead_code)]
    struct FakeRecord;

    impl FakeRecord {
        fn file(&self) -> FileRecord {
            FileRecord {
                id: FileId::new(1),
                relative_path: crate::domain::SourcePath::new("src/test.rs").expect("path"),
                snapshot: crate::domain::FileSnapshot {
                    size_bytes: 0,
                    modified_epoch_ms: None,
                    content_hash: None,
                },
                index_status: crate::domain::FileIndexStatus::NotIndexed,
            }
        }
    }
}
