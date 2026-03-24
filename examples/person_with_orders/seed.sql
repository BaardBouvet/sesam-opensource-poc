CREATE TABLE IF NOT EXISTS person (
  _id text PRIMARY KEY,
  name text NOT NULL,
  age numeric,
  updated_at text
);

CREATE TABLE IF NOT EXISTS orders (
  _id numeric PRIMARY KEY,
  amount numeric,
  cust_id text,
  updated_at text
);

CREATE TABLE IF NOT EXISTS person_with_orders (
  _id text PRIMARY KEY,
  name text NOT NULL,
  type text,
  age numeric,
  order_count numeric,
  orders jsonb NOT NULL DEFAULT '[]'::jsonb,
  updated_at text
);

INSERT INTO person (_id, name, age, updated_at)
VALUES
  ('p1', 'Alice', 31, '2026-03-24T08:00:00Z'),
  ('p2', 'Bob', 39, '2026-03-24T09:00:00Z')
ON CONFLICT (_id) DO NOTHING;

INSERT INTO orders (_id, amount, cust_id, updated_at)
VALUES
  (1001, 120, 'p1', '2026-03-24T08:10:00Z'),
  (1002, 250, 'p1', '2026-03-24T08:20:00Z'),
  (1003, 75, 'p2', '2026-03-24T09:10:00Z')
ON CONFLICT (_id) DO NOTHING;

INSERT INTO person_with_orders (_id, name, type, age, order_count, orders, updated_at)
VALUES
  (
    'p1',
    'Alice',
    'customer',
    31,
    2,
    '[{"order_id": 1001, "amount": 120}, {"order_id": 1002, "amount": 250}]'::jsonb,
    '2026-03-24T10:00:00Z'
  ),
  (
    'p2',
    'Bob',
    'customer',
    39,
    1,
    '[{"order_id": 1003, "amount": 75}]'::jsonb,
    '2026-03-24T10:00:00Z'
  )
ON CONFLICT (_id) DO NOTHING;
