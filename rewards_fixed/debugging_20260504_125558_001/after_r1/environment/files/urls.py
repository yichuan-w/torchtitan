"""
URL configuration for inventory_api project.
"""
from django.urls import path
from inventory.views import product_list_view, product_detail_view, order_create_view

urlpatterns = [
    path('api/products/', product_list_view, name='product-list'),
    path('api/products/<int:pk>/', product_detail_view, name='product-detail'),
    path('api/orders/', order_create_view, name='order-create'),
]
