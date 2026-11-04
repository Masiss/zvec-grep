use std::{
    ffi::{CStr, CString, c_char, c_int},
    marker::PhantomData,
    path::Path,
};

#[repr(C)]
struct RawDoc {
    text: *const u8,
    text_len: usize,
}

#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Hit {
    pub file_id: u64,
    pub revision: u64,
    pub ordinal: u32,
    pub score: f32,
}

#[repr(C)]
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct FileState {
    pub revision: u64,
    pub visibility: u32,
    pub expected: u32,
    pub acknowledged: u32,
    pub retained_docs: u32,
    pub found: u32,
}

#[repr(C)]
struct RawIndex {
    _private: [u8; 0],
}

unsafe extern "C" {
    fn m4_rust_file_open(root: *const c_char, error: *mut c_char, cap: usize) -> *mut RawIndex;
    fn m4_rust_file_close(index: *mut RawIndex);
    fn m4_rust_file_compact_now(index: *mut RawIndex, error: *mut c_char, cap: usize) -> c_int;
    fn m4_rust_file_begin(
        index: *mut RawIndex,
        file_id: u64,
        revision: u64,
        expected: u32,
        error: *mut c_char,
        cap: usize,
    ) -> c_int;
    fn m4_rust_file_append(
        index: *mut RawIndex,
        file_id: u64,
        revision: u64,
        first: u32,
        docs: *const RawDoc,
        count: usize,
        error: *mut c_char,
        cap: usize,
    ) -> c_int;
    fn m4_rust_file_publish(
        index: *mut RawIndex,
        file_id: u64,
        revision: u64,
        error: *mut c_char,
        cap: usize,
    ) -> c_int;
    fn m4_rust_file_rollback(
        index: *mut RawIndex,
        file_id: u64,
        revision: u64,
        error: *mut c_char,
        cap: usize,
    ) -> c_int;
    fn m4_rust_file_delete(
        index: *mut RawIndex,
        file_id: u64,
        revision: u64,
        error: *mut c_char,
        cap: usize,
    ) -> c_int;
    fn m4_rust_file_get_state(
        index: *mut RawIndex,
        file_id: u64,
        state: *mut FileState,
        error: *mut c_char,
        cap: usize,
    ) -> c_int;
    fn m4_rust_file_search(
        index: *mut RawIndex,
        query: *const u8,
        query_len: usize,
        limit: usize,
        scan_cap: u32,
        hits: *mut Hit,
        hit_cap: usize,
        count: *mut usize,
        error: *mut c_char,
        error_cap: usize,
    ) -> c_int;
}

pub struct Adapter {
    raw: *mut RawIndex,
    _not_send: PhantomData<*mut ()>,
}

// SAFETY: `raw` uniquely owns the opaque C++ handle. `Adapter` exposes every
// operation through `&mut self`, and the handle serializes access internally;
// moving the unique owner between threads does not move the C++ object itself.
unsafe impl Send for Adapter {}

impl Adapter {
    pub fn open(root: &Path) -> Result<Self, String> {
        let path = CString::new(root.to_str().ok_or("non-Unicode research path")?)
            .map_err(|e| e.to_string())?;
        let mut error = [0 as c_char; 1024];
        let raw = unsafe { m4_rust_file_open(path.as_ptr(), error.as_mut_ptr(), error.len()) };
        if raw.is_null() {
            return Err(read_error(&error));
        }
        Ok(Self {
            raw,
            _not_send: PhantomData,
        })
    }

    /// Flush and compact the research sidecar after publication; never changes native-store state.
    pub fn compact_now(&mut self) -> Result<(), String> {
        self.call(|raw, error| unsafe { m4_rust_file_compact_now(raw, error, 1024) })
    }

    pub fn begin(&mut self, file_id: u64, revision: u64, expected: u32) -> Result<(), String> {
        self.call(|raw, error| unsafe {
            m4_rust_file_begin(raw, file_id, revision, expected, error, 1024)
        })
    }

    pub fn append(
        &mut self,
        file_id: u64,
        revision: u64,
        first: u32,
        docs: &[String],
    ) -> Result<(), String> {
        let raw_docs = docs
            .iter()
            .map(|doc| RawDoc {
                text: doc.as_ptr(),
                text_len: doc.len(),
            })
            .collect::<Vec<_>>();
        self.call(|raw, error| unsafe {
            m4_rust_file_append(
                raw,
                file_id,
                revision,
                first,
                raw_docs.as_ptr(),
                raw_docs.len(),
                error,
                1024,
            )
        })
    }

    pub fn publish(&mut self, file_id: u64, revision: u64) -> Result<(), String> {
        self.call(|raw, error| unsafe { m4_rust_file_publish(raw, file_id, revision, error, 1024) })
    }

