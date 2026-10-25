//! Research-only prototype for versioned base + searchable delta retrieval.
//!
//! This is deliberately an integration test rather than production storage code.
//! It fixes the retrieval invariants and crash protocol before any migration is
//! considered. The prototype models the current fragment identity separately
//! from logical document identity, because an update must hide an older base hit
//! even when the old vector remains physically present until compaction.

use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, OpenOptions},
    io::{self, BufRead, BufReader, Write},
    path::{Path, PathBuf},
    sync::{Mutex, MutexGuard},
};

use serde::{Deserialize, Serialize};
use tempfile::TempDir;

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Document {
    logical_id: String,
    fragment_id: String,
    generation: u64,
    deleted: bool,
    vector_score: f64,
    bm25_score: f64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Segment {
    name: String,
    documents: Vec<Document>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
struct Manifest {
    generation: u64,
    base: String,
    deltas: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
enum WalRecord {
    DeltaPublished { segment: String, generation: u64 },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum CrashPoint {
    AfterSegmentFileSyncBeforeRename,
    AfterSegmentRename,
    AfterWalPayloadBeforeNewline,
    AfterWalAppendBeforeSync,
    AfterWalSync,
    BeforeManifestRename,
    AfterManifestRename,
}

fn inject_crash(point: Option<CrashPoint>, at: CrashPoint) -> io::Result<()> {
    if point == Some(at) {
        Err(io::Error::new(
            io::ErrorKind::Interrupted,
            format!("injected crash at {at:?}"),
        ))
    } else {
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq)]
struct Hit {
    logical_id: String,
    fragment_id: String,
    generation: u64,
    score: f64,
}

#[derive(Debug)]
struct SearchIndex {
    manifest: Manifest,
    segments: BTreeMap<String, Segment>,
}

impl SearchIndex {
    fn segments_in_order(&self) -> impl Iterator<Item = &Segment> {
        std::iter::once(&self.manifest.base)
            .chain(self.manifest.deltas.iter())
            .filter_map(|name| self.segments.get(name))
    }

    fn latest_documents(&self) -> BTreeMap<&str, &Document> {
        let mut latest = BTreeMap::new();
        for segment in self.segments_in_order() {
            for document in &segment.documents {
                let replace = latest
                    .get(document.logical_id.as_str())
                    .is_none_or(|current: &&Document| current.generation <= document.generation);
                if replace {
                    latest.insert(document.logical_id.as_str(), document);
                }
            }
        }
        latest
    }

    /// Search every segment with a refill loop. A stale top candidate cannot
    /// consume one of K merely because it was returned before the tombstone.
    fn search(
        &self,
        vector_weight: f64,
        lexical_weight: f64,
        limit: usize,
        initial_probe: usize,
    ) -> Vec<Hit> {
        if limit == 0 {
            return Vec::new();
        }
        let latest = self.latest_documents();
        // Segment-local ranges let a one-row delta look perfect regardless of its absolute score.
        let live_documents = latest
            .values()
            .copied()
            .filter(|document| !document.deleted)
            .collect::<Vec<_>>();
        let vector_range =
            ScoreRange::from_values(live_documents.iter().map(|document| document.vector_score));
        let lexical_range =
            ScoreRange::from_values(live_documents.iter().map(|document| document.bm25_score));
        let mut hits = Vec::new();
        for segment in self.segments_in_order() {
            hits.extend(segment_candidates(
                segment,
                &latest,
                vector_weight,
                lexical_weight,
                vector_range,
                lexical_range,
                limit,
                initial_probe,
            ));
        }

        // A logical document can occur in both base and delta. Generation
        // filtering above removes older copies; this final set is defensive for
        // malformed/equal-generation input and makes the invariant explicit.
        let mut seen = BTreeSet::new();
        hits.retain(|hit| seen.insert(hit.logical_id.clone()));
        hits.sort_by(|left, right| {
            right
                .score
                .total_cmp(&left.score)
                .then_with(|| right.generation.cmp(&left.generation))
                .then_with(|| left.logical_id.cmp(&right.logical_id))
        });
        hits.truncate(limit);
        hits
    }

    fn compact(&self) -> io::Result<Segment> {
        let generation = self
            .manifest
            .generation
            .checked_add(1)
            .ok_or_else(|| io::Error::other("manifest generation overflow"))?;
        let mut documents = self
            .latest_documents()
            .into_values()
            .filter(|document| !document.deleted)
            .cloned()
            .collect::<Vec<_>>();
        documents.sort_by(|left, right| left.logical_id.cmp(&right.logical_id));
        Ok(Segment {
            name: format!("base-{generation}"),
            documents,
        })
    }
}

fn segment_candidates(
    segment: &Segment,
    latest: &BTreeMap<&str, &Document>,
    vector_weight: f64,
    lexical_weight: f64,
    vector_range: ScoreRange,
    lexical_range: ScoreRange,
    limit: usize,
    initial_probe: usize,
) -> Vec<Hit> {
    let ranked = ranked_documents(
        segment,
        vector_weight,
        lexical_weight,
        vector_range,
        lexical_range,
    );
    if ranked.is_empty() {
        return Vec::new();
    }

    let mut probe = initial_probe.max(1).min(ranked.len());
    loop {
        let visible = ranked
            .iter()
            .take(probe)
            .filter_map(|(document, score)| {
                let current = latest.get(document.logical_id.as_str())?;
                if !std::ptr::eq(*current, *document) || document.deleted {
                    return None;
                }
                Some(Hit {
                    logical_id: document.logical_id.clone(),
                    fragment_id: document.fragment_id.clone(),
                    generation: document.generation,
                    score: *score,
                })
            })
            .take(limit)
            .collect::<Vec<_>>();
        if visible.len() >= limit || probe == ranked.len() {
            return visible;
        }
        probe = probe.saturating_mul(2).max(probe + 1).min(ranked.len());
    }
}

fn ranked_documents(
    segment: &Segment,
    vector_weight: f64,
    lexical_weight: f64,
    vector_range: ScoreRange,
    lexical_range: ScoreRange,
) -> Vec<(&Document, f64)> {
    let mut ranked = segment
        .documents
        .iter()
        .map(|document| {
            let vector = vector_range.normalize(document.vector_score);
            let lexical = lexical_range.normalize(document.bm25_score);
            (document, vector_weight * vector + lexical_weight * lexical)
        })
        .collect::<Vec<_>>();
    ranked.sort_by(|left, right| {
        right
            .1
            .total_cmp(&left.1)
            .then_with(|| left.0.logical_id.cmp(&right.0.logical_id))
    });
    ranked
}

#[derive(Clone, Copy)]
struct ScoreRange {
    min: f64,
    max: f64,
}

impl ScoreRange {
    fn from_values(values: impl IntoIterator<Item = f64>) -> Self {
        let (min, max) = values
            .into_iter()
            .fold((f64::INFINITY, f64::NEG_INFINITY), |(min, max), value| {
                (min.min(value), max.max(value))
            });
        Self { min, max }
    }

    fn normalize(self, value: f64) -> f64 {
        let range = self.max - self.min;
        if !range.is_finite() || range.abs() < f64::EPSILON {
            if value.abs() < f64::EPSILON { 0.0 } else { 1.0 }
        } else {
            (value - self.min) / range
        }
    }
}

struct Repository {
    root: PathBuf,
    writer: Mutex<()>,
}

impl Repository {
    fn new(root: &Path) -> io::Result<Self> {
        fs::create_dir_all(root.join("segments"))?;
        Ok(Self {
            root: root.to_path_buf(),
            writer: Mutex::new(()),
        })
    }

    fn lock_writer(&self) -> io::Result<MutexGuard<'_, ()>> {
        self.writer
            .lock()
            .map_err(|_| io::Error::other("repository writer lock poisoned"))
    }

    fn manifest_path(&self) -> PathBuf {
        self.root.join("manifest.json")
    }

    fn wal_path(&self) -> PathBuf {
        self.root.join("wal.jsonl")
    }

    fn segment_path(&self, name: &str) -> PathBuf {
        self.root.join("segments").join(format!("{name}.json"))
    }

    fn write_segment(&self, segment: &Segment, crash: Option<CrashPoint>) -> io::Result<()> {
        let temporary = self.segment_path(&segment.name).with_extension("json.tmp");
        let bytes = serde_json::to_vec_pretty(segment).map_err(io::Error::other)?;
        let mut file = fs::File::create(&temporary)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        inject_crash(crash, CrashPoint::AfterSegmentFileSyncBeforeRename)?;
        fs::rename(temporary, self.segment_path(&segment.name))?;
        #[cfg(unix)]
        fs::File::open(self.root.join("segments"))?.sync_all()?;
        inject_crash(crash, CrashPoint::AfterSegmentRename)?;
        Ok(())
    }

    fn append_wal(&self, record: &WalRecord, crash: Option<CrashPoint>) -> io::Result<()> {
        let path = self.wal_path();
        let created = !path.exists();
        let mut file = OpenOptions::new().create(true).append(true).open(&path)?;
        let original_len = file.metadata()?.len();
        serde_json::to_writer(&mut file, record).map_err(io::Error::other)?;
        inject_crash(crash, CrashPoint::AfterWalPayloadBeforeNewline)?;
        file.write_all(b"\n")?;
        if crash == Some(CrashPoint::AfterWalAppendBeforeSync) {
            file.set_len(original_len)?;
            file.sync_all()?;
            return inject_crash(crash, CrashPoint::AfterWalAppendBeforeSync);
        }
        file.sync_all()?;
        #[cfg(unix)]
        if created {
            fs::File::open(&self.root)?.sync_all()?;
        }
        inject_crash(crash, CrashPoint::AfterWalSync)
    }

    fn publish_manifest(&self, manifest: &Manifest, crash: Option<CrashPoint>) -> io::Result<()> {
        for name in std::iter::once(&manifest.base).chain(manifest.deltas.iter()) {
            if !self.segment_path(name).is_file() {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    format!("manifest references missing segment {name}"),
                ));
            }
        }
        let temporary = self.manifest_path().with_extension("json.tmp");
        let bytes = serde_json::to_vec_pretty(manifest).map_err(io::Error::other)?;
        let mut file = fs::File::create(&temporary)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        inject_crash(crash, CrashPoint::BeforeManifestRename)?;
        fs::rename(temporary, self.manifest_path())?;
        inject_crash(crash, CrashPoint::AfterManifestRename)?;
        #[cfg(unix)]
        fs::File::open(&self.root)?.sync_all()?;
        Ok(())
    }

    fn initialize(&self, base: Segment) -> io::Result<()> {
        let _writer = self.lock_writer()?;
        self.write_segment(&base, None)?;
        self.publish_manifest(
            &Manifest {
                generation: base
                    .documents
                    .iter()
                    .map(|document| document.generation)
                    .max()
                    .unwrap_or(0),
                base: base.name,
                deltas: Vec::new(),
            },
            None,
        )
    }

    fn load_manifest(&self) -> io::Result<Manifest> {
        serde_json::from_slice(&fs::read(self.manifest_path())?).map_err(io::Error::other)
    }

    fn load_segment(&self, name: &str) -> io::Result<Segment> {
        serde_json::from_slice(&fs::read(self.segment_path(name))?).map_err(io::Error::other)
    }

    fn install_delta(&self, delta: Segment, leave_manifest_unpublished: bool) -> io::Result<()> {
        self.install_delta_inner(delta, !leave_manifest_unpublished, None)
    }

    fn install_delta_with_crash(
        &self,
        delta: Segment,
        crash: Option<CrashPoint>,
    ) -> io::Result<()> {
        self.install_delta_inner(delta, true, crash)
    }

    fn install_delta_inner(
        &self,
        delta: Segment,
        publish_manifest: bool,
        crash: Option<CrashPoint>,
    ) -> io::Result<()> {
        let _writer = self.lock_writer()?;
        let mut manifest = self.recover_locked()?.manifest;
        let generation = manifest
            .generation
            .checked_add(1)
            .ok_or_else(|| io::Error::other("manifest generation overflow"))?;
        self.write_segment(&delta, crash)?;
        self.append_wal(
            &WalRecord::DeltaPublished {
                segment: delta.name.clone(),
                generation,
            },
            crash,
        )?;
        manifest.generation = generation;
        manifest.deltas.push(delta.name);
        if publish_manifest {
            self.publish_manifest(&manifest, crash)?;
        }
        Ok(())
    }

    fn recover(&self) -> io::Result<SearchIndex> {
        let _writer = self.lock_writer()?;
        self.recover_locked()
    }

    fn recover_locked(&self) -> io::Result<SearchIndex> {
        let mut manifest = self.load_manifest()?;
        let manifest_generation = manifest.generation;
        let wal_path = self.wal_path();
        let file = match OpenOptions::new().read(true).write(true).open(&wal_path) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                self.publish_manifest(&manifest, None)?;
                return self.load_search_index(manifest);
            }
            Err(error) => return Err(error),
        };
        let mut file = file;
        let mut valid_bytes = 0_u64;
        let mut has_torn_tail = false;
        {
            let mut reader = BufReader::new(&mut file);
            let mut line = Vec::new();
            loop {
                line.clear();
                let bytes_read = reader.read_until(b'\n', &mut line)?;
                if bytes_read == 0 {
                    break;
                }
                if line.last() != Some(&b'\n') {
                    has_torn_tail = true;
                    break;
                }
                valid_bytes += bytes_read as u64;
                line.pop();
                if line.last() == Some(&b'\r') {
                    line.pop();
                }
                if line.iter().all(u8::is_ascii_whitespace) {
                    continue;
                }
                let WalRecord::DeltaPublished {
                    segment,
                    generation,
                } = serde_json::from_slice(&line)
                    .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
                if generation <= manifest_generation {
                    continue;
                }
                let expected_generation = manifest
                    .generation
                    .checked_add(1)
                    .ok_or_else(|| io::Error::other("manifest generation overflow"))?;
                if generation < expected_generation {
                    if manifest.deltas.contains(&segment) {
                        continue;
                    }
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "WAL reuses a manifest generation for a different segment",
                    ));
                }
                if generation != expected_generation {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "WAL manifest generation is not contiguous",
                    ));
                }
                if !manifest.deltas.contains(&segment) {
                    manifest.deltas.push(segment);
                }
                manifest.generation = generation;
            }
        }
        if has_torn_tail {
            file.set_len(valid_bytes)?;
            file.sync_all()?;
        }
        self.publish_manifest(&manifest, None)?;
        self.load_search_index(manifest)
    }

    fn load_search_index(&self, manifest: Manifest) -> io::Result<SearchIndex> {
        let mut names = vec![manifest.base.clone()];
        names.extend(manifest.deltas.iter().cloned());
        let segments = names
            .into_iter()
            .map(|name| self.load_segment(&name).map(|segment| (name, segment)))
            .collect::<io::Result<BTreeMap<_, _>>>()?;
        Ok(SearchIndex { manifest, segments })
    }

    fn compact(&self) -> io::Result<()> {
        let _writer = self.lock_writer()?;
        let index = self.recover_locked()?;
        let generation = index
            .manifest
            .generation
            .checked_add(1)
            .ok_or_else(|| io::Error::other("manifest generation overflow"))?;
        let base = index.compact()?;
        self.write_segment(&base, None)?;
        self.publish_manifest(
            &Manifest {
                generation,
                base: base.name,
                deltas: Vec::new(),
            },
            None,
        )
    }
}

