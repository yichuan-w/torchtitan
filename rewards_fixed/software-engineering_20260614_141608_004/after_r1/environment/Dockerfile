FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install build tools needed to compile the binary AND for the solver to use
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    xxd \
    && rm -rf /var/lib/apt/lists/*

# Copy source files temporarily for compilation
COPY files/vm.h /app/
COPY files/vm.c /app/
COPY files/asm.h /app/
COPY files/asm.c /app/
COPY files/main.c /app/

# Compile the buggy binary
RUN gcc -Wall -Wextra -O2 -o svm main.c asm.c vm.c

# Remove source files (they were "accidentally deleted")
RUN rm -f vm.h vm.c asm.h asm.c main.c *.o

# Copy the Makefile (may need fixes) and test programs
COPY files/Makefile /app/
COPY files/NOTES.txt /app/
COPY files/test_progs/ /app/test_progs/

