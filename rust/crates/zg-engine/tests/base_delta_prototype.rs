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

#[derive(Clone, Debug, PartialEq)]
struct Hit {
    logical_id: String,
    fragment_id: String,
    generation: u64,
    score: f64,
}

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
                    .is_none_or(|current: &&Document| current.generation < document.generation);
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

    fn compact(&self) -> Segment {
        let mut documents = self
            .latest_documents()
            .into_values()
            .filter(|document| !document.deleted)
            .cloned()
            .collect::<Vec<_>>();
        documents.sort_by(|left, right| left.logical_id.cmp(&right.logical_id));
        Segment {
            name: format!("base-{}", self.manifest.generation + 1),
            documents,
        }
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
                if current.generation != document.generation || document.deleted {
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
}

impl Repository {
    fn new(root: &Path) -> io::Result<Self> {
        fs::create_dir_all(root.join("segments"))?;
        Ok(Self {
            root: root.to_path_buf(),
        })
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

    fn write_segment(&self, segment: &Segment) -> io::Result<()> {
        let temporary = self.segment_path(&segment.name).with_extension("json.tmp");
        let bytes = serde_json::to_vec_pretty(segment).map_err(io::Error::other)?;
        let mut file = fs::File::create(&temporary)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        fs::rename(temporary, self.segment_path(&segment.name))?;
        Ok(())
    }

    fn append_wal(&self, record: &WalRecord) -> io::Result<()> {
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.wal_path())?;
        serde_json::to_writer(&mut file, record).map_err(io::Error::other)?;
        file.write_all(b"\n")?;
        file.sync_all()
    }

    fn publish_manifest(&self, manifest: &Manifest) -> io::Result<()> {
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
        fs::rename(temporary, self.manifest_path())?;
        #[cfg(unix)]
        fs::File::open(&self.root)?.sync_all()?;
        Ok(())
    }

    fn initialize(&self, base: Segment) -> io::Result<()> {
        self.write_segment(&base)?;
        self.publish_manifest(&Manifest {
            generation: base
                .documents
                .iter()
                .map(|document| document.generation)
                .max()
                .unwrap_or(0),
            base: base.name,
            deltas: Vec::new(),
        })
    }

    fn load_manifest(&self) -> io::Result<Manifest> {
        serde_json::from_slice(&fs::read(self.manifest_path())?).map_err(io::Error::other)
    }

    fn load_segment(&self, name: &str) -> io::Result<Segment> {
        serde_json::from_slice(&fs::read(self.segment_path(name))?).map_err(io::Error::other)
    }

    fn install_delta(&self, delta: Segment, crash_after_wal: bool) -> io::Result<()> {
        let mut manifest = self.load_manifest()?;
        self.write_segment(&delta)?;
        let generation = delta
            .documents
            .iter()
            .map(|document| document.generation)
            .max()
            .unwrap_or(manifest.generation);
        self.append_wal(&WalRecord::DeltaPublished {
            segment: delta.name.clone(),
            generation,
        })?;
        manifest.generation = manifest.generation.max(generation);
        manifest.deltas.push(delta.name);
        if !crash_after_wal {
            self.publish_manifest(&manifest)?;
        }
        Ok(())
    }

    fn recover(&self) -> io::Result<SearchIndex> {
        let mut manifest = self.load_manifest()?;
        if let Ok(file) = fs::File::open(self.wal_path()) {
            for line in BufReader::new(file).lines() {
                let line = line?;
                if line.trim().is_empty() {
                    continue;
                }
                let WalRecord::DeltaPublished {
                    segment,
                    generation,
                } = serde_json::from_str(&line).map_err(io::Error::other)?;
                if generation > manifest.generation {
                    if !manifest.deltas.contains(&segment) {
                        manifest.deltas.push(segment);
                    }
                    manifest.generation = generation;
                }
            }
        }
        self.publish_manifest(&manifest)?;
        let mut names = vec![manifest.base.clone()];
        names.extend(manifest.deltas.iter().cloned());
        let segments = names
            .into_iter()
            .map(|name| self.load_segment(&name).map(|segment| (name, segment)))
            .collect::<io::Result<BTreeMap<_, _>>>()?;
        Ok(SearchIndex { manifest, segments })
    }

    fn compact(&self) -> io::Result<()> {
        let index = self.recover()?;
        let base = index.compact();
        self.write_segment(&base)?;
        self.publish_manifest(&Manifest {
            generation: index.manifest.generation + 1,
            base: base.name,
            deltas: Vec::new(),
        })
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
    assert!(repository.publish_manifest(&invalid).is_err());
    assert_eq!(repository.load_manifest()?, recovered.manifest);
    assert_ne!(previous, recovered.manifest);
    Ok(())
}
