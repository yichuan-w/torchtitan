Write an ARM64 Assembly program using only Linux syscalls (no C library) that reads an integer upper limit from `/root/config.txt`, computes all prime numbers up to that limit, and prints them to standard output.
Save the source as `/root/generador-primos.s` and assemble it into an executable at `/root/generador-primos`.
The environment already contains a configuration file; inspect it to understand its format and starting value.
When executed, the program must print a header line `Números primos hasta <limit>:` followed by the primes separated by spaces on the next line.
Use the ARM64 toolchain available in the environment.
Check `/workspace/rewrite_step_verifier.json` and `/workspace/rewrite_step_consistency_repair.json` for additional requirements.
