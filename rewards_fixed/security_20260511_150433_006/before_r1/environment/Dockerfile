FROM ubuntu:24.04

# Install forensic analysis tools and ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    sleuthkit \
    extundelete \
    foremost \
    binwalk \
    xxd \
    file \
    util-linux \
    e2fsprogs \
    grep \
    gawk \
    sed \
    coreutils \
    findutils \
    ca-certificates \
    python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and run the forensic image setup script
COPY files/setup_forensic_image.sh /tmp/setup_forensic_image.sh
RUN chmod +x /tmp/setup_forensic_image.sh && \
    bash /tmp/setup_forensic_image.sh && \
    rm /tmp/setup_forensic_image.sh

