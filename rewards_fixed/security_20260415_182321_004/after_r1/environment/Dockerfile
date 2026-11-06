FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    curl \
    nano \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest==8.4.1

WORKDIR /app

# Copy the vulnerable URL fetcher
COPY files/url_fetcher.py /app/url_fetcher.py
COPY files/test_outputs.py /tests/test_outputs.py
