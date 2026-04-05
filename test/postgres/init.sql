-- Seed table for first PostgreSQL backend (testdb_pg) — publisher
CREATE TABLE IF NOT EXISTS items_pg (
    id   SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    val  INT          NOT NULL DEFAULT 0
);
INSERT INTO items_pg (name, val) VALUES ('alpha', 1), ('beta', 2), ('gamma', 3)
ON CONFLICT (name) DO NOTHING;

-- Replication user for logical replication
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'replicator') THEN
    CREATE USER replicator WITH REPLICATION PASSWORD 'replicapass';
  END IF;
END
$$;
GRANT ALL PRIVILEGES ON DATABASE testdb_pg TO replicator;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO replicator;

-- Publication for logical replication
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_publication WHERE pubname = 'pub_items_pg') THEN
    CREATE PUBLICATION pub_items_pg FOR TABLE items_pg;
  END IF;
END
$$;
