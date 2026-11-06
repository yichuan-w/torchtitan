#ifndef LZ4_DECOMPRESS_H
#define LZ4_DECOMPRESS_H

#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

/*
 * Decompress an LZ4 compressed block.
 *
 * This function implements decompression of the LZ4 block format as described
 * in the LZ4 Block Format Specification (lz4_block_format.md).
 *
 * The LZ4 block format consists of a series of sequences. Each sequence:
 *   1. A 1-byte token: high 4 bits = literal length, low 4 bits = match length
 *   2. If literal length == 15, additional bytes follow (see below)
 *   3. Literal bytes (uncompressed, copied directly to output)
 *   4. If this is NOT the last sequence:
 *      a. 2-byte offset (little-endian, 1..65535, 0 is invalid)
 *      b. If match length nibble == 15, additional bytes follow the OFFSET
 *      c. Copy match_length bytes from (current_output_position - offset)
 *
 * Variable-length encoding: when a length field is 15, additional bytes are
 * read. Each byte's value (0..255) is added to the total. A value of 255 means
 * another byte follows. There is no practical limit on the number of bytes.
 *
 * Match length base values:
 *   nibble = 0  -> match_length = 4
 *   nibble = 1..14 -> match_length = 4 + nibble
 *   nibble = 15 -> match_length = 19 + sum of additional bytes
 *
 * End of block: the last sequence contains only literals (no offset follows).
 * The last 5 bytes of any input are always literals.
 * A valid compressed block contains at least its token byte. Completely empty
 * input is invalid; a single 0x00 byte represents empty decompressed output.
 *
 * Overlapping matches: when match_length > offset, the copy source and
 * destination overlap. The copy must be performed byte-by-byte so that
 * newly copied bytes are used as source for subsequent copies.
 *
 * Parameters:
 *   src         - pointer to compressed data
 *   src_size    - size of compressed data in bytes
 *   dst         - pointer to output buffer (must be pre-allocated and large enough)
 *   dst_capacity - size of output buffer in bytes
 *
 * Returns:
 *   Number of decompressed bytes on success, or -1 on error (corrupt data,
 *   output buffer too small, invalid offset, etc.)
 */
ssize_t lz4_block_decompress(const void *src, size_t src_size,
                              void *dst, size_t dst_capacity);

#endif /* LZ4_DECOMPRESS_H */
