#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct m4_rust_file_index m4_rust_file_index;
typedef struct m4_rust_file_doc {
  const uint8_t *text;
  size_t text_len;
} m4_rust_file_doc;
typedef struct m4_rust_file_hit {
  uint64_t file_id, revision;
  uint32_t ordinal;
  float score;
} m4_rust_file_hit;
// Visibility values: 0=not indexed, 1=staging, 2=indexed; found=0 means absent.
typedef struct m4_rust_file_state {
  uint64_t revision;
  uint32_t visibility, expected, acknowledged, retained_docs, found;
} m4_rust_file_state;
m4_rust_file_index *m4_rust_file_open(const char *, char *, size_t);
void m4_rust_file_close(m4_rust_file_index *);
int m4_rust_file_begin(m4_rust_file_index *, uint64_t, uint64_t, uint32_t,
                       char *, size_t);
int m4_rust_file_append(m4_rust_file_index *, uint64_t, uint64_t, uint32_t,
                        const m4_rust_file_doc *, size_t, char *, size_t);
int m4_rust_file_publish(m4_rust_file_index *, uint64_t, uint64_t, char *,
                         size_t);
int m4_rust_file_rollback(m4_rust_file_index *, uint64_t, uint64_t, char *,
                          size_t);
int m4_rust_file_delete(m4_rust_file_index *, uint64_t, uint64_t, char *,
                        size_t);
int m4_rust_file_get_state(m4_rust_file_index *, uint64_t, m4_rust_file_state *,
                           char *, size_t);
int m4_rust_file_search(m4_rust_file_index *, const uint8_t *, size_t, size_t,
                        uint32_t, m4_rust_file_hit *, size_t, size_t *, char *,
                        size_t);
#ifdef __cplusplus
}
static_assert(sizeof(m4_rust_file_hit) == 24, "FFI layout");
static_assert(sizeof(m4_rust_file_state) == 32, "FFI state layout");
#endif
// Research-only. begin hides a file, append uses fsync-enabled M4 WAL, publish
// exposes one revision. Rust must commit retained native data before publish
// and reconcile cross-store crash gaps. M4PF1 errors fail closed.
