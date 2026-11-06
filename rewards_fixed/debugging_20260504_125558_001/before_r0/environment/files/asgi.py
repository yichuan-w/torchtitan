"""
ASGI config for the inventory_api project.
"""
import os
import django
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

application = get_asgi_application()
