-- Seed table for second PostgreSQL backend (testdb_pg2) — subscriber
-- Must have the same schema as the publisher table for logical replication
CREATE TABLE IF NOT EXISTS items_pg (
    id   SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    val  INT          NOT NULL DEFAULT 0
);

-- Own seed data
CREATE TABLE IF NOT EXISTS products_pg (
    id    SERIAL PRIMARY KEY,
    name  VARCHAR(100)   NOT NULL UNIQUE,
    price NUMERIC(10,2)  NOT NULL DEFAULT 0.00
);
INSERT INTO products_pg (name, price) VALUES ('widget', 9.99), ('gadget', 19.99), ('thingamajig', 4.99)
ON CONFLICT (name) DO NOTHING;

-- Subscription to publisher (postgres:5432/testdb_pg)
-- CREATE SUBSCRIPTION cannot run inside DO blocks, so run it directly.
-- This only executes on first container init, so no idempotency guard needed.
CREATE SUBSCRIPTION sub_items_pg
  CONNECTION 'host=postgres port=5432 dbname=testdb_pg user=replicator password=replicapass'
  PUBLICATION pub_items_pg;
