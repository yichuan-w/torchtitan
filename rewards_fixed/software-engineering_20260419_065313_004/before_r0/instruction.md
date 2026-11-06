The file `/app/vm.c` is a stack-based bytecode virtual machine interpreter. Study the source code carefully.

Create a bytecode program `/app/prog.bin` (under 300 bytes) that computes the modular multiplicative inverse of the input N modulo 65537. That is, find X such that (N × X) mod 65537 = 1, where 0 < N < 65537.

If no inverse exists (when GCD(N, 65537) ≠ 1), output 0. Otherwise, output the inverse X in the range 1 to 65536.

Note: 65537 is prime (a Fermat prime), so every N from 1 to 65536 has a unique inverse.

IMPORTANT: You must use the extended Euclidean algorithm. Brute-force search will exceed the 10 million instruction limit for most inputs.

For example:
- Input 1 → output 1
- Input 2 → output 32769 (2 × 32769 = 65538 = 1 mod 65537)
- Input 65536 → output 65536
- Input 0 → output 0
