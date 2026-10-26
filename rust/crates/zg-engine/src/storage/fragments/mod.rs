//! Search projections: one zvec collection per embedding model, with FTS and vectors.

use std::{
    borrow::Cow,
    collections::{BTreeMap, HashMap, HashSet},
    path::Path,
    sync::Arc,
};

use zvec_rust::{
    Collection, CollectionSchema, DataType, Doc, FieldSchema, Fts, IndexParams, MetricType,
    SearchQuery,
};

use super::{
    path::{file_membership_doc, file_membership_schema},
    types::{
        IndexedFragment, StoragePathFilter, StorageSearchFilter, StorageSearchHit,
        StorageSearchPath,
    },
    zvec::{
        corrupt, disjunction_filter, doc_key, fetch_map, fetch_map_with_vectors, native,
        open_collection, scalar, string_field, u32_field, write_docs,
    },
};
use crate::{
    EngineError, EngineResult,
    domain::{
        CodeMetadata, DirectoryId, Entity, EntityFragment, EntityId, EntityMetadata, FTS_CONFIG,
        FileId, FileRecord, FragmentId, IndexField, SourcePath,
        model::{EmbeddingModelInfo, Metric},
    },
    utils::{sha256_hex, sha256_hex_parts},
};

const MAX_TOP_K: usize = 100_000;

pub(super) struct Fragments {
    indexes: BTreeMap<String, Arc<Collection>>,
}

impl Fragments {
    pub(super) fn open(
        root: &Path,
        embeddings: &[EmbeddingModelInfo],
        read_only: bool,
    ) -> EngineResult<Self> {
        let mut indexes = BTreeMap::new();
        for embedding in embeddings {
            let dimension = u32::try_from(embedding.dimension)
                .ok()
                .filter(|value| (1..=20_000).contains(value))
                .ok_or_else(|| {
                    EngineError::invalid_argument(
                        "storage embedding dimension must be between 1 and 20,000",
                    )
                })?;
            let metric = match embedding.metric {
                Metric::Cosine => MetricType::Cosine,
                Metric::DotProduct => MetricType::Ip,
                Metric::Euclidean => MetricType::L2,
            };
            let name = fragment_collection_name(embedding);
            let collection = open_collection(
                &root.join(&name),
                &fragments_schema(dimension, metric)?,
                read_only,
            )?;
            if indexes
                .insert(embedding.model.reference(), Arc::new(collection))
                .is_some()
            {
                return Err(EngineError::invalid_argument("duplicate embedding model"));
            }
        }
        Ok(Self { indexes })
    }

    /// Encode the entire replacement before any stored rows are changed.
    pub(super) fn prepare(
        file: &FileRecord,
        entities: &[Entity],
        entries: &[IndexedFragment],
        directories: &[DirectoryId],
    ) -> EngineResult<BTreeMap<String, Vec<Doc>>> {
        let mut encoder = ProjectionEncoder::new(file, entities, directories)?;
        let projections = encoder.prepare(entries)?;
        encoder.finish()?;
        Ok(projections)
    }

    /// Write fragment projections in bounded chunks so a large file does not
    /// hand one oversized `Vec<Doc>` to the native collection in one call.
    pub(super) fn write(&self, projections: &BTreeMap<String, Vec<Doc>>) -> EngineResult<()> {
        const WRITE_CHUNK: usize = 512;
        for (model, docs) in projections {
            let index = self.index(model)?;
            if docs.len() <= WRITE_CHUNK {
                write_docs(index, docs, "write model fragments")?;
            } else {
                for chunk in docs.chunks(WRITE_CHUNK) {
                    write_docs(index, chunk, "write model fragments")?;
                }
            }
        }
        Ok(())
    }

    pub(super) fn fetch_vectors(
        &self,
        model: &str,
        fragment_ids: &[String],
    ) -> EngineResult<HashMap<String, Vec<f32>>> {
        let index = self.index(model)?;
        fetch_map_with_vectors(index, fragment_ids, true)?
            .into_iter()
            .map(|(id, doc)| {
                let vector = native(doc.get_vector_f32("embedding"), "read stored embedding")?
                    .ok_or_else(|| corrupt("stored fragment has no embedding vector"))?;
                Ok((id, vector))
            })
            .collect()
    }

