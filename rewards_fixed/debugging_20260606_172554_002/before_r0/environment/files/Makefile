CC = gcc
CFLAGS = -Wall -Wextra -g -O2 -fPIC
LDFLAGS = -L. -lparser -Wl,-rpath,.

all: reader

# Build the reader (links against pre-compiled libparser.so)
reader: reader.c
	$(CC) $(CFLAGS) -o reader reader.c $(LDFLAGS)

clean:
	rm -f reader *.o

.PHONY: all clean
