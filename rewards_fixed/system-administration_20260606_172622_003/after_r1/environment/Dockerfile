FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install rsyslog with GnuTLS support, certtool, logrotate, and utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    rsyslog \
    rsyslog-gnutls \
    gnutls-bin \
    logrotate \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /usr/local/sbin \
    && cp -a /usr/sbin/rsyslogd /usr/local/sbin/rsyslogd \
    && ln -sf /usr/local/sbin/rsyslogd /usr/sbin/rsyslogd

# Ensure rsyslog is running with basic config
RUN mkdir -p /run/rsyslog

# Copy a minimal base rsyslog config that ensures the service starts
# but has NO TLS configuration - the solver must add that
COPY files/rsyslog-base.conf /etc/rsyslog.conf

# Start rsyslog by default
CMD ["rsyslogd", "-n"]
