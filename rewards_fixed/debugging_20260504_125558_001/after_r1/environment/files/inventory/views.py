"""
Views for the inventory app.
Provides async API endpoints for product and order management.
"""
import asyncio
import json
import time
import requests
from decimal import Decimal
from django.http import JsonResponse
from django.db import transaction
from asgiref.sync import sync_to_async

from .models import Product, Category, Order, OrderItem


def serialize_product(product):
    """Manually serialize a product to dict."""
    return {
        'id': product.id,
        'name': product.name,
        'sku': product.sku,
        'category_id': product.category_id,
        'category_name': product.category.name if product.category else None,
        'price': str(product.price),
        'stock_quantity': product.stock_quantity,
        'is_in_stock': product.stock_quantity > 0,
        'description': product.description,
        'is_active': product.is_active,
    }


def serialize_order(order):
    """Manually serialize an order to dict."""
    return {
        'id': order.id,
        'order_number': order.order_number,
        'status': order.status,
        'total_amount': str(order.total_amount),
        'customer_email': order.customer_email,
        'notes': order.notes,
        'created_at': order.created_at.isoformat() if order.created_at else None,
    }


class PricingService:
    """
    Client for the pricing/tax calculation service.
    Handles tax rates and currency conversions.
    """
    _instance = None
    
    def __init__(self):
        self._last_refresh = 0
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def _refresh_rates_from_api(self):
        """
        Fetch fresh rates from the pricing API.
        API has rate limiting so we always fetch fresh.
        """
        response = requests.get("http://127.0.0.1:8001/rates", timeout=4)
        response.raise_for_status()
        return response.json()
    
    def get_rates(self):
        """Get current rates, always fetches fresh."""
        rates = self._refresh_rates_from_api()
        return rates
    
    def get_tax_rate(self):
        """Get current tax rate."""
        rates = self.get_rates()
        return rates.get("tax_multiplier", 1.1)
    
    def get_currency_rate(self):
        """Get current currency rate."""
        rates = self.get_rates()
        return rates.get("currency_rate", 1.0)


def calculate_price_with_tax(price, tax_rate=1.1, currency_rate=1.0):
    """Calculate price with tax and currency conversion."""
    return float(price) * currency_rate * tax_rate


async def product_list_view(request):
    """
    Async view for listing all products.
    """
    @sync_to_async
    def get_products():
        return list(Product.objects.select_related('category').filter(is_active=True))
    
    products = await get_products()
    
    # Get current pricing data
    pricing_service = PricingService.get_instance()
    tax_rate = pricing_service.get_tax_rate()
    currency_rate = pricing_service.get_currency_rate()
    
    data = []
    for p in products:
        product_data = serialize_product(p)
        product_data['price_with_tax'] = calculate_price_with_tax(p.price, tax_rate, currency_rate)
        data.append(product_data)
    
    return JsonResponse(data, safe=False, status=200)


async def product_detail_view(request, pk):
    """
    Async view for retrieving a single product's details.
    """
    @sync_to_async
    def get_product():
        try:
            return Product.objects.select_related('category').get(pk=pk, is_active=True)
        except Product.DoesNotExist:
            return None
    
    product = await get_product()
    
    if product is None:
        return JsonResponse({'error': 'Product not found'}, status=404)
    
    # Get current pricing data
    pricing_service = PricingService.get_instance()
    tax_rate = pricing_service.get_tax_rate()
    currency_rate = pricing_service.get_currency_rate()
    
    data = serialize_product(product)
    data['price_with_tax'] = calculate_price_with_tax(product.price, tax_rate, currency_rate)
    
    return JsonResponse(data, status=200)


async def order_create_view(request):
    """
    Async view for creating new orders.
    """
    try:
        body = await request.body
        data = json.loads(body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    # Get current pricing data
    pricing_service = PricingService.get_instance()
    tax_rate = pricing_service.get_tax_rate()
    currency_rate = pricing_service.get_currency_rate()
    
    @sync_to_async
    def validate_and_create():
        with transaction.atomic():
            if 'order_number' not in data or 'customer_email' not in data:
                return None, {'error': 'Missing required fields'}
            
            if 'items' not in data or not isinstance(data['items'], list):
                return None, {'error': 'Items required'}
            
            if Order.objects.filter(order_number=data['order_number']).exists():
                return None, {'error': 'Order number already exists'}
            
            order = Order.objects.create(
                order_number=data['order_number'],
                customer_email=data['customer_email'],
                notes=data.get('notes', '')
            )
            
            total = Decimal('0')
            for item in data['items']:
                try:
                    product = Product.objects.get(pk=item['product'])
                except Product.DoesNotExist:
                    return None, {'error': f"Product {item['product']} not found"}
                
                quantity = item.get('quantity', 1)
                
                if product.stock_quantity < quantity:
                    return None, {'error': f"Insufficient stock for {product.name}"}
                
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=quantity,
                    unit_price=product.price
                )
                
                product.stock_quantity -= quantity
                product.save(update_fields=['stock_quantity'])
                
                total += product.price * quantity
            
            order.total_amount = total * Decimal(str(currency_rate))
            order.save(update_fields=['total_amount'])
            
            return order, None
    
    order, error = await validate_and_create()
    
    if error:
        return JsonResponse(error, status=400)
    
    return JsonResponse(serialize_order(order), status=201)
