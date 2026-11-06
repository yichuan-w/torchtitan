"""
Revenue summary by customer segment and product category.
Joins orders.csv, customers.csv, and products.csv.
"""
import csv
import time
from collections import defaultdict


def load_csv(path):
    """Load a CSV into a list of dicts."""
    rows = []
    with open(path, 'r') as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def find_in_list(lst, key, value):
    """Linear scan through a list to find a matching dict (O(N))."""
    for item in lst:
        if item.get(key) == value:
            return item
    return None


def main():
    t0 = time.time()
    
    # Load all data
    orders = load_csv('/app/orders.csv')
    customers = load_csv('/app/customers.csv')
    products = load_csv('/app/products.csv')
    
    print(f"Loaded {len(orders)} orders, {len(customers)} customers, "
          f"{len(products)} products in {time.time() - t0:.2f}s")
    
    summary = defaultdict(lambda: defaultdict(lambda: {'revenue': 0.0, 'count': 0}))
    
    for order in orders:
        # O(C) linear scan per order to find customer
        customer = find_in_list(customers, 'customer_id', order['customer_id'])
        if customer is None:
            continue
        if customer['segment'] == 'test':
            continue
        
        # O(P) linear scan per order to find product
        product = find_in_list(products, 'product_id', order['product_id'])
        if product is None:
            continue
        
        segment = customer['segment']
        category = product['category']
        line_total = int(order['quantity']) * float(product['list_price'])
        
        summary[segment][category]['revenue'] += line_total
        summary[segment][category]['count'] += 1
    
    # Collect unique segments and categories
    all_segments = sorted(set(c['segment'] for c in customers if c['segment'] != 'test'))
    all_categories = sorted(set(p['category'] for p in products))
    
    # Write output
    with open('/app/output.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['segment', 'category', 'total_revenue', 'order_count', 'avg_order_value'])
        for segment in all_segments:
            for category in all_categories:
                stats = summary[segment].get(category)
                if stats is None:
                    continue
                revenue = round(stats['revenue'], 2)
                count = stats['count']
                avg = round(revenue / count, 2) if count > 0 else 0.0
                writer.writerow([segment, category, revenue, count, avg])
    
    elapsed = time.time() - t0
    print(f"Output written in {elapsed:.2f}s total")
    return elapsed


if __name__ == '__main__':
    main()
