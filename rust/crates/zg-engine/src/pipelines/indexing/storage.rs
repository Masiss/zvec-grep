//! Storage operations used by indexing, with a small seam for pipeline tests.

use std::path::PathBuf;

use crate::{
    EngineResult,
    domain::{Entity, FileGraphResult, FileId, FileRecord},
    storage::{IndexStore, manifest::FileManifest, types::IndexedFragment},
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
        graph: &FileGraphResult,
    ) -> EngineResult<()>;
    fn update_file_snapshot(&self, file: &FileRecord) -> EngineResult<()>;
    fn mark_file_failed(&self, file: &FileRecord, error: &str) -> EngineResult<()>;
    fn delete_file(&self, file_id: FileId) -> EngineResult<()>;
    fn finalize_writes(&self) -> EngineResult<()>;
    /// Per-stage maintenance wall durations for the last finalization. The
    /// default keeps lightweight test storages from implementing telemetry.
    fn finalize_writes_detailed(&self) -> EngineResult<Vec<(String, std::time::Duration)>> {
        self.finalize_writes().map(|()| Vec::new())
    }
    /// Return stored vectors whose fragment identity and model still match.
    /// The default keeps lightweight test storages from implementing M2.
    fn load_reusable_vectors(
        &self,
        _file_id: FileId,
        _model: &str,
        _fragment_ids: &[String],
    ) -> EngineResult<std::collections::HashMap<String, Vec<f32>>> {
        Ok(std::collections::HashMap::new())
    }

    fn load_embedding_manifest(&self, _file_id: FileId) -> EngineResult<Option<FileManifest>> {
        Ok(None)
    }

    fn publish_embedding_manifest(&self, _manifest: &mut FileManifest) -> EngineResult<bool> {
        Ok(true)
    }

    fn delete_embedding_manifest(&self, _file_id: FileId) -> EngineResult<()> {
        Ok(())
    }
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
        graph: &FileGraphResult,
    ) -> EngineResult<()> {
        self.replace_file_with_graph(file, entities, entries, graph)
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

    fn finalize_writes(&self) -> EngineResult<()> {
        self.finalize_writes()
    }

    fn finalize_writes_detailed(&self) -> EngineResult<Vec<(String, std::time::Duration)>> {
        self.finalize_writes_detailed()
    }

    fn load_reusable_vectors(
        &self,
        file_id: FileId,
        model: &str,
        fragment_ids: &[String],
    ) -> EngineResult<std::collections::HashMap<String, Vec<f32>>> {
        self.load_reusable_vectors(file_id, model, fragment_ids)
    }

    fn load_embedding_manifest(&self, file_id: FileId) -> EngineResult<Option<FileManifest>> {
        self.load_embedding_manifest(file_id)
    }

    fn publish_embedding_manifest(&self, manifest: &mut FileManifest) -> EngineResult<bool> {
        self.publish_embedding_manifest(manifest)
    }

    fn delete_embedding_manifest(&self, file_id: FileId) -> EngineResult<()> {
        self.delete_embedding_manifest(file_id)
    }
}
