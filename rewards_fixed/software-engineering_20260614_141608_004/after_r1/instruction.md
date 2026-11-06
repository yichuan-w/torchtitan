The file /app/svm is a compiled binary of a stack-based virtual machine that reads assembly files and executes them. The assembler source (asm.c, asm.h) and VM source (vm.c, vm.h) were accidentally deleted, leaving only the compiled binary, the Makefile, and the test programs.

The svm binary has a bug. One test program in /app/test_progs/ crashes or produces incorrect output; the others demonstrate working instruction families.

You have the following available:
- /app/svm — the compiled (buggy) binary
- /app/Makefile — the original Makefile (may need fixes)
- /app/test_progs/ — test programs in assembly format

Use objdump, xxd, or other binary analysis tools to find and fix the bugs in the svm binary. You can fix bugs by patching the binary directly (using xxd + dd, or writing a small C patcher), or by recompiling from a reverse-engineered source.

The command to run a test is:
  ./svm test_progs/test_arith.asm

The test_progs/ directory should not be modified.