    pub(super) fn validate_ownership(
        &self,
        entries: &[IndexedFragment],
        file_id: FileId,
    ) -> EngineResult<()> {
        let keys = entries
            .iter()
            .map(|entry| entry.fragment_id.as_str().to_owned())
            .collect::<Vec<_>>();
        for index in self.indexes.values() {
            reject_foreign_ids(index, &keys, file_id)?;
        }
        Ok(())
    }

    pub(super) fn delete_fragment_ids(
        &self,
        model: &str,
        fragment_ids: &[FragmentId],
    ) -> EngineResult<()> {
        let index = self.index(model)?;
        for batch in fragment_ids.chunks(512) {
            if batch.is_empty() {
                continue;
            }
            let filter = batch
                .iter()
                .map(|id| format!("document_id = '{}'", id.as_str()))
                .collect::<Vec<_>>()
                .join(" OR ");
            native(
                index.delete_by_filter(&filter),
                "delete selected model fragments",
            )?;
        }
        Ok(())
    }

    pub(super) fn delete_file(&self, file_id: FileId) -> EngineResult<()> {
        self.delete_files(std::slice::from_ref(&file_id))
    }

    pub(super) fn delete_files(&self, file_ids: &[FileId]) -> EngineResult<()> {
        if file_ids.is_empty() {
            return Ok(());
        }
        let values = file_ids
            .iter()
            .map(|file_id| file_id.get())
            .collect::<Vec<_>>();
        let filter = disjunction_filter("file_id", &values);
        for index in self.indexes.values() {
            native(index.delete_by_filter(&filter), "delete source fragments")?;
        }
        Ok(())
    }

    pub(super) fn flush(&self) -> EngineResult<()> {
        for index in self.indexes.values() {
            native(index.flush(), "flush fragment collection")?;
        }
        Ok(())
    }

    pub(super) fn needs_optimize(&self) -> EngineResult<bool> {
        for index in self.indexes.values() {
            let stats = native(index.stats(), "inspect fragment indexes")?;
            if stats.indexes.iter().any(|stat| stat.completeness < 1.0) {
                return Ok(true);
            }
        }
        Ok(false)
    }

    pub(super) fn completeness_summary(&self) -> EngineResult<f32> {
        let mut minimum = 1.0_f32;
        for index in self.indexes.values() {
            let stats = native(index.stats(), "inspect fragment indexes")?;
            for stat in stats.indexes {
                if stat.completeness < minimum {
                    minimum = stat.completeness;
                }
            }
        }
        Ok(minimum)
    }

    pub(super) fn search_fts(
        &self,
        query: &str,
        limit: usize,
        filter: Option<&str>,
    ) -> EngineResult<Vec<StorageSearchHit>> {
        if limit == 0 {
            return Ok(Vec::new());
        }
        let mut fts = native(Fts::new(), "create full-text request")?;
        native(
            fts.set_match_string(&index_text(query)),
            "set full-text query",
        )?;
        let mut request = native(
            SearchQuery::fts("text", &fts, top_k(limit)?),
            "create full-text query",
        )?;
        configure_query(
            &mut request,
            filter,
            &["document_id", "entity_id", "file_id"],
        )?;
        let mut hits = Vec::new();
        for index in self.indexes.values() {
            for (rank, doc) in native(index.query(&request), "search full-text index")?
                .into_iter()
                .enumerate()
            {
                let mut hit = decode_search_hit(&doc, StorageSearchPath::Fts)?;
                // BM25 is computed against each table's corpus; merge independent rankings.
                if self.indexes.len() > 1 {
                    let rank = u32::try_from(rank + 1)
                        .map_err(|_| corrupt("FTS rank exceeds query limit"))?;
                    hit.score = 1.0 / (60.0 + f64::from(rank));
                }
                hits.push(hit);
            }
        }
        hits.sort_by(|a, b| {
            b.score
                .total_cmp(&a.score)
                .then_with(|| a.document_id.cmp(&b.document_id))
        });
        hits.truncate(limit);
        Ok(hits)
    }

