# Makefile for numutils library
# 
# Numerical Utilities Library
# Version 1.2.3
#
# Installation:
#   make
#   make install
#
# To build as shared library:
#   make shared
#   make install-shared

CC = gcc
AR = ar
RANLIB = ranlib

PREFIX = /usr/local
LIBDIR = $(PREFIX)/lib
INCLUDEDIR = $(PREFIX)/include

# Compiler flags - legacy defaults
CFLAGS = -O2
LDFLAGS = -lm

# Source files
SRCS = numutils.c numutils_io.c
OBJS = numutils.o numutils_io.o

# Library names
LIB_STATIC = libnumutils.a
LIB_SHARED = libnumutils.so.1.2.3
LIB_SHARED_LINK = libnumutils.so

# Default target
all: $(LIB_STATIC)

# Static library
$(LIB_STATIC): $(OBJS)
	$(AR) rcs $@ $(OBJS)
	$(RANLIB) $@

# Shared library
shared: $(LIB_SHARED)

$(LIB_SHARED): $(OBJS)
	$(CC) -shared -o $@ $(OBJS) $(LDFLAGS)

# Object files
.c.o:
	$(CC) $(CFLAGS) -c $< -o $@

# Installation
install: $(LIB_STATIC)
	install -d $(LIBDIR)
	install -d $(INCLUDEDIR)
	install -m 644 $(LIB_STATIC) $(LIBDIR)
	install -m 644 numutils.h $(INCLUDEDIR)

install-shared: $(LIB_SHARED)
	install -d $(LIBDIR)
	install -d $(INCLUDEDIR)
	install -m 755 $(LIB_SHARED) $(LIBDIR)
	ln -sf $(LIB_SHARED) $(LIBDIR)/$(LIB_SHARED_LINK)
	install -m 644 numutils.h $(INCLUDEDIR)

# Clean
clean:
	rm -f $(OBJS) $(LIB_STATIC) $(LIB_SHARED)

# Dependencies
numutils.o: numutils.c numutils.h
numutils_io.o: numutils_io.c numutils.h

.PHONY: all shared install install-shared clean
