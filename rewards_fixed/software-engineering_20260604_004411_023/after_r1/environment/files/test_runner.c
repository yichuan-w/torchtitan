#include "lz4_decompress.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*
 * Simple test runner: reads compressed data from stdin, decompresses,
 * writes result to stdout. Returns 0 on success, 1 on error.
 */
int main(void) {
    /* Read all of stdin into a buffer */
    size_t buf_cap = 16 * 1024 * 1024;  /* 16 MB max */
    uint8_t *src = malloc(buf_cap);
    if (!src) { fprintf(stderr, "malloc failed\n"); return 1; }

    size_t src_size = 0;
    while (src_size < buf_cap) {
        size_t n = fread(src + src_size, 1, buf_cap - src_size, stdin);
        if (n == 0) break;
        src_size += n;
    }

    /* Allocate output buffer (decompressed data is at most slightly larger,
     * but for safety use a large buffer) */
    size_t dst_cap = 64 * 1024 * 1024;
    uint8_t *dst = malloc(dst_cap);
    if (!dst) { free(src); fprintf(stderr, "malloc failed\n"); return 1; }

    ssize_t result = lz4_block_decompress(src, src_size, dst, dst_cap);
    if (result < 0) {
        free(src);
        free(dst);
        return 1;  /* decompression error */
    }

    /* Write decompressed data to stdout */
    fwrite(dst, 1, (size_t)result, stdout);
    fflush(stdout);

    free(src);
    free(dst);
    return 0;
}