    pub(super) fn search_vector(
        &self,
        model: &str,
        vector: &[f32],
        limit: usize,
        filter: Option<&str>,
    ) -> EngineResult<Vec<StorageSearchHit>> {
        let index = self.index(model)?;
        if limit == 0 {
            return Ok(Vec::new());
        }
        let mut request = native(
            SearchQuery::new("embedding", vector, top_k(limit)?),
            "create vector query",
        )?;
        configure_query(
            &mut request,
            filter,
            &["document_id", "entity_id", "file_id"],
        )?;
        native(index.query(&request), "search vector index")?
            .into_iter()
            .map(|doc| decode_search_hit(&doc, StorageSearchPath::Vector))
            .collect()
    }

    pub(super) fn supports_model(&self, model: &str) -> bool {
        self.indexes.contains_key(model)
    }

    fn index(&self, model: &str) -> EngineResult<&Collection> {
        self.indexes
            .get(model)
            .map(|collection| &**collection)
            .ok_or_else(|| {
            EngineError::invalid_argument(format!("unknown embedding model {model:?}"))
        })
    }

    #[cfg(test)]
    pub(super) fn collection(&self, model: &str) -> EngineResult<&Collection> {
        self.index(model)
    }

    /// Checked-out handles for maintenance to optimize outside the state lock.
    pub(super) fn handles(&self) -> Vec<(String, Arc<Collection>)> {
        self.indexes
            .iter()
            .map(|(model, collection)| (model.clone(), Arc::clone(collection)))
            .collect()
    }

    /// Native completeness of a set of fragment handles (no state lock needed).
    pub(super) fn handles_completeness(
        handles: &[(String, Arc<Collection>)],
    ) -> EngineResult<f32> {
        let mut minimum = 1.0_f32;
        for (_, collection) in handles {
            let stats = native(collection.stats(), "inspect fragment indexes")?;
            for stat in stats.indexes {
                if stat.completeness < minimum {
                    minimum = stat.completeness;
                }
            }
        }
        Ok(minimum)
    }
}

/// Reusable lookup state for preparing bounded batches from a staged replacement.
pub(super) struct ProjectionEncoder<'a> {
    file: &'a FileRecord,
    directories: &'a [DirectoryId],
    owners: HashMap<&'a str, (&'a Entity, Vec<(IndexField, String)>)>,
    fragments: HashMap<&'a str, (&'a Entity, &'a EntityFragment)>,
    seen: HashSet<&'a str>,
    models: HashMap<&'a str, String>,
}

impl<'a> ProjectionEncoder<'a> {
    pub(super) fn new(
        file: &'a FileRecord,
        entities: &'a [Entity],
        directories: &'a [DirectoryId],
    ) -> EngineResult<Self> {
        let mut owners = HashMap::with_capacity(entities.len());
        let mut fragments = HashMap::new();
        for entity in entities {
            let fields = entity
                .metadata
                .as_ref()
                .map(|metadata| {
                    let value = serde_json::to_value(metadata)
                        .map_err(|error| corrupt(format!("encode entity metadata: {error}")))?;
                    encode_metadata_fields(&value)
                })
                .transpose()?
                .unwrap_or_default();
            if owners
                .insert(entity.id.as_str(), (entity, fields))
                .is_some()
            {
                return Err(EngineError::invalid_argument("duplicate entity id"));
            }
            for fragment in &entity.fragments {
                if fragments
                    .insert(fragment.id.as_str(), (entity, fragment))
                    .is_some()
                {
                    return Err(EngineError::invalid_argument("duplicate fragment id"));
                }
            }
        }
        Ok(Self {
            file,
            directories,
            owners,
            fragments,
            seen: HashSet::new(),
            models: HashMap::new(),
        })
    }

