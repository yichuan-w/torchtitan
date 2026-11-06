Build the legacy `numutils` numerical computing library from source and install it as a shared library to `/usr/local/lib/libnumutils.so` with the header at `/usr/local/include/numutils.h`.

The source code is in `/app/numutils-src/`. It was last maintained around 2005 and builds successfully on older systems with GCC 3.x/4.x, but fails to compile on modern systems.

Your goal is to:
1. Get the library to compile and link on this Ubuntu 24.04 system with GCC 14
2. Install the shared library and header to the system paths above
3. Ensure the library is functional by running the test program: `./test_numutils` from `/app/` should complete successfully

Do not modify any test files or the `test_numutils.c` program.