fn document(
    logical_id: &str,
    fragment_id: &str,
    generation: u64,
    deleted: bool,
    vector_score: f64,
    bm25_score: f64,
) -> Document {
    Document {
        logical_id: logical_id.into(),
        fragment_id: fragment_id.into(),
        generation,
        deleted,
        vector_score,
        bm25_score,
    }
}

fn segment(name: &str, documents: impl IntoIterator<Item = Document>) -> Segment {
    Segment {
        name: name.into(),
        documents: documents.into_iter().collect(),
    }
}

#[test]
fn small_update_is_searchable_before_base_compaction_and_hides_old_fragment()
-> Result<(), Box<dyn std::error::Error>> {
    let temporary = TempDir::new()?;
    let repository = Repository::new(temporary.path())?;
    repository.initialize(segment(
        "base-1",
        [
            document("file-a", "fragment-a-old", 1, false, 0.8, 0.1),
            document("file-b", "fragment-b", 1, false, 0.7, 0.2),
        ],
    ))?;
    repository.install_delta(
        segment(
            "delta-2",
            [document("file-a", "fragment-a-new", 2, false, 0.95, 0.9)],
        ),
        false,
    )?;

    let index = repository.recover()?;
    assert_eq!(index.manifest.deltas, ["delta-2"]);
    let hits = index.search(1.0, 0.0, 2, 1);
    assert_eq!(hits[0].fragment_id, "fragment-a-new");
    assert!(!hits.iter().any(|hit| hit.fragment_id == "fragment-a-old"));
    Ok(())
}