    pub(super) fn prepare(
        &mut self,
        entries: &[IndexedFragment],
    ) -> EngineResult<BTreeMap<String, Vec<Doc>>> {
        let mut projections: BTreeMap<String, Vec<Doc>> = BTreeMap::new();
        for entry in entries {
            let (owner, fields) = self.owners.get(entry.entity_id.as_str()).ok_or_else(|| {
                EngineError::invalid_argument("search projection references a missing entity")
            })?;
            let (fragment_owner, fragment) = self
                .fragments
                .get(entry.fragment_id.as_str())
                .ok_or_else(|| {
                    EngineError::invalid_argument("search projection references a missing fragment")
                })?;
            if fragment_owner.id != owner.id {
                return Err(EngineError::invalid_argument(
                    "search projection references a fragment owned by another entity",
                ));
            }
            if !self.seen.insert(fragment.id.as_str()) {
                return Err(EngineError::invalid_argument(
                    "canonical fragment has more than one search projection",
                ));
            }
            if self
                .models
                .insert(owner.id.as_str(), entry.model.clone())
                .is_some_and(|previous| previous != entry.model)
            {
                return Err(EngineError::invalid_argument(
                    "all fragments of an entity must use one embedding model",
                ));
            }
            let mut doc = fragment_doc(owner, fragment, self.file, self.directories, fields)?;
            native(
                doc.add_string("text", &index_text(&entry.fts_text)),
                "encode searchable text",
            )?;
            native(
                doc.add_vector_f32("embedding", &entry.vector),
                "encode embedding vector",
            )?;
            projections
                .entry(entry.model.clone())
                .or_default()
                .push(doc);
        }
        Ok(projections)
    }

    pub(super) fn finish(&self) -> EngineResult<()> {
        if self.seen.len() != self.fragments.len() {
            return Err(EngineError::invalid_argument(
                "every canonical fragment requires a search projection",
            ));
        }
        Ok(())
    }
}

fn primary_key(namespace: &str, value: &str) -> String {
    sha256_hex_parts([namespace.as_bytes(), b"\0", value.as_bytes()])
}

pub(super) fn fragment_collection_name(embedding: &EmbeddingModelInfo) -> String {
    format!(
        "fragments_{}",
        primary_key(
            "space",
            &format!(
                "{}\0{}\0{}\0{:?}",
                embedding.model.provider,
                embedding.model.name,
                embedding.dimension,
                embedding.metric
            )
        )
    )
}

fn identity_schema(name: &str) -> EngineResult<CollectionSchema> {
    let mut schema = native(CollectionSchema::new(name), "create collection schema")?;
    scalar(&mut schema, "file_id", DataType::Uint32, false, true)?;
    scalar(&mut schema, "entity_id", DataType::String, false, true)?;
    scalar(&mut schema, "document_id", DataType::String, false, false)?;
    Ok(schema)
}

fn retrieval_schema(name: &str) -> EngineResult<CollectionSchema> {
    let mut schema = identity_schema(name)?;
    file_membership_schema(&mut schema)?;
    for field in EntityMetadata::index_schema() {
        match field {
            IndexField::String(name) => {
                scalar(&mut schema, name, DataType::String, true, true)?;
            }
        }
    }
    Ok(schema)
}

fn fragments_schema(dimension: u32, metric: MetricType) -> EngineResult<CollectionSchema> {
    let mut schema = retrieval_schema("fragments")?;
    let mut text = native(
        FieldSchema::new("text", DataType::String, false, 0),
        "define full-text field",
    )?;
    native(
        text.set_index_params(&native(
            IndexParams::fts(Some(FTS_CONFIG.tokenizer), Some(FTS_CONFIG.filters), None),
            "define full-text index",
        )?),
        "attach full-text index",
    )?;
    native(schema.add_field(&text), "add full-text field")?;

    let mut vector = native(
        FieldSchema::new("embedding", DataType::VectorFp32, false, dimension),
        "define vector field",
    )?;
    native(
        vector.set_index_params(&native(
            IndexParams::hnsw(metric, 50, 500),
            "define vector index",
        )?),
        "attach vector index",
    )?;
    native(schema.add_field(&vector), "add vector field")?;
    Ok(schema)
}

