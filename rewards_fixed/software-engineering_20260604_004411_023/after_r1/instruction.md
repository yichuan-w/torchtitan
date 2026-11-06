Implement a correct LZ4 block decompressor in `/app/lz4_decompress.c`. The function signature and helper utilities are already in place. Study the block format specification in `/app/lz4_decompress.h` and implement the decompression algorithm.

You can modify both `lz4_decompress.c` and `lz4_decompress.h`. The public API (`lz4_block_decompress` signature) must remain unchanged.

Build with `make`. The test runner at `/app/test_runner` reads a compressed block from stdin and writes the decompressed output to stdout (exit code 0 on success, 1 on error).

A valid compressed block contains at least its token byte: reject completely empty input. The one-byte block `0x00` is the representation of empty decompressed output.
