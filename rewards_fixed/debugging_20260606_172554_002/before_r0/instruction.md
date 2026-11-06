I have a binary file reader at `/app/` that crashes on certain input files. The program uses a shared library `libparser.so` (API in `parser.h`) to parse a custom binary format. The library was provided by another team and I don't have its source code.

There are two sample files:
- `/app/normal.dat` - processes fine
- `/app/malformed.dat` - causes a crash

I need to get this working. You can modify `/app/reader.c` and the `/app/Makefile`, but you cannot change the compiled library `libparser.so` or its header `parser.h`. You can compile with `make` and test with `./reader <file>`.

The binary format is described in parser.h.
