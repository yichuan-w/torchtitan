FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Install required packages
RUN apt-get update && apt-get install -y \
    supervisor \
    postgresql \
    postgresql-contrib \
    redis-server \
    nginx \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    procps \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment for the application
RUN python3 -m venv /opt/appenv

# Create application directory
RUN mkdir -p /opt/webapp

WORKDIR /app

# Copy supervisord configuration files with intentional issues
COPY files/supervisord.conf /etc/supervisor/supervisord.conf
COPY files/appdb.conf /etc/supervisor/conf.d/appdb.conf
COPY files/appcache.conf /etc/supervisor/conf.d/appcache.conf
COPY files/appserver.conf /etc/supervisor/conf.d/appserver.conf
COPY files/appworker.conf /etc/supervisor/conf.d/appworker.conf
COPY files/appproxy.conf /etc/supervisor/conf.d/appproxy.conf

# Copy application files
COPY files/app.py /opt/webapp/app.py
COPY files/worker.py /opt/webapp/worker.py
COPY files/init_db.py /opt/webapp/init_db.py
COPY files/nginx.conf /etc/nginx/sites-available/app

# Enable nginx site
RUN ln -sf /etc/nginx/sites-available/app /etc/nginx/sites-enabled/app && \
    rm -f /etc/nginx/sites-enabled/default

# Install pinned application dependencies
RUN /opt/appenv/bin/pip install --no-cache-dir \
    flask==3.0.3 \
    gunicorn==22.0.0 \
    psycopg2-binary==2.9.9 \
    redis==5.0.8 \
    supervisor==4.2.5

# Require clients to authenticate to Redis
RUN sed -i '/^# requirepass /a requirepass cachepass456' /etc/redis/redis.conf
RUN touch /var/log/redis/redis-server.log && chown redis:adm /var/log/redis/redis-server.log

# Set up Redis data directory
RUN mkdir -p /var/lib/redis && chown redis:redis /var/lib/redis

# Set up PostgreSQL runtime directory
RUN mkdir -p /var/run/postgresql && chown postgres:postgres /var/run/postgresql

# Create log directories
RUN mkdir -p /var/log/webapp /var/log/supervisor && \
    chown root:root /var/log/webapp /var/log/supervisor

# Initialize PostgreSQL database using pg_ctlcluster
RUN pg_ctlcluster 16 main start && \
    su - postgres -c "psql -c \"CREATE USER appuser WITH PASSWORD 'apppass123';\"" && \
    su - postgres -c "psql -c \"CREATE DATABASE appdb OWNER appuser;\"" && \
    pg_ctlcluster 16 main stop

# Disable services from auto-starting (supervisord will manage them)
RUN update-rc.d -f supervisor disable 2>/dev/null || true && \
    update-rc.d -f postgresql disable 2>/dev/null || true && \
    update-rc.d -f redis-server disable 2>/dev/null || true && \
    update-rc.d -f nginx disable 2>/dev/null || true && \
    rm -f /etc/init.d/supervisor 2>/dev/null || true
