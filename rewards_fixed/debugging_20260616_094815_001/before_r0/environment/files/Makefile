CC      = gcc
CFLAGS  = -Wall -Wextra -g -O0
LDLIBS  = -lm
TARGET  = imgproc
SRCS    = imgproc.c utils.c
OBJS    = $(SRCS:.c=.o)

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(LDLIBS)

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(TARGET) $(OBJS)