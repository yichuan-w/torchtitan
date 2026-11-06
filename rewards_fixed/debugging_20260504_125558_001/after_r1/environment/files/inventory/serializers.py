"""
Serializers for the inventory app.
"""
from rest_framework import serializers
from .models import Category, Product, Order, OrderItem


class CategorySerializer(serializers.ModelSerializer):
    product_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Category
        fields = ['id', 'name', 'description', 'product_count', 'created_at']
    
    def get_product_count(self, obj):
        return obj.products.count()


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_details = CategorySerializer(source='category', read_only=True)
    is_in_stock = serializers.SerializerMethodField()
    
    class Meta:
        model = Product
        fields = [
            'id', 'name', 'sku', 'category', 'category_name', 'category_details',
            'price', 'stock_quantity', 'is_in_stock', 'description',
            'is_active', 'created_at', 'updated_at'
        ]
    
    def get_is_in_stock(self, obj):
        return obj.stock_quantity > 0


class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    total_price = serializers.SerializerMethodField()
    
    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'product_name', 'product_sku', 'quantity', 'unit_price', 'total_price']
    
    def get_total_price(self, obj):
        return obj.quantity * obj.unit_price


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    item_count = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = Order
        fields = [
            'id', 'order_number', 'status', 'status_display', 'total_amount',
            'customer_email', 'notes', 'items', 'item_count', 'created_at', 'updated_at'
        ]
    
    def get_item_count(self, obj):
        return obj.items.count()


class OrderCreateSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True)
    
    class Meta:
        model = Order
        fields = ['order_number', 'customer_email', 'notes', 'items']
    
    def create(self, validated_data):
        items_data = validated_data.pop('items')
        order = Order.objects.create(**validated_data)
        total = 0
        for item_data in items_data:
            product = item_data['product']
            quantity = item_data['quantity']
            unit_price = product.price
            OrderItem.objects.create(
                order=order,
                product=product,
                quantity=quantity,
                unit_price=unit_price
            )
            total += unit_price * quantity
        order.total_amount = total
        order.save()
        return order