#[test]
fn tombstones_trigger_candidate_refill_and_compaction_removes_stale_rows()
-> Result<(), Box<dyn std::error::Error>> {
    let temporary = TempDir::new()?;
    let repository = Repository::new(temporary.path())?;
    repository.initialize(segment(
        "base-1",
        [
            document("file-a", "fragment-a-old", 1, false, 1.0, 0.0),
            document("file-b", "fragment-b", 1, false, 0.4, 0.0),
        ],
    ))?;
    repository.install_delta(
        segment(
            "delta-2",
            [document("file-a", "tombstone-a", 2, true, 0.0, 0.0)],
        ),
        false,
    )?;

    let index = repository.recover()?;
    let hits = index.search(1.0, 0.0, 1, 1);
    assert_eq!(hits[0].logical_id, "file-b");
    assert!(!hits.iter().any(|hit| hit.logical_id == "file-a"));

    repository.compact()?;
    let compacted = repository.recover()?;
    assert!(compacted.manifest.deltas.is_empty());
    assert_eq!(
        compacted.segments[&compacted.manifest.base].documents.len(),
        1
    );
    assert_eq!(compacted.search(1.0, 0.0, 2, 1)[0].logical_id, "file-b");
    Ok(())
}

#[test]
fn hybrid_search_normalizes_modalities_before_weighted_merge()
-> Result<(), Box<dyn std::error::Error>> {
    let temporary = TempDir::new()?;
    let repository = Repository::new(temporary.path())?;
    repository.initialize(segment(
        "base-1",
        [
            document("vector-winner", "vector", 1, false, 0.9, 1.0),
            document("lexical-winner", "lexical", 1, false, 0.1, 100.0),
        ],
    ))?;
    let index = repository.recover()?;

    assert_eq!(index.search(1.0, 0.0, 1, 2)[0].logical_id, "vector-winner");
    assert_eq!(index.search(0.0, 1.0, 1, 2)[0].logical_id, "lexical-winner");
    Ok(())
}

