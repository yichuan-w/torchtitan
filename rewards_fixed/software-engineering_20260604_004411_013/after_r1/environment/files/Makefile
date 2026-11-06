# MiniLisp - A small Lisp interpreter
# Build with: make

CC = gc

CFLAGS = -Wall -g -std=c99

SRCDIR = src
INCDIR = include

SRCS = $(SRCDIR)/types.c $(SRCDIR)/reader.c $(SRCDIR)/printer.c $(SRCDIR)/eval.c $(SRCDIR)/env.c $(SRCDIR)/main.c
OBJS = $(SRCS:.c=.o)

TARGET = lisp

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^

$(SRCDIR)/%.o: $(SRCDIR)/%.c
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(OBJS) $(TARGET)
