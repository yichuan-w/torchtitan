FROM debian:12

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates curl gdb wget && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /data

# Download the crackme binary (CTF educational binary from Monologix)
RUN curl -fsSL \
    "https://github.com/Monologix/Monologix-Crackmes/raw/master/Crackme1-Linux/Crackme-Monologix-Linux" \
    -o /data/Crackme-Monologix-Linux && \
    chmod +x /data/Crackme-Monologix-Linux

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