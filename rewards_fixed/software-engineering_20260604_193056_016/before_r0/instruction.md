The parser at /app/parser.c decodes binary files in a custom structured data format. It compiles and runs without crashing on all the test files in /app/testdata/, but some values are decoded incorrectly.

Find and fix all bugs in parser.c so that every file in /app/testdata/ is decoded correctly. The format uses big-endian byte order for multi-byte integers. Build with `make` or `gcc -o parser parser.c -Wall`.
