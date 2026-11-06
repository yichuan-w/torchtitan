FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install mail server packages
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    postfix \
    dovecot-core \
    dovecot-imapd \
    openssl \
    && rm -rf /var/lib/apt/lists/*

# Create directories for virtual mail storage
RUN mkdir -p /var/mail/vhosts/example.com && \
    chmod -R 755 /var/mail/vhosts

# Ensure SSL directories exist
RUN mkdir -p /etc/ssl/certs /etc/ssl/private