#[test]
fn hybrid_normalization_is_global_across_base_and_delta() -> Result<(), Box<dyn std::error::Error>>
{
    let temporary = TempDir::new()?;
    let repository = Repository::new(temporary.path())?;
    repository.initialize(segment(
        "base-1",
        [
            document("strong-base", "base-strong", 1, false, 0.9, 90.0),
            document("weaker-base", "base-weaker", 1, false, 0.8, 80.0),
        ],
    ))?;
    repository.install_delta(
        segment(
            "delta-2",
            [document("weak-delta", "delta-weak", 2, false, 0.1, 10.0)],
        ),
        false,
    )?;

    let index = repository.recover()?;
    let hits = index.search(0.5, 0.5, 1, 1);
    assert_eq!(hits[0].logical_id, "strong-base");
    Ok(())
}

#[test]
fn latest_equal_generation_duplicate_and_delete_wins_after_recovery()
-> Result<(), Box<dyn std::error::Error>> {
    let temporary = TempDir::new()?;
    let repository = Repository::new(temporary.path())?;
    repository.initialize(segment(
        "base-1",
        [
            document("same", "base-version", 4, false, 0.1, 0.1),
            document("deleted", "base-live", 4, false, 0.2, 0.2),
        ],
    ))?;
    repository.install_delta(
        segment(
            "delta-2",
            [
                document("same", "batch-first", 5, false, 0.3, 0.3),
                document("same", "batch-last", 5, false, 0.4, 0.4),
                document("deleted", "delta-live", 5, false, 0.5, 0.5),
            ],
        ),
        false,
    )?;
    repository.install_delta(
        segment(
            "delta-3",
            [
                document("same", "later-mutation", 5, false, 0.6, 0.6),
                document("deleted", "delete-tombstone", 5, true, 0.9, 0.9),
            ],
        ),
        true,
    )?;

    let recovered = repository.recover()?;
    assert_eq!(recovered.manifest.deltas, ["delta-2", "delta-3"]);
    let hits = recovered.search(1.0, 0.0, 10, 1);
    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0].logical_id, "same");
    assert_eq!(hits[0].fragment_id, "later-mutation");
    let reopened = repository.recover()?;
    assert_eq!(reopened.search(1.0, 0.0, 10, 1), hits);

    repository.compact()?;
    let compacted = repository.recover()?;
    assert_eq!(compacted.manifest.generation, 7);
    assert!(compacted.manifest.deltas.is_empty());
    repository.install_delta(
        segment(
            "delta-after-compact",
            [document("same", "after-compact", 5, false, 0.8, 0.8)],
        ),
        false,
    )?;
    let after_compaction_write = repository.recover()?;
    assert_eq!(after_compaction_write.manifest.generation, 8);
    assert_eq!(
        after_compaction_write.search(1.0, 0.0, 1, 1)[0].fragment_id,
        "after-compact"
    );
    Ok(())
}

