I have a compiled Lua 5.1 bytecode file at /app/program.luac that I need to execute, but I don't have a Lua runtime available. Please implement a Lua 5.1 bytecode interpreter so I can run this program.

Write your interpreter as /app/luavm.py. It should accept the bytecode file path as a command-line argument:

```
python3 /app/luavm.py /app/program.luac
```

Running this should parse the bytecode, execute it, and print the expected output to stdout. The program uses print() for output — you'll need to implement at least basic print/tostring support.

The bytecode file follows the standard Lua 5.1 binary chunk format (the format produced by luac). You have full network access to research the format and instruction set.