# Makefile for sysinfod
# System Information HTTP Daemon

CC      = gcc
CFLAGS  = -Wall -Wextra -Werror -O2 -D_GNU_SOURCE
LDFLAGS = -lcurl -lpthread
PREFIX  = /usr/local
BINDIR  = $(PREFIX)/bin

TARGET  = sysinfod
SRCS    = sysinfod.c
OBJS    = $(SRCS:.c=.o)

.PHONY: all clean install

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $^ $(LDLIBS)

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

install: $(TARGET)
	install -d $(DESTDIR)$(BINDIR)
	install -m 755 $(TARGET) $(DESTDIR)$(BINDIR)/

clean:
	rm -f $(TARGET) $(OBJS)