#[test]
fn concurrent_delta_publication_serializes_manifest_generations()
-> Result<(), Box<dyn std::error::Error>> {
    let temporary = TempDir::new()?;
    let repository = std::sync::Arc::new(Repository::new(temporary.path())?);
    repository.initialize(segment(
        "base-1",
        [document("base", "base", 1, false, 0.5, 0.5)],
    ))?;
    let start = std::sync::Arc::new(std::sync::Barrier::new(4));
    let mut writers = Vec::new();
    for worker in 0..4 {
        let repository = std::sync::Arc::clone(&repository);
        let start = std::sync::Arc::clone(&start);
        writers.push(std::thread::spawn(move || -> io::Result<()> {
            start.wait();
            for mutation in 0..3 {
                let logical_id = format!("worker-{worker}-{mutation}");
                repository.install_delta(
                    segment(
                        &format!("delta-{worker}-{mutation}"),
                        [document(&logical_id, &logical_id, 2, false, 0.5, 0.5)],
                    ),
                    false,
                )?;
            }
            Ok(())
        }));
    }
    for writer in writers {
        writer
            .join()
            .map_err(|_| io::Error::other("concurrent writer panicked"))??;
    }

    let recovered = repository.recover()?;
    assert_eq!(recovered.manifest.generation, 13);
    assert_eq!(recovered.manifest.deltas.len(), 12);
    assert_eq!(recovered.search(1.0, 0.0, 20, 1).len(), 13);
    Ok(())
}

