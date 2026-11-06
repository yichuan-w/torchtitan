FROM python:3.9

# Install basic tools
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Make bash the default /bin/sh so that solve.sh `source` builtin works
RUN ln -sf /bin/bash /bin/sh

# Ensure pip and build tools are available
RUN pip install --no-cache-dir --upgrade pip wheel setuptools build

# Copy the application source code to the expected location
COPY pkg_src/ /root/pkg_src/

# Create output directory expected by solve.sh
RUN mkdir -p /app

# Add stale environment index required by the task
RUN echo "pip==20.0.2" > /root/env_index_previous.txt && \
    echo "setuptools==49.0.0" >> /root/env_index_previous.txt && \
    echo "wheel==0.34.2" >> /root/env_index_previous.txt && \
    echo "requests==2.25.0" >> /root/env_index_previous.txt && \
    echo "urllib3==1.26.0" >> /root/env_index_previous.txt

# Set HOME explicitly and default working directory
ENV HOME=/root
WORKDIR /app

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