FROM ubuntu:24.04
WORKDIR /app

RUN apt-get update && apt-get install -y gcc python3 && rm -rf /var/lib/apt/lists/*

COPY files/vm.c /app/
COPY files/assembler.py /app/
