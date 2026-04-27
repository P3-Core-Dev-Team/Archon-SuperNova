-- Synthetic source database schema and seed data for integration testing
-- Tests undeclared foreign key discovery and PII detection

CREATE TABLE customers (
  id BIGSERIAL PRIMARY KEY,
  email TEXT NOT NULL,
  phone TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE products (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  price NUMERIC(10, 2) NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE orders (
  id BIGSERIAL PRIMARY KEY,
  customer_id BIGINT NOT NULL,
  total NUMERIC(10, 2) NOT NULL,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE order_items (
  id BIGSERIAL PRIMARY KEY,
  order_id BIGINT NOT NULL,
  product_id BIGINT NOT NULL,
  quantity INT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY,
  event_type TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Populate customers (with realistic email and phone PII)
INSERT INTO customers (email, phone) VALUES
  ('alice.johnson@example.com', '+14155551234'),
  ('bob.smith@example.com', '+14155555678'),
  ('carol.white@example.com', '+14155559012'),
  ('david.brown@example.com', '+14155553456'),
  ('eve.davis@example.com', '+14155557890'),
  ('frank.miller@example.com', '+14155551111'),
  ('grace.lee@example.com', '+14155552222'),
  ('henry.taylor@example.com', '+14155553333'),
  ('iris.martin@example.com', '+14155554444'),
  ('jack.anderson@example.com', '+14155555555');

-- Populate products
INSERT INTO products (name, price) VALUES
  ('Laptop', 999.99),
  ('Mouse', 29.99),
  ('Keyboard', 79.99),
  ('Monitor', 299.99),
  ('USB Cable', 9.99),
  ('Headphones', 149.99),
  ('Webcam', 89.99),
  ('Desk Lamp', 39.99),
  ('Phone Stand', 19.99),
  ('Desk Pad', 24.99);

-- Populate orders with customer_id references (undeclared FK)
INSERT INTO orders (customer_id, total, notes) VALUES
  (1, 1029.98, 'Contains customer email: alice.johnson@example.com'),
  (2, 459.97, 'Bulk office equipment'),
  (1, 249.98, 'Replacement keyboard'),
  (3, 1379.96, 'Complete workstation'),
  (4, 179.97, 'Peripherals only'),
  (5, 99.98, 'Cables and accessories'),
  (2, 629.95, 'Monitor upgrade'),
  (6, 309.98, 'Standing desk setup'),
  (7, 89.99, 'Single product'),
  (8, 1499.97, 'High-value bulk order'),
  (3, 349.97, 'Replacement items'),
  (9, 209.98, 'Accessories bundle'),
  (10, 819.96, 'Team equipment'),
  (4, 74.98, 'Cable refresh'),
  (5, 259.97, 'Monitor and keyboard');

-- Populate order_items with order_id and product_id references (undeclared FKs)
INSERT INTO order_items (order_id, product_id, quantity) VALUES
  (1, 1, 1), (1, 2, 1), (1, 3, 1),
  (2, 2, 2), (2, 5, 3), (2, 9, 1),
  (3, 3, 1),
  (4, 1, 1), (4, 4, 1), (4, 6, 1), (4, 7, 1),
  (5, 2, 1), (5, 5, 2), (5, 10, 1),
  (6, 5, 5), (6, 9, 2),
  (7, 4, 2),
  (8, 1, 1), (8, 4, 1), (8, 8, 2),
  (9, 7, 1),
  (10, 1, 2), (10, 4, 1), (10, 6, 1), (10, 8, 1),
  (11, 3, 1), (11, 2, 1),
  (12, 6, 1), (12, 9, 1),
  (13, 1, 1), (13, 2, 1), (13, 4, 1), (13, 5, 2),
  (14, 5, 1),
  (15, 3, 1), (15, 4, 1), (15, 2, 1);

-- Populate audit_log with noise data (should be excluded from schema discovery)
INSERT INTO audit_log (event_type) VALUES
  ('user_login'),
  ('user_login'),
  ('data_export'),
  ('user_login'),
  ('configuration_change'),
  ('user_login'),
  ('data_export'),
  ('user_login'),
  ('system_health_check'),
  ('user_login'),
  ('user_login'),
  ('data_export'),
  ('user_login'),
  ('system_health_check'),
  ('configuration_change'),
  ('user_login'),
  ('user_login'),
  ('data_export'),
  ('user_login'),
  ('user_login');

-- Refresh statistics for accurate schema introspection
ANALYZE;
