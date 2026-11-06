import sqlite3, os, random

db_path = '/app/data.db'
if os.path.exists(db_path):
    os.remove(db_path)

conn = sqlite3.connect(db_path)
conn.execute('PRAGMA secure_delete = OFF')
conn.execute('PRAGMA journal_mode = DELETE')

conn.execute('''CREATE TABLE employees (
    id INTEGER PRIMARY KEY,
    name TEXT,
    department TEXT,
    salary INTEGER,
    hire_date TEXT,
    email TEXT
)''')

# Generate many records to create a multi-page b-tree with interior pages
departments = ['Engineering', 'Marketing', 'Sales', 'HR', 'Finance', 'Design', 'Legal', 'Operations']
first_names = ['Alice', 'Bob', 'Carol', 'Dan', 'Eve', 'Frank', 'Grace', 'Henry',
               'Iris', 'Jack', 'Kate', 'Leo', 'Maria', 'Nate', 'Olivia', 'Paul',
               'Quinn', 'Rachel', 'Sam', 'Tina', 'Uma', 'Victor', 'Wendy', 'Xavier',
               'Yara', 'Zack', 'Amy', 'Brian', 'Chloe', 'Derek', 'Emily', 'Fred',
               'Gwen', 'Hank', 'Ivy', 'Jake', 'Kara', 'Liam', 'Maya', 'Noah',
               'Olga', 'Peter', 'Quincy', 'Rose', 'Steve', 'Tara', 'Ulysses']
last_names = ['Johnson', 'Smith', 'Williams', 'Brown', 'Martinez', 'Lee', 'Kim',
              'Wilson', 'Chen', 'Turner', 'Adams', 'Garcia', 'Singh', 'Robinson',
              'Park', 'Diaz', 'Foster', 'Tan', 'Kwon', 'Liu', 'Mendez', 'Rossi',
              'Anders', 'Wong', 'Patel', 'Murphy', 'Davis', 'Miller']

random.seed(42)
for i in range(1, 301):
    fn = first_names[(i * 7) % len(first_names)]
    ln = last_names[(i * 13) % len(last_names)]
    name = f"{fn} {ln}"
    dept = departments[(i * 3) % len(departments)]
    salary = random.randint(50000, 150000)
    year = random.randint(2015, 2023)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    hire_date = f"{year}-{month:02d}-{day:02d}"
    email = f"{fn.lower()}.{ln.lower()}{i}@corp.com"
    conn.execute('INSERT INTO employees VALUES (?,?,?,?,?,?)',
                 (i, name, dept, salary, hire_date, email))
conn.commit()

# Delete records scattered across multiple pages
delete_ids = [5, 23, 47, 72, 98, 123, 156, 189, 201, 234, 267, 288]
conn.execute(f"DELETE FROM employees WHERE id IN ({','.join(map(str, delete_ids))})")
conn.commit()

remaining = conn.execute('SELECT count(*) FROM employees').fetchone()[0]
print(f'Database created: {remaining} live records, {len(delete_ids)} deleted')
print(f'Total pages: {os.path.getsize(db_path) // 4096}')
conn.close()
