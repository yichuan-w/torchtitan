FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install john the ripper for password cracking
RUN apt-get update && apt-get install -y --no-install-recommends john && \
    rm -rf /var/lib/apt/lists/*

# Copy the leaked hash file and wordlist
COPY files/leaked_hashes.txt /app/leaked_hashes.txt
COPY files/wordlist.txt /app/wordlist.txt

