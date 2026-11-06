import struct

records = [
    (1001, b'Widget A', 19.99, 150, 1460),
    (1002, b'Gadget B', 29.99, 75, 1475),
    (1003, b'Tool C', 149.50, 30, 1476),
    (1004, b'Part D', 5.99, 500, 1477),
    (1005, b'Kit E', 89.00, 45, 1478),
    (1006, b'Supply F', 12.49, 200, 1479),
    (1007, b'Device G', 249.99, 20, 1480),
    (1008, b'Adapter H', 8.75, 300, 1481),
    (1009, b'Module I', 45.00, 60, 1482),
    (1010, b'Cleaner J', 7.99, 400, 1483),
]

with open('/app/inventory.bin', 'wb') as f:
    f.write(b'BIN2')
    f.write(struct.pack('>H', len(records)))
    for rid, name, price, qty, days in records:
        f.write(struct.pack('>I', rid))
        f.write(struct.pack('>B', len(name)))
        f.write(name)
        f.write(struct.pack('>f', price))
        f.write(struct.pack('>I', qty))
        f.write(struct.pack('>I', days))

print('Binary file created with variable-length records')