#[test]
fn wal_recovery_uses_manifest_sequence_not_document_generation()
-> Result<(), Box<dyn std::error::Error>> {
    let temporary = TempDir::new()?;
    let repository = Repository::new(temporary.path())?;
    repository.initialize(segment(
        "base-1",
        [document("base", "base", 1, false, 0.1, 0.1)],
    ))?;
    repository.install_delta(
        segment("delta-2", [document("first", "first", 2, false, 0.5, 0.5)]),
        false,
    )?;
    repository.install_delta(
        segment(
            "delta-3",
            [document("second", "second", 2, false, 0.9, 0.9)],
        ),
        true,
    )?;

    let recovered = repository.recover()?;
    assert_eq!(recovered.manifest.deltas, ["delta-2", "delta-3"]);
    assert!(
        recovered
            .search(1.0, 0.0, 3, 1)
            .iter()
            .any(|hit| hit.logical_id == "second")
    );
    Ok(())
}

#[test]
fn wal_recovery_keeps_valid_prefix_and_truncates_torn_tail()
-> Result<(), Box<dyn std::error::Error>> {
    let temporary = TempDir::new()?;
    let repository = Repository::new(temporary.path())?;
    repository.initialize(segment(
        "base-1",
        [document("base", "base", 1, false, 0.1, 0.1)],
    ))?;
    repository.install_delta(
        segment("delta-2", [document("first", "first", 2, false, 0.5, 0.5)]),
        false,
    )?;
    let mut wal = OpenOptions::new()
        .append(true)
        .open(repository.wal_path())?;
    wal.write_all(b"{")?;
    wal.sync_all()?;

    let recovered = repository.recover()?;
    assert_eq!(recovered.manifest.deltas, ["delta-2"]);
    repository.install_delta(
        segment(
            "delta-3",
            [document("second", "second", 3, false, 0.9, 0.9)],
        ),
        true,
    )?;
    let recovered = repository.recover()?;
    assert_eq!(recovered.manifest.deltas, ["delta-2", "delta-3"]);
    assert_eq!(recovered.search(1.0, 0.0, 3, 1).len(), 3);
    Ok(())
}

