#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* Validate and decode a UTF-8 byte sequence.
 * Reads bytes from stdin, writes the decoded codepoints (one per line, as U+XXXX)
 * to stdout. Reports validation errors to stderr.
 *
 * According to RFC 3629, a valid UTF-8 sequence must:
 * 1. Use the minimal number of bytes for the codepoint
 * 2. Not encode surrogate pairs (U+D800..U+DFFF)
 * 3. Not exceed U+10FFFF
 * 4. Have correct continuation bytes (10xxxxxx)
 */

#define MAX_CODEPOINT 0x10FFFF
#define SURROGATE_LO  0xD800
#define SURROGATE_HI  0xDFFF

static void print_codepoint(uint32_t cp) {
    printf("U+%04X\n", cp);
}

static int is_continuation(unsigned char c) {
    return (c & 0xC0) == 0x80;
}

/* Decode a single UTF-8 sequence starting at *p.
 * Returns number of bytes consumed, or -1 on error.
 * Sets *cp to the decoded codepoint on success. */
static int decode_utf8_char(const unsigned char *p, int len, uint32_t *cp) {
    if (len < 1) return -1;

    unsigned char c0 = p[0];

    /* ASCII */
    if ((c0 & 0x80) == 0) {
        *cp = c0;
        return 1;
    }

    int bytes;
    uint32_t min_val;

    if ((c0 & 0xE0) == 0xC0) {
        bytes = 2;
        min_val = 0x80;
        *cp = c0 & 0x1F;
    } else if ((c0 & 0xF0) == 0xE0) {
        bytes = 3;
        min_val = 0x800;
        /* BUG: For 3-byte sequences starting with 0xE0, the second byte
         * must be >= 0xA0 (not 0x80) to avoid overlong encoding.
         * This check is missing. */
        *cp = c0 & 0x0F;
    } else if ((c0 & 0xF8) == 0xF0) {
        bytes = 4;
        min_val = 0x10000;
        *cp = c0 & 0x07;
    } else {
        return -1;
    }

    if (len < bytes) return -1;

    for (int i = 1; i < bytes; i++) {
        if (!is_continuation(p[i])) return -1;
        *cp = (*cp << 6) | (p[i] & 0x3F);
    }

    /* BUG: Should check cp < min_val to reject overlong encodings,
     * but the check is missing. Overlong sequences pass validation. */

    /* BUG: Should reject surrogate range (U+D800..U+DFFF),
     * but this check is missing. */
    /* Missing: if (*cp >= SURROGATE_LO && *cp <= SURROGATE_HI) return -1; */

    /* BUG: The max codepoint check uses > instead of >=,
     * allowing U+110000 which is outside valid range.
     * Actually, wait - let me use a subtler bug. The check should be > MAX_CODEPOINT
     * which is correct. But let me instead make the overlong check the main bug. */
    if (*cp > MAX_CODEPOINT) return -1;

    return bytes;
}

int main(void) {
    unsigned char buf[4096];
    size_t nread = fread(buf, 1, sizeof(buf), stdin);
    if (nread == 0) {
        fprintf(stderr, "No input\n");
        return 1;
    }

    size_t pos = 0;
    int errors = 0;

    while (pos < nread) {
        uint32_t cp;
        int consumed = decode_utf8_char(buf + pos, nread - pos, &cp);

        if (consumed < 0) {
            fprintf(stderr, "Error at byte %zu: invalid sequence\n", pos);
            errors++;
            pos++;
        } else {
            print_codepoint(cp);
            pos += consumed;
        }
    }

    if (errors > 0) {
        fprintf(stderr, "Total errors: %d\n", errors);
        return 1;
    }
    return 0;
}
