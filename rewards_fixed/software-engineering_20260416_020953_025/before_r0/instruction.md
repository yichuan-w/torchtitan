A binary file parsing library in `libbinparse/` crashes intermittently when compiled with `-O2` optimizations. The library includes a test binary `binparse_test` that processes sample files from `testdata/`.

Build and run the test:
```
cd libbinparse && make clean && make && ./binparse_test testdata/sample1.bin testdata/sample2.bin
```

With the default Makefile (using `-O2`), the test crashes with SIGSEGV. The same code works correctly when compiled with `-O0` or `-O1`.

Diagnose the root cause and fix the library source code so that:
1. The test binary runs successfully with `-O2` optimizations enabled
2. The library correctly parses all test data files without crashes
3. The fix does not simply disable optimizations or change compiler flags

Do not modify the Makefile or test code. Fix the underlying issue in the library source (`binparse.c`).
