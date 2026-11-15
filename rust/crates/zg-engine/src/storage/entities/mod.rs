//! Canonical entities, content, metadata, and fragment selectors.
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    sync::{Arc, OnceLock},
};

use zvec_rust::{Collection, CollectionSchema, DataType, Doc};

use crate::{
    EngineError, EngineResult,
    domain::{CodeMetadata, Entity, EntityId, EntityMetadata, FileId},
};

use super::zvec::{
    WRITE_BATCH, collect_entity_ids, corrupt, doc_key, fetch_map, native, open_collection, scalar,
    string_field, u32_field, write_docs,
};
use crate::utils::directory_exists;

mod codec;

pub(super) struct Entities {
    path: PathBuf,
    read_only: bool,
    /// Opened on first use so a pass with no entity work never pays for the
    /// native collection (the E1/no-op audit measured ~8 ms here, and the same
    /// pattern removes the much larger vector-collection cost).
    collection: OnceLock<Arc<Collection>>,
}

impl Entities {
    #[cfg(test)]
    pub(super) fn collection(&self) -> &Collection {
        self.ensure_collection().expect("entities collection")
    }

    /// Clone a stable native handle for handle-lifetime regression tests.
    #[cfg(test)]
    pub(super) fn handle(&self) -> Arc<Collection> {
        self.ensure_collection().expect("entities collection");
        Arc::clone(self.collection.get().expect("entities collection"))
    }

    pub(super) fn open(path: &Path, read_only: bool) -> EngineResult<Self> {
        let entities = Self {
            path: path.to_path_buf(),
            read_only,
            collection: OnceLock::new(),
        };
        // New storage must publish a complete layout immediately; existing
        // collections are opened only when a pass actually needs them.
        let collection_path = entities.path.join("entities");
        if !directory_exists(&collection_path)? {
            if read_only {
                return Err(EngineError::not_found(format!(
                    "index collection does not exist: {}",
                    collection_path.display()
                )));
            }
            entities.ensure_collection()?;
        }
        Ok(entities)
    }

    fn ensure_collection(&self) -> EngineResult<&Collection> {
        if let Some(collection) = self.collection.get() {
            return Ok(collection);
        }
        let opened = Arc::new(open_collection(
            &self.path.join("entities"),
            &schema()?,
            self.read_only,
        )?);
        let _ = self.collection.set(opened);
        Ok(self.collection.get().expect("initialized"))
    }

    #[cfg(test)]
    pub(super) fn is_open(&self) -> bool {
        self.collection.get().is_some()
    }

    /// Encode the full replacement before any existing records are removed.
    /// The storage write entry point validates ownership.
    pub(super) fn prepare(entities: &[Entity]) -> EngineResult<Vec<Doc>> {
        entities.iter().map(encode_doc).collect()
    }

    pub(super) fn write(&self, docs: &[Doc]) -> EngineResult<()> {
        write_docs(
            self.ensure_collection()?,
            docs,
            "write entities and canonical fragments",
        )
    }

    pub(super) fn fetch(&self, ids: &[EntityId]) -> EngineResult<HashMap<EntityId, Entity>> {
        let keys = ids
            .iter()
            .map(|id| id.as_str().to_owned())
            .collect::<Vec<_>>();
        fetch_map(self.ensure_collection()?, &keys)?
            .into_values()
            .map(|doc| decode_doc(&doc).map(|entity| (entity.id().clone(), entity)))
            .collect()
    }

    /// Fetch endpoint identity and ownership without loading content or metadata.
    pub(super) fn fetch_file_ids(
        &self,
        ids: &[EntityId],
    ) -> EngineResult<HashMap<EntityId, FileId>> {
        let mut owners = HashMap::with_capacity(ids.len());
        for batch in ids.chunks(WRITE_BATCH) {
            let keys: Vec<_> = batch.iter().map(EntityId::as_str).collect();
            for doc in native(
                self.ensure_collection()?.fetch_with_options(
                    &keys,
                    Some(&["entity_id", "file_id"]),
                    false,
                ),
                "fetch entity ownership",
            )? {
                let id = string_field(&doc, "entity_id")?;
                if doc_key(&doc)? != id {
                    return Err(corrupt("entity identity differs from its index fields"));
                }
                owners.insert(
                    EntityId::from_string(id),
                    FileId::new(u32_field(&doc, "file_id")?),
                );
            }
        }
        Ok(owners)
    }

    /// Read code metadata and ownership without loading canonical bodies.
    pub(super) fn fetch_code_metadata(
        &self,
        ids: &[EntityId],
    ) -> EngineResult<HashMap<EntityId, (FileId, CodeMetadata)>> {
        let mut owners = HashMap::new();
        for batch in ids.chunks(WRITE_BATCH) {
            let keys: Vec<_> = batch.iter().map(EntityId::as_str).collect();
            for doc in native(
                self.ensure_collection()?.fetch_with_options(
                    &keys,
                    Some(&["entity_id", "file_id", "metadata"]),
                    false,
                ),
                "fetch symbol metadata",
            )? {
                let id = string_field(&doc, "entity_id")?;
                if doc_key(&doc)? != id {
                    return Err(corrupt("entity identity differs from its index fields"));
                }
                if let Some(EntityMetadata::Code(metadata)) = decode_metadata(&doc)? {
                    owners.insert(
                        EntityId::from_string(id),
                        (FileId::new(u32_field(&doc, "file_id")?), metadata),
                    );
                }
            }
        }
        Ok(owners)
    }

