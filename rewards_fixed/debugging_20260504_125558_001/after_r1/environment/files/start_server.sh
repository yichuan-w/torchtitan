#!/bin/bash
# Startup script for the inventory API server

cd /app

# Initialize database
python manage.py migrate --run-syncdb

# Create sample data
python init_db.py

# Start the local pricing service
python pricing_service.py &

# Start the server
exec uvicorn asgi:application --host 127.0.0.1 --port 8000 --workers 1
