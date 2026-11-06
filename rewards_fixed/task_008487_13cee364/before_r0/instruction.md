You are a network engineer analyzing intercepted telemetry traffic from a compromised IoT device. 

You have been provided with a binary capture file located at `/home/user/traffic.bin`. 

Through preliminary reverse engineering of the device's firmware, you've discovered the following about the proprietary protocol:
1. Every packet consists of a **32-byte SHA-256 checksum** followed by an **encrypted payload**.
2. The SHA-256 checksum is calculated over the *decrypted* (plaintext) payload.
3. The encryption is a simple rolling XOR stream cipher. The keystream is generated using a Linear Congruential Generator (LCG) with an 8-bit state: 
   $K_{i+1} = (A \cdot K_i + C) \pmod{256}$
   The $i$-th byte of the encrypted payload is produced by XORing the $i$-th byte of the plaintext payload with $K_i$.
4. The first 4 bytes of the plaintext payload are always the magic header: `TELE` (in ASCII).

Your objective is to:
1. Write a **Rust** program to analyze `/home/user/traffic.bin`.
2. Perform a known-plaintext attack using the 4-byte magic header to recover the first 4 bytes of the keystream ($K_0, K_1, K_2, K_3$).
3. Use those keystream bytes to mathematically derive the unknown LCG parameters $A$ and $C$.
4. Generate the rest of the keystream and decrypt the entire payload.
5. Verify your decryption by computing the SHA-256 hash of the recovered plaintext and ensuring it exactly matches the 32-byte checksum at the start of the file.
6. Extract the plaintext message (which starts with `TELE`) and save it to `/home/user/secret.txt`.

Ensure your Rust program can be compiled and run in the standard environment. Do not guess the parameters via brute-force; derive them algebraically to prove the cryptanalysis.