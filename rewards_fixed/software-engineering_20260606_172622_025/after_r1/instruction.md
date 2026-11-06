The CHIP-8 interpreter in this directory is based on a widely-circulated reference implementation but does not match the behavior of the original 1977 COSMAC VIP interpreter. Several test ROMs are provided that expose deviations from the original specification.

Build the emulator with `make`, run each test ROM, compare the register dumps against the original CHIP-8 specification, locate the incorrect implementation in `chip8.c`, and fix the code so all five test ROMs produce correct results.

The emulator prints final register state to stdout in this format:
```
V0=XX V1=XX ... VF=XX I=XXXX PC=XXXX
```

The `VF` register is used as a flag for arithmetic overflow, borrow, and shift carry. The `I` register is the 16-bit memory address index.

Do not modify the test ROMs.
