I've been working on a tiny stack-based virtual machine for an embedded project. The VM binary is at `/app/vm` — you run it like `./vm program.bin` and it executes the bytecode.

I only have three example bytecode programs left in `/app/examples/` (with expected outputs in `/app/expected/` — matching filenames, `.txt` instead of `.bin`). These don't cover all instructions, so you'll need to dig into the VM binary itself to figure out the complete encoding. The bytecode files start with a 4-byte magic number; the VM validates this header.

Here's the problem: I lost the assembler. I need to write new bytecode programs but I can't practically hand-assemble binary files. Write a C program at `/app/asm` that takes an assembly source file and writes the corresponding bytecode to stdout. Your assembler must accept this syntax:

- **Comments**: Lines starting with `;` are ignored (may appear anywhere, including after instructions).
- **Instructions**: One per line. Blank lines are ignored.
- **Labels**: A word followed by `:` on its own line defines a label pointing to the next instruction.
- **PUSH**: `PUSH <integer>` — push a signed 32-bit integer onto the stack.
- **Arithmetic**: `ADD`, `SUB`, `MUL`, `DIV`, `MOD` — pop b, pop a, push `a op b`. DIV and MOD truncate toward zero.
- **Comparison**: `EQ`, `LT`, `GT` — pop b, pop a, push 1 if `a op b` is true, else push 0.
- **Stack**: `POP` (discard top), `DUP` (duplicate top), `SWAP` (exchange top two).
- **Output**: `PRINT` — pop and print as integer. `PRCH` — pop and print the low byte as an ASCII character.
- **Control flow**: `JMP <label>`, `JZ <label>`, `JNZ <label>` — JZ/JNZ pop the top of stack first, then conditionally jump.
- **HALT**: Stop execution.

Your assembler must be a standalone C program. Compile it with `gcc -static -o asm asm.c` (the tests compile it this way). It must read the assembly file given as its first argument and write the bytecode to stdout.
