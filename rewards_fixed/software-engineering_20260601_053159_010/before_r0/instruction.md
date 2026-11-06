I need a file compression tool that uses Huffman coding. Write me a dependency-free C program that compresses files using Huffman coding and can decompress them back to the original. The compressed format must be entirely self-contained — everything needed to decompress must be in the output file.

Call your program `/app/huff.c`. I will compile with `gcc -O3 -lm -o /app/huff /app/huff.c`.

Run it as:
- `/app/huff c input.txt output.bin` to compress
- `/app/huff d input.bin output.txt` to decompress

Your `/app/huff.c` must be under 4000 bytes.
