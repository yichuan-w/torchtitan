CC = gcc
CFLAGS = -Wall -Wextra -std=c11 -O2 -g
LDFLAGS =

# Build the decompressor as a shared library
liblz4dec.a: lz4_decompress.o
	ar rcs $@ $^

lz4_decompress.o: lz4_decompress.c lz4_decompress.h
	$(CC) $(CFLAGS) -c -o $@ $<

# Test runner that links the decompressor
test_runner: test_runner.c liblz4dec.a
	$(CC) $(CFLAGS) -o $@ test_runner.c -L. -llz4dec

.PHONY: clean
clean:
	rm -f *.o liblz4dec.a test_runner
