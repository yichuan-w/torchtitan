Build the C project at `/app` with `make` to produce `/app/build/strtool`. The build fails — fix the build system.

The code also has a logic bug. After fixing both issues, verify:
- `/app/build/strtool "a,b,c" ","` → 3 parts: `a`, `b`, `c`
- `/app/build/strtool "hello world" " "` → 2 parts: `hello`, `world`
- `/app/build/strtool "solo" ","` → 1 part: `solo`

Splitting must preserve empty parts between adjacent delimiters.

The header `include/strlib.h` must not be modified.
