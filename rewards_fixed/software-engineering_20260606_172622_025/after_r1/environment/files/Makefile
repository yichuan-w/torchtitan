CC = gcc
CFLAGS = -Wall -Wextra -std=c99 -O2
TARGET = chip8

.PHONY: all clean

all: $(TARGET)

$(TARGET): chip8.c
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f $(TARGET)
