FROM debian:12

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        binutils ca-certificates curl file gdb wget xz-utils && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /data

# Download the crackme binary (CTF educational binary from Monologix)
RUN curl -fsSL \
    "https://raw.githubusercontent.com/Monologix/Monologix-Crackmes/07f50bf1dbb6495136fd9f13e83762745d7953a3/Crackme1-Linux/Crackme-Monologix-Linux" \
    -o /data/Crackme-Monologix-Linux && \
    echo "6dd7f2b64a18d015ed60f45d77d7c9c62acb00d1aa632da0eb23448566880fd8  /data/Crackme-Monologix-Linux" | sha256sum -c - && \
    perl -pi -e 's/6571965/9462751/' /data/Crackme-Monologix-Linux && \
    chmod +x /data/Crackme-Monologix-Linux && \
    curl -fsSL "https://github.com/upx/upx/releases/download/v4.2.4/upx-4.2.4-amd64_linux.tar.xz" -o /tmp/upx.tar.xz && \
    echo "75cab4e57ab72fb4585ee45ff36388d280c7afd72aa03e8d4b9c3cbddb474193  /tmp/upx.tar.xz" | sha256sum -c - && \
    tar -xJf /tmp/upx.tar.xz -C /tmp && \
    /tmp/upx-4.2.4-amd64_linux/upx --lzma /data/Crackme-Monologix-Linux && \
    rm -rf /tmp/upx.tar.xz /tmp/upx-4.2.4-amd64_linux

# Create left-over file for cleanup verification
RUN echo 'leftover artifact' > /data/leftover.log

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
SHELL ["/bin/bash", "-c"]

# harbor-agent-runtime: tmux is required by the terminal agent
RUN if command -v tmux >/dev/null 2>&1; then \
      exit 0; \
    elif command -v apt-get >/dev/null 2>&1; then \
      (apt-get update || ( \
        if grep -Eqi 'debian.*(buster|stretch|jessie)' /etc/os-release 2>/dev/null; then \
          sed -i -e 's|http://deb.debian.org/debian|http://archive.debian.org/debian|g' \
                 -e 's|http://security.debian.org/debian-security|http://archive.debian.org/debian-security|g' \
                 -e 's|http://deb.debian.org/debian-security|http://archive.debian.org/debian-security|g' \
                 /etc/apt/sources.list; \
          echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99harbor-archive; \
          apt-get update; \
        else \
          exit 1; \
        fi \
      )) && (apt-get install -y tmux || apt-get install -y --allow-unauthenticated tmux || apt-get install -y --force-yes tmux) && rm -rf /var/lib/apt/lists/*; \
    elif command -v yum >/dev/null 2>&1; then \
      yum install -y tmux && yum clean all; \
    elif command -v dnf >/dev/null 2>&1; then \
      dnf install -y tmux && dnf clean all; \
    elif command -v microdnf >/dev/null 2>&1; then \
      microdnf install -y tmux && microdnf clean all; \
    elif command -v apk >/dev/null 2>&1; then \
      apk add --no-cache tmux; \
    else \
      echo 'WARNING: no supported package manager found to install tmux' >&2; \
    fi

CMD ["bash"]
