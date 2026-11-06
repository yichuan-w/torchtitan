# Makefile for binparse library
CC = gcc
CFLAGS = -Wall -Wextra -O2 -g
LDFLAGS = 

SRCS = binparse.c binparse_test.c
OBJS = $(SRCS:.c=.o)
TARGET = binparse_test

.PHONY: all clean

all: $(TARGET)

$(TARGET): binparse.c binparse_test.c binparse.h
	$(CC) $(CFLAGS) -o $@ binparse.c binparse_test.c $(LDFLAGS)

clean:
	rm -f $(TARGET) $(OBJS)

# Debug build target (for comparison)
debug: CFLAGS = -Wall -Wextra -O0 -g
debug: clean all
