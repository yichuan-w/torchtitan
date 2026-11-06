I'm recovering data from a legacy embedded system whose storage uses zlib-compressed blobs. The target machine has no compression libraries installed — only gcc and libc. I need a standalone, dependency-free C decompressor.

Write me a C file at /app/deflate.c that decompresses zlib-wrapped DEFLATE streams. I will compile it with:

  gcc -O3 -lm /app/deflate.c

Your program must read compressed data from stdin and write the decompressed data to stdout. It must handle all three DEFLATE block types (stored, fixed Huffman, dynamic Huffman) and the zlib wrapper format (RFC 1950).

Your C program must be <5000 bytes.

I have provided a sample compressed file at /app/sample.zlib for testing. Run it like:

  cat /app/sample.zlib | ./a.out