FROM ubuntu:24.04

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive

# Install required packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor \
    nginx \
    python3 \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Create log directories (intentionally missing some that are needed)
RUN mkdir -p /var/log/supervisor /var/log/app /var/log/nginx

# Copy configuration files
COPY files/supervisord.conf /etc/supervisor/supervisord.conf
COPY files/backend.conf /etc/supervisor/conf.d/backend.conf
COPY files/nginx.conf /etc/nginx/nginx.conf
COPY files/backend_service.py /app/backend_service.py
COPY files/event_listener.py /app/event_listener.py

# Create a startup script that runs supervisord
RUN echo '#!/bin/bash\nsupervisord -c /etc/supervisor/supervisord.conf\nnginx\nexec sleep infinity' > /app/start.sh && chmod +x /app/start.sh

CMD ["/app/start.sh"]






