FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install tools needed to create the FAT16 image
RUN apt-get update && apt-get install -y --no-install-recommends \
    dosfstools \
    mtools \
    && rm -rf /var/lib/apt/lists/*

# Remove sleuthkit to prevent trivial solution with fls/icat
RUN apt-get remove -y sleuthkit 2>/dev/null; \
    apt-get autoremove -y 2>/dev/null; \
    rm -rf /usr/bin/fls /usr/bin/icat /usr/bin/fsstat /usr/bin/istat 2>/dev/null; \
    true

# Generate a FAT16 disk image with deleted files, then corrupt boot sector
COPY files/gen_disk.py /tmp/gen_disk.py
RUN python3 /tmp/gen_disk.py /app/disk.img && rm /tmp/gen_disk.py