#[test]
fn wal_recovery_fails_closed_on_mid_log_corruption() -> Result<(), Box<dyn std::error::Error>> {
    let temporary = TempDir::new()?;
    let repository = Repository::new(temporary.path())?;
    repository.initialize(segment(
        "base-1",
        [document("base", "base", 1, false, 0.1, 0.1)],
    ))?;
    repository.install_delta(
        segment("delta-2", [document("first", "first", 2, false, 0.5, 0.5)]),
        false,
    )?;
    repository.write_segment(
        &segment("delta-3", [document("later", "later", 3, false, 0.9, 0.9)]),
        None,
    )?;
    let mut wal = OpenOptions::new()
        .append(true)
        .open(repository.wal_path())?;
    wal.write_all(b"X\n")?;
    serde_json::to_writer(
        &mut wal,
        &WalRecord::DeltaPublished {
            segment: "delta-3".into(),
            generation: 3,
        },
    )?;
    wal.write_all(b"\n")?;
    wal.sync_all()?;

    let previous = repository.load_manifest()?;
    let error = repository.recover().unwrap_err();
    assert_eq!(error.kind(), io::ErrorKind::InvalidData);
    assert_eq!(repository.load_manifest()?, previous);
    Ok(())
}

#[test]
fn wal_recovery_rejects_generation_gaps_and_conflicting_duplicates()
-> Result<(), Box<dyn std::error::Error>> {
    let scenarios = [
        (
            vec![WalRecord::DeltaPublished {
                segment: "delta-gap".into(),
                generation: 3,
            }],
            vec!["delta-gap"],
            true,
        ),
        (
            vec![
                WalRecord::DeltaPublished {
                    segment: "delta-2".into(),
                    generation: 2,
                },
                WalRecord::DeltaPublished {
                    segment: "delta-conflict".into(),
                    generation: 2,
                },
            ],
            vec!["delta-2", "delta-conflict"],
            true,
        ),
        (
            vec![
                WalRecord::DeltaPublished {
                    segment: "delta-2".into(),
                    generation: 2,
                },
                WalRecord::DeltaPublished {
                    segment: "delta-2".into(),
                    generation: 2,
                },
            ],
            vec!["delta-2"],
            false,
        ),
    ];
    for (records, segment_names, expect_error) in scenarios {
        let temporary = TempDir::new()?;
        let repository = Repository::new(temporary.path())?;
        repository.initialize(segment(
            "base-1",
            [document("base", "base", 1, false, 0.1, 0.1)],
        ))?;
        for name in segment_names {
            repository.write_segment(
                &segment(name, [document(name, name, 2, false, 0.5, 0.5)]),
                None,
            )?;
        }
        let mut wal = OpenOptions::new()
            .create(true)
            .append(true)
            .open(repository.wal_path())?;
        for record in records {
            serde_json::to_writer(&mut wal, &record)?;
            wal.write_all(b"\n")?;
        }
        wal.sync_all()?;

        let result = repository.recover();
        if expect_error {
            let error = result.expect_err("invalid sequence must fail closed");
            assert_eq!(error.kind(), io::ErrorKind::InvalidData);
            assert_eq!(repository.load_manifest()?.generation, 1);
        } else {
            let recovered = result?;
            assert_eq!(recovered.manifest.generation, 2);
            assert_eq!(recovered.manifest.deltas, ["delta-2"]);
        }
    }
    Ok(())
}

