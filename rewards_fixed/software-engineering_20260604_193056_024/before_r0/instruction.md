Implement the compressor in `/app/compress.c`.

You are given:
- `/app/compress.h` — the API header
- `/app/decompress.o` — a pre-compiled, working decompressor (no source)
- `/app/compress.c` — stubs for `compress` and `compressBound`

The decompressor implements a custom binary compression format. Study its
behavior by linking it into a shared library and testing it from Python
(using `ctypes`) to understand the wire format. Then implement `compress`
and `compressBound` in `compress.c` so that:

  decompress(compress(input)) == input

for all inputs.

Build `libcompress.so` by linking your code with `decompress.o`:
  gcc -shared -fPIC -o libcompress.so compress.c decompress.o
