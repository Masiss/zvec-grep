#define _GNU_SOURCE
#include <dlfcn.h>
#include <pthread.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Mirrors zvec-rust-sys 0.7.2's repr(C) zvec_write_result_t. */
typedef struct {
    uint32_t code;
    const char *message;
} zvec_write_result_t;

typedef uint32_t (*delete_with_results_fn)(
    void *collection,
    const char *const *pks,
    size_t pk_count,
    zvec_write_result_t **results,
    size_t *result_count);

static pthread_once_t resolve_once = PTHREAD_ONCE_INIT;
static delete_with_results_fn real_delete_with_results;

static void resolve_real_symbol(void) {
    void *symbol = dlsym(RTLD_NEXT, "zvec_collection_delete_with_results");
    memcpy(&real_delete_with_results, &symbol, sizeof(real_delete_with_results));
}

uint32_t zvec_collection_delete_with_results(
    void *collection,
    const char *const *pks,
    size_t pk_count,
    zvec_write_result_t **results,
    size_t *result_count) {
    pthread_once(&resolve_once, resolve_real_symbol);
    if (real_delete_with_results == NULL) {
        fprintf(stderr, "ZVEC_TEST_FAULT_ERROR: could not resolve native delete symbol\n");
        return 10; /* ZVEC_ERROR_UNKNOWN */
    }

    const char *enabled = getenv("ZVEC_TEST_FAULT_INJECT_DELETE");
    if (enabled == NULL || strcmp(enabled, "1") != 0 || pks == NULL || pk_count != 2) {
        return real_delete_with_results(
            collection, pks, pk_count, results, result_count);
    }

    /*
     * Let zvec really delete the first key. Use a deliberately missing second
     * key so the native library returns an owned NotFound result/message while
     * leaving the real second document untouched. Change only that result code
     * to InternalError; zvec_rust still frees the native-owned result normally.
     */
    const char *forwarded_pks[2] = {pks[0], "native-fault-injected-missing-pk"};
    const uint32_t status = real_delete_with_results(
        collection, forwarded_pks, 2, results, result_count);
    if (status != 0 || results == NULL || *results == NULL || result_count == NULL ||
        *result_count != 2) {
        fprintf(stderr,
                "ZVEC_TEST_FAULT_NOT_INJECTED: native status=%u result_count=%zu\n",
                status,
                result_count == NULL ? 0 : *result_count);
        return status;
    }

    if ((*results)[0].code != 0 || (*results)[1].code != 1) {
        fprintf(stderr,
                "ZVEC_TEST_FAULT_NOT_INJECTED: native result codes=%u,%u\n",
                (*results)[0].code,
                (*results)[1].code);
        return status;
    }

    (*results)[1].code = 8; /* ZVEC_ERROR_INTERNAL_ERROR */
    fprintf(stderr,
            "ZVEC_TEST_FAULT_INJECTED: first delete is native-success; second status changed NotFound->InternalError\n");
    return status;
}

_Static_assert(sizeof(real_delete_with_results) == sizeof(void *),
               "POSIX function and object pointers must have equal size here");
_Static_assert(offsetof(zvec_write_result_t, message) >= sizeof(uint32_t),
               "unexpected zvec_write_result_t ABI layout");
