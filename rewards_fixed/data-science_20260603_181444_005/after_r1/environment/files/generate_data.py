"""Generate all datasets for the data merge task."""
import csv
import random
import datetime

random.seed(12345)

# --- Products ---
PRODUCTS = [
    (101, 'Laptop Pro', 'Electronics', 999.99),
    (102, 'Wireless Mouse', 'Electronics', 29.99),
    (103, 'USB-C Hub', 'Electronics', 49.99),
    (104, 'Cotton T-Shirt', 'Clothing', 19.99),
    (105, 'Denim Jeans', 'Clothing', 59.99),
    (106, 'Winter Jacket', 'Clothing', 129.99),
    (107, 'Garden Shovel', 'Home & Garden', 24.99),
    (108, 'Ceramic Pot', 'Home & Garden', 15.99),
    (109, 'Plant Fertilizer', 'Home & Garden', 12.99),
    (110, 'Yoga Mat', 'Sports', 34.99),
    (111, 'Resistance Bands', 'Sports', 19.99),
    (112, 'Water Bottle', 'Sports', 14.99),
    (113, 'Mystery Novel', 'Books', 14.99),
    (114, 'Programming Guide', 'Books', 49.99),
    (115, 'History Book', 'Books', 29.99),
    (116, 'Board Game', 'Toys', 29.99),
    (117, 'Building Blocks', 'Toys', 24.99),
    (118, 'Stuffed Animal', 'Toys', 19.99),
    (119, 'Organic Coffee', 'Food', 16.99),
    (120, 'Dark Chocolate', 'Food', 8.99),
    (121, 'Olive Oil Set', 'Food', 34.99),
    (122, 'Face Moisturizer', 'Beauty', 24.99),
    (123, 'Shampoo Set', 'Beauty', 19.99),
    (124, 'Sunscreen Lotion', 'Beauty', 15.99),
    (125, 'Car Air Freshener', 'Automotive', 7.99),
    (126, 'Motor Oil 5W-30', 'Automotive', 29.99),
    (127, 'Windshield Wipers', 'Automotive', 22.99),
    (128, 'Ballpoint Pens', 'Office', 9.99),
    (129, 'Sticky Notes Pack', 'Office', 7.99),
    (130, 'Desk Organizer', 'Office', 24.99),
]

with open('/app/products.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['product_id', 'product_name', 'category', 'list_price'])
    for p in PRODUCTS:
        w.writerow(p)

# --- Customers ---
SEGMENTS = ['premium', 'standard', 'basic', 'test']
SEGMENT_WEIGHTS = [0.15, 0.50, 0.30, 0.05]
start_date = datetime.date(2019, 1, 1)
end_date = datetime.date(2024, 1, 1)
days_range = (end_date - start_date).days

NUM_CUSTOMERS = 5000

with open('/app/customers.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['customer_id', 'segment', 'signup_date', 'region'])
    for cid in range(1, NUM_CUSTOMERS + 1):
        segment = random.choices(SEGMENTS, weights=SEGMENT_WEIGHTS, k=1)[0]
        signup = start_date + datetime.timedelta(days=random.randint(0, days_range))
        region = random.choice(['North', 'South', 'East', 'West'])
        w.writerow([cid, segment, signup.isoformat(), region])

# --- Orders ---
STATUSES = ['completed', 'cancelled', 'refunded']
STATUS_WEIGHTS = [0.82, 0.06, 0.12]
order_start = datetime.date(2023, 1, 1)
order_end = datetime.date(2024, 12, 31)
order_days = (order_end - order_start).days

NUM_ORDERS = 80000

with open('/app/orders.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['order_id', 'customer_id', 'product_id', 'quantity', 'unit_price', 'order_date', 'status'])
    for oid in range(1, NUM_ORDERS + 1):
        cid = random.randint(1, NUM_CUSTOMERS)
        pid = random.choice(PRODUCTS)[0]
        qty = random.randint(1, 5)
        base = next(p[3] for p in PRODUCTS if p[0] == pid)
        price = round(base * random.uniform(0.85, 1.15), 2)
        odate = order_start + datetime.timedelta(days=random.randint(0, order_days))
        status = random.choices(STATUSES, weights=STATUS_WEIGHTS, k=1)[0]
        w.writerow([oid, cid, pid, qty, price, odate.isoformat(), status])

print(f"Generated: {len(PRODUCTS)} products, {NUM_CUSTOMERS} customers, {NUM_ORDERS} orders")
