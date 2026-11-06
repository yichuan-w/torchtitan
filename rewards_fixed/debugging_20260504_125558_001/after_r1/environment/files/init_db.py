#!/usr/bin/env python
"""
Script to initialize the database with sample data.
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
sys.path.insert(0, '/app')
django.setup()

from inventory.models import Category, Product, Order, OrderItem


def create_sample_data():
    """Create sample categories and products."""
    print("Creating sample data...")
    
    # Create categories
    electronics = Category.objects.create(
        name='Electronics',
        description='Electronic devices and accessories'
    )
    clothing = Category.objects.create(
        name='Clothing',
        description='Apparel and fashion items'
    )
    books = Category.objects.create(
        name='Books',
        description='Books and publications'
    )
    
    # Create products
    products_data = [
        # Electronics
        (electronics, 'Laptop Pro 15', 'LAP-PRO-15', 1299.99, 50),
        (electronics, 'Wireless Mouse', 'WLM-001', 29.99, 200),
        (electronics, 'USB-C Hub', 'USB-HUB-7', 49.99, 150),
        (electronics, 'Bluetooth Headphones', 'BT-HD-100', 149.99, 75),
        (electronics, 'Mechanical Keyboard', 'MECH-KB-RGB', 129.99, 100),
        
        # Clothing
        (clothing, 'Cotton T-Shirt', 'CT-TS-BLK-M', 24.99, 500),
        (clothing, 'Denim Jeans', 'DJ-CLSC-32', 79.99, 300),
        (clothing, 'Running Shoes', 'RN-SHOE-42', 119.99, 120),
        
        # Books
        (books, 'Python Programming Guide', 'BK-PY-001', 49.99, 80),
        (books, 'Data Science Handbook', 'BK-DS-002', 39.99, 100),
        (books, 'System Design Primer', 'BK-SD-003', 59.99, 60),
    ]
    
    for category, name, sku, price, stock in products_data:
        Product.objects.create(
            name=name,
            sku=sku,
            category=category,
            price=price,
            stock_quantity=stock,
            description=f'{name} - High quality product',
            is_active=True
        )
    
    print(f"Created {Category.objects.count()} categories")
    print(f"Created {Product.objects.count()} products")


if __name__ == '__main__':
    create_sample_data()
    print("Database initialization complete!")
