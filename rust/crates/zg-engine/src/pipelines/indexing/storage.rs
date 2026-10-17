//! Storage operations used by indexing, with a small seam for pipeline tests.

use std::{collections::HashMap, path::PathBuf};

use crate::{
    EngineResult,
    domain::{Entity, FileId, FileRecord},
    storage::{
        IndexStore, manifest::FileManifest, staging::DiskStagedBatches, types::IndexedFragment,
    },
};

pub(crate) trait IndexStorage: Send + Sync {
    fn is_read_only(&self) -> bool;
    fn list_files(&self) -> EngineResult<Vec<FileRecord>>;
    fn resolve_file_ids(&self, paths: &[PathBuf]) -> EngineResult<Vec<FileId>>;
    fn replace_file(
        &self,
        file: &FileRecord,
        entities: &[Entity],
        entries: &[IndexedFragment],
    ) -> EngineResult<()>;

    /// Publish a staged replacement built from disk-backed fragment batches.
    fn replace_file_from_staged(&self, staged: DiskStagedBatches) -> EngineResult<()>;
    fn load_embedding_manifest(&self, _file_id: FileId) -> EngineResult<Option<FileManifest>> {
        Ok(None)
    }
    fn fetch_fragment_vectors(
        &self,
        _model: &str,
        _fragment_ids: &[String],
    ) -> EngineResult<HashMap<String, Vec<f32>>> {
        Ok(HashMap::new())
    }
    fn update_file_snapshot(&self, file: &FileRecord) -> EngineResult<()>;
    fn mark_file_failed(&self, file: &FileRecord, error: &str) -> EngineResult<()>;
    fn delete_file(&self, file_id: FileId) -> EngineResult<()>;
    fn delete_files(&self, file_ids: &[FileId]) -> EngineResult<()> {
        for file_id in file_ids {
            self.delete_file(*file_id)?;
        }
        Ok(())
    }
    fn finalize_writes(&self) -> EngineResult<()>;
}

impl IndexStorage for IndexStore {
    fn is_read_only(&self) -> bool {
        self.is_read_only()
    }

    fn list_files(&self) -> EngineResult<Vec<FileRecord>> {
        self.list_files()
    }

    fn resolve_file_ids(&self, paths: &[PathBuf]) -> EngineResult<Vec<FileId>> {
        self.resolve_file_ids(paths)
    }

    fn replace_file(
        &self,
        file: &FileRecord,
        entities: &[Entity],
        entries: &[IndexedFragment],
    ) -> EngineResult<()> {
        self.replace_file(file, entities, entries)
    }

    fn replace_file_from_staged(&self, staged: DiskStagedBatches) -> EngineResult<()> {
        self.replace_file_from_staged(staged)
    }

    fn load_embedding_manifest(&self, file_id: FileId) -> EngineResult<Option<FileManifest>> {
        self.load_embedding_manifest(file_id)
    }

    fn fetch_fragment_vectors(
        &self,
        model: &str,
        fragment_ids: &[String],
    ) -> EngineResult<HashMap<String, Vec<f32>>> {
        self.fetch_fragment_vectors(model, fragment_ids)
    }

    fn update_file_snapshot(&self, file: &FileRecord) -> EngineResult<()> {
        self.update_file_snapshot(file)
    }

    fn mark_file_failed(&self, file: &FileRecord, error: &str) -> EngineResult<()> {
        self.mark_file_failed(file, error)
    }

    fn delete_file(&self, file_id: FileId) -> EngineResult<()> {
        self.delete_file(file_id)
    }

    fn delete_files(&self, file_ids: &[FileId]) -> EngineResult<()> {
        self.delete_files(file_ids)
    }

    fn finalize_writes(&self) -> EngineResult<()> {
        self.finalize_writes()
    }
}