fn identity_doc(entity: &Entity, fragment: &EntityFragment) -> EngineResult<Doc> {
    let mut doc = native(Doc::new(), "create fragment record")?;
    doc.set_pk(fragment.id.as_str());
    native(
        doc.add_u32("file_id", entity.file_id.get()),
        "encode source identity",
    )?;
    native(
        doc.add_string("entity_id", entity.id.as_str()),
        "encode entity identity",
    )?;
    native(
        doc.add_string("document_id", fragment.id.as_str()),
        "encode document identity",
    )?;
    Ok(doc)
}

fn fragment_doc(
    entity: &Entity,
    fragment: &EntityFragment,
    file: &FileRecord,
    directories: &[DirectoryId],
    fields: &[(IndexField, String)],
) -> EngineResult<Doc> {
    let mut doc = identity_doc(entity, fragment)?;
    file_membership_doc(&mut doc, file, directories)?;
    for (field, value) in fields {
        native(
            doc.add_string(field.name(), value),
            "encode metadata index field",
        )?;
    }
    Ok(doc)
}

fn decode_search_hit(doc: &Doc, path: StorageSearchPath) -> EngineResult<StorageSearchHit> {
    let document_id = FragmentId::from_string(string_field(doc, "document_id")?);
    let entity_id = EntityId::from_string(string_field(doc, "entity_id")?);
    if doc_key(doc)? != document_id.as_str() {
        return Err(corrupt(
            "search document identity differs from its primary key",
        ));
    }
    Ok(StorageSearchHit {
        document_id: document_id.as_str().to_owned(),
        entity_id,
        file_id: FileId::new(u32_field(doc, "file_id")?),
        path,
        score: f64::from(doc.get_score()),
        #[cfg(test)]
        m4_revision: None,
    })
}

fn configure_query(
    query: &mut SearchQuery,
    filter: Option<&str>,
    fields: &[&str],
) -> EngineResult<()> {
    native(
        query.set_include_vector(false),
        "omit vectors from search output",
    )?;
    native(
        query.set_output_fields(fields),
        "select search output fields",
    )?;
    if let Some(filter) = filter {
        native(query.set_filter(filter), "set search filter")?;
    }
    Ok(())
}

pub(super) fn build_filter(
    filter: Option<&StorageSearchFilter>,
    resolve_directory: &impl Fn(&SourcePath) -> EngineResult<Option<DirectoryId>>,
) -> EngineResult<Option<String>> {
    let Some(filter) = filter else {
        return Ok(None);
    };
    if empty_filter(Some(filter)) {
        return Ok(Some(constant_filter(false)));
    }
    let mut clauses = Vec::new();
    if let Some(path) = &filter.path
        && !matches!(path, StoragePathFilter::All)
    {
        clauses.push(path_filter(resolve_directory, path, false)?);
    }
    if let Some(ids) = &filter.file_ids {
        clauses.push(file_id_filter(ids));
    }
    if let Some(ids) = &filter.entity_ids {
        clauses.push(in_filter("entity_id", ids.iter().map(EntityId::as_str)));
    }
    if let Some(names) = &filter.symbol_names {
        clauses.push(metadata_in_filter(
            CodeMetadata::SYMBOL_NAME,
            names.iter().map(String::as_str),
        ));
    }
    if let Some(types) = &filter.symbol_types {
        clauses.push(metadata_in_filter(
            CodeMetadata::SYMBOL_TYPE,
            types.iter().map(|kind| kind.as_str()),
        ));
    }
    Ok((!clauses.is_empty()).then(|| clauses.join(" AND ")))
}

