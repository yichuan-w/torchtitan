A colleague was working on a C project in `/app` that uses flex/bison for parsing but left it in a broken state. The project should build an executable called `parser` that reads a simple data format and prints its AST representation.

The build fails when running `make -j$(nproc)` (parallel build). It seems to work with `make -j1` sometimes, but not reliably. There are also linker errors about undefined references.

Fix the build system so that:
1. `make -j$(nproc)` consistently succeeds from a clean state (`make clean && make -j$(nproc)`)
2. The resulting `parser` executable works correctly: `./parser test_input.dat` should parse the file and print an AST
3. Incremental rebuilds work correctly after editing source files (the project should detect changes to headers and regenerate appropriately)
