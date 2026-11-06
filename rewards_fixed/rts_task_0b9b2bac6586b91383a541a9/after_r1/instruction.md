Complete the starter ARM64 Assembly source at `/root/generador-primos.s` using only Linux syscalls (no C library). It already handles configuration parsing and output formatting; implement the computation that prints all prime numbers up to the configured integer limit.
Assemble it into an executable at `/root/generador-primos`.
The environment already contains a configuration file; inspect it to understand its format and starting value.
When executed, the program must print a header line `Números primos hasta <limit>:` followed by the primes separated by spaces on the next line.
Use the ARM64 toolchain available in the environment.