/// Fingerprint of the entity fields materialized into a search projection.
///
/// A fragment document stores only identity, file membership, and the indexed
/// metadata fields. Non-indexed metadata (documentation, signature, scope, or
/// Markdown headings) never reaches a projection, so changing it must not
/// invalidate the stored projection and force a vector-index optimize.
pub(super) fn metadata_projection_fingerprint(
    metadata: Option<&EntityMetadata>,
) -> EngineResult<String> {
    let fields = match metadata {
        Some(metadata) => {
            let value = serde_json::to_value(metadata)
                .map_err(|error| corrupt(format!("encode projection metadata: {error}")))?;
            encode_metadata_fields(&value)?
        }
        None => Vec::new(),
    };
    let encoded = fields
        .iter()
        .map(|(field, value)| (field.name(), value.as_str()))
        .collect::<Vec<_>>();
    let bytes = serde_json::to_vec(&encoded)
        .map_err(|error| corrupt(format!("encode metadata projection: {error}")))?;
    Ok(sha256_hex(&bytes))
}

fn encode_metadata_fields(metadata: &serde_json::Value) -> EngineResult<Vec<(IndexField, String)>> {
    let mut fields = Vec::new();
    for field in EntityMetadata::index_schema() {
        let Some(value) = metadata.get(field.name()).filter(|value| !value.is_null()) else {
            continue;
        };
        match field {
            IndexField::String(name) => {
                let value = value.as_str().ok_or_else(|| {
                    corrupt(format!("metadata index field {name:?} must be a string"))
                })?;
                fields.push((field, encode_metadata_value(field, value).into_owned()));
            }
        }
    }
    Ok(fields)
}

fn encode_metadata_value(field: IndexField, value: &str) -> Cow<'_, str> {
    if field == CodeMetadata::SYMBOL_NAME {
        Cow::Owned(primary_key("symbol", value))
    } else {
        Cow::Borrowed(value)
    }
}

fn metadata_in_filter<'a>(field: IndexField, values: impl Iterator<Item = &'a str>) -> String {
    in_filter(
        field.name(),
        values.map(|value| encode_metadata_value(field, value)),
    )
}

fn in_filter(field: &str, values: impl Iterator<Item = impl AsRef<str>>) -> String {
    format!(
        "{field} IN ({})",
        values
            .map(|value| quote(value.as_ref()))
            .collect::<Vec<_>>()
            .join(", ")
    )
}

/// Push negation to field operators; execution support and errors belong to zvec.
fn path_filter(
    resolve_directory: &impl Fn(&SourcePath) -> EngineResult<Option<DirectoryId>>,
    filter: &StoragePathFilter,
    negated: bool,
) -> EngineResult<String> {
    Ok(match filter {
        StoragePathFilter::All => constant_filter(!negated),
        StoragePathFilter::None => constant_filter(negated),
        StoragePathFilter::Directory(path) => {
            let Some(id) = resolve_directory(path)? else {
                return Ok(constant_filter(negated));
            };
            format!(
                "ancestor_directory_ids {}CONTAIN_ANY ({id})",
                if negated { "NOT " } else { "" }
            )
        }
        StoragePathFilter::FileNameExact(name) => {
            if name.contains(['\\', '\0']) {
                return Err(EngineError::invalid_argument(
                    "this file name requires path matching",
                ));
            }
            format!(
                "file_name {} {}",
                if negated { "!=" } else { "=" },
                quote(name)
            )
        }
        StoragePathFilter::FileNamePrefix(prefix) => format!(
            "file_name {}LIKE {}",
            if negated { "NOT " } else { "" },
            quote(&format!("{}%", like_literal(prefix)))
        ),
        StoragePathFilter::FileNameSuffix(suffix) => format!(
            "file_name {}LIKE {}",
            if negated { "NOT " } else { "" },
            quote(&format!("%{}", like_literal(suffix)))
        ),
        StoragePathFilter::And(filters) => {
            boolean_filter(resolve_directory, filters, !negated, negated)?
        }
        StoragePathFilter::Or(filters) => {
            boolean_filter(resolve_directory, filters, negated, negated)?
        }
        StoragePathFilter::Not(filter) => path_filter(resolve_directory, filter, !negated)?,
    })
}

