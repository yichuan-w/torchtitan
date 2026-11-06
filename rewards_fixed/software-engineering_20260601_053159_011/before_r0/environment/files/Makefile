CC = gcc
CFLAGS = -O2 -Wall
LDFLAGS =

TARGET = kvdb
OBJS = main.o kvdb.o

all: $(TARGET)

kvdb_config.h: gen_config.sh
	chmod +x gen_config.sh
	./gen_config.sh

$(TARGET): $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $^

main.o: main.c kvdb.h
	$(CC) $(CFLAGS) -c -o $@ $<

kvdb.o: kvdb.c kvdb.h
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(TARGET) $(OBJS) kvdb_config.h
