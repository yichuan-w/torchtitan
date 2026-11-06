FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    curl \
    nano \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the vulnerable URL fetcher
COPY files/url_fetcher.py /app/url_fetcher.py