    /// Stage one complete file in bounded M4 batches; rows remain invisible.
    pub fn stage_file(
        &mut self,
        file_id: u64,
        revision: u64,
        docs: &[String],
    ) -> Result<(), String> {
        let expected =
            u32::try_from(docs.len()).map_err(|_| "file has too many fragments".to_owned())?;
        let state = self.file_state(file_id)?;
        if state.found != 0 && state.revision == revision && state.visibility == 2 {
            if state.expected != expected {
                return Err("published revision retry has a different fragment count".to_owned());
            }
            return Ok(());
        }
        if docs.iter().any(|doc| doc.len() > (2 << 20)) {
            return Err("fragment exceeds 2 MiB adapter limit".to_owned());
        }
        self.begin(file_id, revision, expected)?;
        let mut first = 0usize;
        while first < docs.len() {
            let mut count = 0usize;
            let mut bytes = 0usize;
            while first + count < docs.len() && count < 256 {
                let next = docs[first + count].len();
                if count != 0 && bytes + next > (6 << 20) {
                    break;
                }
                bytes += next;
                count += 1;
            }
            let end = first + count;
            if let Err(stage_error) =
                self.append(file_id, revision, first as u32, &docs[first..end])
            {
                return match self.rollback(file_id, revision) {
                    Ok(()) => Err(stage_error),
                    Err(rollback_error) => Err(format!(
                        "{stage_error}; M4 rollback failed: {rollback_error}"
                    )),
                };
            }
            first = end;
        }
        Ok(())
    }

    /// Publish only after the caller confirms its native file commit is durable.
    /// A failed native commit leaves the file hidden and returns the rollback result.
    pub fn publish_after_native_commit(
        &mut self,
        file_id: u64,
        revision: u64,
        native_commit: impl FnOnce() -> Result<(), String>,
    ) -> Result<(), String> {
        if let Err(commit_error) = native_commit() {
            return match self.rollback(file_id, revision) {
                Ok(()) => Err(commit_error),
                Err(rollback_error) => Err(format!(
                    "{commit_error}; M4 rollback failed: {rollback_error}"
                )),
            };
        }
        self.publish(file_id, revision)
    }

    pub fn rollback(&mut self, file_id: u64, revision: u64) -> Result<(), String> {
        self.call(|raw, error| unsafe {
            m4_rust_file_rollback(raw, file_id, revision, error, 1024)
        })
    }

    pub fn delete(&mut self, file_id: u64, revision: u64) -> Result<(), String> {
        self.call(|raw, error| unsafe { m4_rust_file_delete(raw, file_id, revision, error, 1024) })
    }

    pub fn file_state(&mut self, file_id: u64) -> Result<FileState, String> {
        let mut state = FileState::default();
        self.call(|raw, error| unsafe {
            m4_rust_file_get_state(raw, file_id, &mut state, error, 1024)
        })?;
        Ok(state)
    }

    pub fn search(&mut self, query: &str, limit: usize, scan_cap: u32) -> Result<Vec<Hit>, String> {
        const MAX_SEARCH_HITS: usize = 65_536;
        if limit > MAX_SEARCH_HITS || scan_cap as usize > MAX_SEARCH_HITS {
            return Err("search exceeds 65,536-hit memory bound".to_owned());
        }
        if limit > scan_cap as usize {
            return Err("scan cap is below requested limit".to_owned());
        }
        let mut hits = vec![
            Hit {
                file_id: 0,
                revision: 0,
                ordinal: 0,
                score: 0.0
            };
            limit
        ];
        let mut count = 0usize;
        self.call(|raw, error| unsafe {
            m4_rust_file_search(
                raw,
                query.as_ptr(),
                query.len(),
                limit,
                scan_cap,
                hits.as_mut_ptr(),
                hits.len(),
                &mut count,
                error,
                1024,
            )
        })?;
        hits.truncate(count);
        Ok(hits)
    }

    fn call(
        &mut self,
        operation: impl FnOnce(*mut RawIndex, *mut c_char) -> c_int,
    ) -> Result<(), String> {
        let mut error = [0 as c_char; 1024];
        let rc = operation(self.raw, error.as_mut_ptr());
        if rc == 0 {
            Ok(())
        } else {
            Err(read_error(&error))
        }
    }
}

impl Drop for Adapter {
    fn drop(&mut self) {
        unsafe { m4_rust_file_close(self.raw) }
    }
}

fn read_error(error: &[c_char]) -> String {
    unsafe { CStr::from_ptr(error.as_ptr()) }
        .to_string_lossy()
        .into_owned()
}
