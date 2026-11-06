CC = gcc
CFLAGS = -Wall -Wextra -O2

.PHONY: all clean

all: svm

svm: main.o asm.o
	$(CC) $(CFLAGS) -o svm main.o asm.o vm.o

main.o: main.c vm.h asm.h
	$(CC) $(CFLAGS) -c main.c

asm.o: asm.c asm.h
	$(CC) $(CFLAGS) -c asm.c

vm.o: vm.c vm.h
	$(CC) $(CFLAGS) -c vm.c

clean:
	rm -f *.o svm