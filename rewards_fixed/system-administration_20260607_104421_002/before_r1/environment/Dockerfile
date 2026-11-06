FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install nothing extra - the solver writes a script using available tools
# The verifier base image has Python, bash, awk, grep, etc.

# Copy the access log for analysis
COPY files/access.log /app/logs/access.log

# Apache 2.4.65 source for the exact combined-log behavior represented above.
COPY files/httpd-2.4.65/ /usr/local/share/httpd-2.4.65/
RUN echo '79112f1303d73f800a9c800e7826e68f3233e5cb29d20354951dec8449efccb6  /usr/local/share/httpd-2.4.65/protocol.c' | sha256sum -c - \
 && echo 'a996263c3dd147886ad92bd0826d1bea5e7f8c50c91aa5413a121cb3f8a612bd  /usr/local/share/httpd-2.4.65/mod_log_config.c' | sha256sum -c -

# Create the bin directory for the solver's script
RUN mkdir -p /app/logs /app/bin