#[test]
fn crash_points_recover_exactly_the_durable_batch_prefix() -> Result<(), Box<dyn std::error::Error>>
{
    let crash_cases = [
        (CrashPoint::AfterSegmentFileSyncBeforeRename, false),
        (CrashPoint::AfterSegmentRename, false),
        (CrashPoint::AfterWalPayloadBeforeNewline, false),
        (CrashPoint::AfterWalAppendBeforeSync, false),
        (CrashPoint::AfterWalSync, true),
        (CrashPoint::BeforeManifestRename, true),
        (CrashPoint::AfterManifestRename, true),
    ];
    for (crash_point, expected_faulted_batch) in crash_cases {
        let temporary = TempDir::new()?;
        let repository = Repository::new(temporary.path())?;
        repository.initialize(segment(
            "base-1",
            [document("base", "base", 1, false, 0.1, 0.1)],
        ))?;
        repository.install_delta(
            segment(
                "delta-acknowledged",
                [document("acknowledged", "acknowledged", 2, false, 0.5, 0.5)],
            ),
            false,
        )?;
        let failed = repository.install_delta_with_crash(
            segment(
                "delta-faulted",
                [
                    document("fault-a", "fault-a", 3, false, 0.7, 0.7),
                    document("fault-b", "fault-b", 3, false, 0.8, 0.8),
                ],
            ),
            Some(crash_point),
        );
        let error = failed.expect_err("injected crash must stop the publication");
        assert_eq!(error.kind(), io::ErrorKind::Interrupted);

        let reopened = Repository::new(temporary.path())?;
        let recovered = reopened.recover()?;
        let ids = recovered
            .search(1.0, 0.0, 10, 1)
            .into_iter()
            .map(|hit| hit.logical_id)
            .collect::<BTreeSet<_>>();
        assert!(ids.contains("acknowledged"), "{crash_point:?}");
        assert_eq!(
            ids.contains("fault-a"),
            expected_faulted_batch,
            "{crash_point:?}"
        );
        assert_eq!(
            ids.contains("fault-b"),
            expected_faulted_batch,
            "{crash_point:?}"
        );
        let reopened_again = Repository::new(temporary.path())?;
        assert_eq!(
            reopened_again.recover()?.search(1.0, 0.0, 10, 1),
            recovered.search(1.0, 0.0, 10, 1),
            "replay must be idempotent at {crash_point:?}"
        );
    }
    Ok(())
}

#[test]
fn wal_recovery_replays_acknowledged_delta_and_manifest_publish_rolls_back()
-> Result<(), Box<dyn std::error::Error>> {
    let temporary = TempDir::new()?;
    let repository = Repository::new(temporary.path())?;
    repository.initialize(segment(
        "base-1",
        [document("file-a", "fragment-a-old", 1, false, 0.2, 0.2)],
    ))?;
    let previous = repository.load_manifest()?;
    repository.install_delta(
        segment(
            "delta-2",
            [document("file-a", "fragment-a-new", 2, false, 0.9, 0.9)],
        ),
        true,
    )?;

    let recovered = repository.recover()?;
    assert_eq!(recovered.manifest.deltas, ["delta-2"]);
    assert_eq!(
        recovered.search(1.0, 0.0, 1, 1)[0].fragment_id,
        "fragment-a-new"
    );

    let invalid = Manifest {
        generation: 99,
        base: "missing-base".into(),
        deltas: Vec::new(),
    };
    assert!(repository.publish_manifest(&invalid, None).is_err());
    assert_eq!(repository.load_manifest()?, recovered.manifest);
    assert_ne!(previous, recovered.manifest);
    Ok(())
}
