DATA DICTIONARY
==============

orders.csv
----------
  order_id      - Unique order identifier
  customer_id   - References customers.customer_id
  product_id    - References products.product_id
  quantity      - Number of units purchased
  unit_price    - Actual price charged per unit (may differ from list_price due to promotions)
  order_date    - Date the order was placed (YYYY-MM-DD)
  status        - Order status: completed, cancelled, or refunded

customers.csv
-------------
  customer_id   - Unique customer identifier
  segment       - Customer segment: premium, standard, basic, or test
  signup_date   - Date the customer registered (YYYY-MM-DD)
  region        - Geographic region: North, South, East, or West

products.csv
------------
  product_id    - Unique product identifier
  product_name  - Human-readable product name
  category      - Product category
  list_price    - Standard catalog price (before any discounts)
