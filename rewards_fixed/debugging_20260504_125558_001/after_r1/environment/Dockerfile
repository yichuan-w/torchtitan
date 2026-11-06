FROM ubuntu:24.04

WORKDIR /app

# Install Python and dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment and install Python packages
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Django and dependencies - versions that work together
RUN pip install --no-cache-dir \
    django==4.2.11 \
    djangorestframework==3.14.0 \
    uvicorn==0.27.1 \
    asgiref==3.7.2 \
    aiohttp==3.10.11 \
    requests==2.32.3

# Copy application files
COPY files/settings.py /app/settings.py
COPY files/asgi.py /app/asgi.py
COPY files/urls.py /app/urls.py
COPY files/manage.py /app/manage.py
COPY files/init_db.py /app/init_db.py
COPY files/start_server.sh /app/start_server.sh
COPY files/load_test.py /app/load_test.py
COPY files/pricing_service.py /app/pricing_service.py
COPY files/inventory/ /app/inventory/

# Make scripts executable
RUN chmod +x /app/start_server.sh

# Initialize the database with sample data
RUN python manage.py migrate --run-syncdb && python init_db.py

