I have two compiled Lua 5.1 bytecode files at /app/program.luac and /app/prog2.luac that I need to execute, but I don't have a Lua runtime available. Please complete the Lua 5.1 bytecode interpreter so I can run both programs.

The starter at /app/luavm.py already parses a standard binary chunk into function prototypes. Complete its execution support. It should accept either bytecode file path as a command-line argument, for example:

```
python3 /app/luavm.py /app/program.luac
```

Running it should execute the selected bytecode and print that program's output to stdout. The programs use print() for output — you'll need to implement at least basic print/tostring support.

Both bytecode files follow the standard Lua 5.1 binary chunk format (the format produced by luac). The same interpreter must execute both files; do not special-case either program. You have full network access to research the instruction set.
