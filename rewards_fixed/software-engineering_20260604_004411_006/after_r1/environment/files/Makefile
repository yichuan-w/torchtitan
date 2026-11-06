CC = gcc
CFLAGS = -Wall -I./include
OBJ = build/strbuf.o build/strsplit.o build/main.o
TARGET = build/strtool

all: $(TARGET)
$(TARGET): $(OBJ); $(CC) -o $@ $<
build/%.o: src/%.c; mkdir -p build; $(CC) $(CFLAGS) -c -o $@ $<
clean:; rm -rf build
.PHONY: all clean