    /// Read only the canonical source range for each requested entity.
    /// Callers bound the ID list; payload bytes are not retained.
    pub(super) fn fetch_source_ranges(
        &self,
        ids: &[EntityId],
    ) -> EngineResult<HashMap<EntityId, crate::domain::Range>> {
        let mut ranges = HashMap::with_capacity(ids.len());
        for batch in ids.chunks(WRITE_BATCH) {
            let keys: Vec<_> = batch.iter().map(EntityId::as_str).collect();
            for doc in native(
                self.ensure_collection()?.fetch_with_options(
                    &keys,
                    Some(&["entity_id", "payload"]),
                    false,
                ),
                "fetch entity source ranges",
            )? {
                let id = string_field(&doc, "entity_id")?;
                if doc_key(&doc)? != id {
                    return Err(corrupt("entity identity differs from its index fields"));
                }
                let range = codec::decode_source_range(&string_field(&doc, "payload")?)?;
                ranges.insert(EntityId::from_string(id), range);
            }
        }
        Ok(ranges)
    }

    /// Read every canonical ID before replacing or deleting a file's entities.
    /// The caller must hold the store lock throughout this read and the update.
    /// Full query batches are split into disjoint indexed ranges so a retrieval
    /// limit cannot silently omit IDs needed to invalidate incoming graph edges.
    pub(super) fn list_ids_for_file(&self, file_id: FileId) -> EngineResult<Vec<String>> {
        collect_entity_ids(
            self.ensure_collection()?,
            &format!("file_id = {}", file_id.get()),
            |doc| {
                if u32_field(doc, "file_id")? != file_id.get() {
                    return Err(corrupt("entity query returned another file's record"));
                }
                if doc_key(doc)? != string_field(doc, "entity_id")? {
                    return Err(corrupt("entity identity differs from its index fields"));
                }
                Ok(())
            },
        )
    }

    pub(super) fn validate_ownership(
        &self,
        entities: &[Entity],
        file_id: FileId,
    ) -> EngineResult<()> {
        let keys = entities
            .iter()
            .map(|entity| entity.id().as_str().to_owned())
            .collect::<Vec<_>>();
        for doc in fetch_map(self.ensure_collection()?, &keys)?.into_values() {
            if u32_field(&doc, "file_id")? != file_id.get() {
                return Err(EngineError::invalid_argument(
                    "entity or fragment ID is already owned by another file",
                ));
            }
        }
        Ok(())
    }

    pub(super) fn delete_file(&self, file_id: FileId) -> EngineResult<()> {
        native(
            self.ensure_collection()?
                .delete_by_filter(&format!("file_id = {}", file_id.get())),
            "delete source entities",
        )
    }

    /// Flushes only when the collection was opened, so a pass that touched no
    /// entity leaves the native index untouched.
    pub(super) fn flush(&self) -> EngineResult<()> {
        match self.collection.get() {
            Some(collection) => native(collection.flush(), "flush entities"),
            None => Ok(()),
        }
    }

    pub(super) fn optimize(&self) -> EngineResult<()> {
        match self.collection.get() {
            Some(collection) => native(collection.optimize(), "optimize entities"),
            None => Ok(()),
        }
    }
}

fn schema() -> EngineResult<CollectionSchema> {
    let mut schema = native(CollectionSchema::new("entities"), "create entities schema")?;
    scalar(&mut schema, "file_id", DataType::Uint32, false, true)?;
    scalar(&mut schema, "entity_id", DataType::String, false, true)?;
    scalar(&mut schema, "payload", DataType::String, false, false)?;
    scalar(&mut schema, "metadata", DataType::String, true, false)?;
    Ok(schema)
}

fn encode_doc(entity: &Entity) -> EngineResult<Doc> {
    let mut doc = native(Doc::new(), "create canonical entity record")?;
    doc.set_pk(entity.id().as_str());
    native(
        doc.add_u32("file_id", entity.file_id().get()),
        "encode entity file",
    )?;
    native(
        doc.add_string("entity_id", entity.id().as_str()),
        "encode entity identity",
    )?;
    native(
        doc.add_string("payload", &codec::encode_entity(entity)?),
        "encode canonical entity",
    )?;
    if let Some(metadata) = entity.metadata() {
        let value = serde_json::to_value(metadata)
            .map_err(|error| corrupt(format!("encode entity metadata: {error}")))?;
        let json = serde_json::to_string(&value)
            .map_err(|error| corrupt(format!("encode entity metadata: {error}")))?;
        native(doc.add_string("metadata", &json), "encode entity metadata")?;
    }
    Ok(doc)
}

fn decode_doc(doc: &Doc) -> EngineResult<Entity> {
    let metadata = decode_metadata(doc)?;
    let entity = codec::decode_entity(&string_field(doc, "payload")?, metadata.as_ref())?;
    if doc_key(doc)? != entity.id().as_str()
        || u32_field(doc, "file_id")? != entity.file_id().get()
        || string_field(doc, "entity_id")? != entity.id().as_str()
    {
        return Err(corrupt("entity identity differs from its index fields"));
    }
    Ok(entity)
}

fn decode_metadata(doc: &Doc) -> EngineResult<Option<EntityMetadata>> {
    if !doc.has_field("metadata") || doc.is_field_null("metadata") {
        return Ok(None);
    }
    let json = string_field(doc, "metadata")?;
    serde_json::from_str(&json)
        .map(Some)
        .map_err(|error| corrupt(format!("invalid entity metadata: {error}")))
}

#[cfg(test)]
mod tests;