fn file_id_filter(ids: &[FileId]) -> String {
    if ids.is_empty() {
        return constant_filter(false);
    }
    format!(
        "file_id IN ({})",
        ids.iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>()
            .join(", ")
    )
}

fn constant_filter(value: bool) -> String {
    // Every stored document has a file ID, including ID zero.
    format!("file_id IS {}NULL", if value { "NOT " } else { "" })
}

fn boolean_filter(
    resolve_directory: &impl Fn(&SourcePath) -> EngineResult<Option<DirectoryId>>,
    filters: &[StoragePathFilter],
    conjunction: bool,
    negated: bool,
) -> EngineResult<String> {
    if filters.is_empty() {
        return Ok(constant_filter(conjunction));
    }
    let operator = if conjunction { " AND " } else { " OR " };
    Ok(format!(
        "({})",
        filters
            .iter()
            .map(|filter| path_filter(resolve_directory, filter, negated))
            .collect::<EngineResult<Vec<_>>>()?
            .join(operator)
    ))
}

fn like_literal(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('%', "\\%")
        .replace('_', "\\_")
}

pub(super) fn empty_filter(filter: Option<&StorageSearchFilter>) -> bool {
    filter.is_some_and(|filter| {
        filter.file_ids.as_ref().is_some_and(Vec::is_empty)
            || filter.entity_ids.as_ref().is_some_and(Vec::is_empty)
            || filter.symbol_names.as_ref().is_some_and(Vec::is_empty)
            || filter.symbol_types.as_ref().is_some_and(Vec::is_empty)
    })
}

fn quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "\\'"))
}

fn top_k(limit: usize) -> EngineResult<i32> {
    if limit > MAX_TOP_K {
        return Err(EngineError::invalid_argument(format!(
            "storage query limit must not exceed {MAX_TOP_K}"
        )));
    }
    i32::try_from(limit)
        .map_err(|_| EngineError::invalid_argument("storage query limit is too large"))
}

// C strings cannot contain NUL; indexed projections preserve token boundaries.
fn index_text(text: &str) -> Cow<'_, str> {
    if text.contains('\0') {
        Cow::Owned(text.replace('\0', " "))
    } else {
        Cow::Borrowed(text)
    }
}

fn reject_foreign_ids(
    collection: &Collection,
    keys: &[String],
    file_id: FileId,
) -> EngineResult<()> {
    for doc in fetch_map(collection, keys)?.into_values() {
        if u32_field(&doc, "file_id")? != file_id.get() {
            return Err(EngineError::invalid_argument(
                "entity or fragment ID is already owned by another file",
            ));
        }
    }
    Ok(())
}

/// Every canonical fragment has exactly one projection, and its entity has one model.
pub(super) fn validate_projections(
    entities: &[Entity],
    entries: &[IndexedFragment],
) -> EngineResult<()> {
    let expected = entities
        .iter()
        .flat_map(|entity| {
            entity
                .fragments
                .iter()
                .map(move |fragment| (&fragment.id, &entity.id))
        })
        .collect::<HashMap<_, _>>();
    let mut seen = HashSet::new();
    let mut models = HashMap::new();
    for entry in entries {
        if expected.get(&entry.fragment_id).copied() != Some(&entry.entity_id) {
            return Err(EngineError::invalid_argument(
                "search projection does not reference its canonical entity fragment",
            ));
        }
        if !seen.insert(&entry.fragment_id) {
            return Err(EngineError::invalid_argument(
                "canonical fragment has more than one search projection",
            ));
        }
        if models
            .insert(&entry.entity_id, &entry.model)
            .is_some_and(|previous| previous != &entry.model)
        {
            return Err(EngineError::invalid_argument(
                "all fragments of an entity must use one embedding model",
            ));
        }
    }
    if expected.len() != seen.len() {
        return Err(EngineError::invalid_argument(
            "every canonical fragment requires a search projection",
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests;
