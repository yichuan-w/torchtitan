FROM ubuntu:24.04

WORKDIR /app

# Install supervisor and other dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor \
    redis-server \
    nginx \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip3 install --no-cache-dir redis==5.0.8 --break-system-packages

# Create application directories
RUN mkdir -p /opt/myapp/bin \
    /opt/myapp/lib \
    /opt/myapp/config \
    /opt/myapp/static \
    /var/log/myapp \
    /var/log/supervisor \
    /var/run/supervisor \
    /var/run/redis \
    /var/run/nginx

# Copy configuration files
COPY files/supervisord.conf /opt/myapp/config/supervisord.conf
COPY files/redis.conf /opt/myapp/config/redis.conf
COPY files/nginx.conf /opt/myapp/config/nginx.conf
COPY files/worker.py /opt/myapp/bin/worker
COPY files/api-server.py /opt/myapp/bin/api-server

# Make Python scripts executable
RUN chmod +x /opt/myapp/bin/worker /opt/myapp/bin/api-server

# Create static index file
RUN echo '<!DOCTYPE html><html><body><h1>MyApp</h1></body></html>' > /opt/myapp/static/index.html
