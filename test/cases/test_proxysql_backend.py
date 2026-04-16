#!/usr/bin/env python3
"""Direct SQL through the ProxySQL MySQL frontend, plus multi-server navigation."""

import os
import unittest

from testlib import HAS_PYMYSQL
if HAS_PYMYSQL:
    import pymysql

from testlib import (
    ProxyWebSession, BASE_URL, USERNAME, PASSWORD,
    SERVER, PG_SERVER, DATABASE,
)


@unittest.skipUnless(HAS_PYMYSQL, "pymysql not installed — skipping backend SQL tests")
class TestProxySQL2BackendSQL(unittest.TestCase):
    """SELECT / INSERT / UPDATE / DELETE through ProxySQL MySQL (port 6033).

    Connects as proxyuser2/proxypass2 which is registered in proxysql_mysql's
    runtime_mysql_users and granted on the mysql2 backend's testdb2.
    Table under test: testdb2.products (id INT PK, name VARCHAR, price DECIMAL).
    """

    HOST = os.environ.get("PROXYSQL_MYSQL_HOST", "127.0.0.1")
    PORT = int(os.environ.get("PROXYSQL_MYSQL_PORT", "6033"))
    USER = "proxyuser2"
    PASS = "proxypass2"
    DB   = "testdb2"

    def _conn(self):
        """
        Create a new PyMySQL connection configured for the test backend.
        
        Returns:
            pymysql.connections.Connection: A connected PyMySQL connection to the configured host/port/database with autocommit enabled.
        """
        return pymysql.connect(
            host=self.HOST, port=self.PORT,
            user=self.USER, password=self.PASS,
            database=self.DB, autocommit=True,
        )

    def test_select_returns_rows(self):
        """
        Verify the products table on the ProxySQL2 test backend returns data and includes a product named "widget".
        
        Asserts that a SELECT query against testdb2.products returns at least one row and that one of the returned rows has the name "widget".
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, price FROM products ORDER BY id")
                rows = cur.fetchall()
        self.assertGreater(len(rows), 0, "Expected seed rows in testdb2.products")
        names = [r[1] for r in rows]
        self.assertIn("widget", names)

    def test_insert_row(self):
        inserted_id = None
        try:
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO products (name, price) VALUES (%s, %s)",
                        ("ps2-test-insert", "7.77"),
                    )
                    inserted_id = conn.insert_id()
            self.assertGreater(inserted_id, 0)
            # FOR UPDATE routes to writer (hg1) to avoid replication lag on the reader
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT name, price FROM products WHERE id = %s FOR UPDATE", (inserted_id,)
                    )
                    row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "ps2-test-insert")
            self.assertAlmostEqual(float(row[1]), 7.77, places=2)
        finally:
            # Cleanup
            if inserted_id is not None:
                with self._conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM products WHERE id = %s", (inserted_id,))

    def test_update_row(self):
        row_id = None
        try:
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO products (name, price) VALUES (%s, %s)",
                        ("ps2-test-update", "1.00"),
                    )
                    row_id = conn.insert_id()
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE products SET price = %s WHERE id = %s",
                        ("49.99", row_id),
                    )
                    self.assertEqual(conn.affected_rows(), 1)
            # FOR UPDATE routes to writer (hg1) to avoid replication lag on the reader
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT price FROM products WHERE id = %s FOR UPDATE", (row_id,)
                    )
                    self.assertAlmostEqual(float(cur.fetchone()[0]), 49.99, places=2)
        finally:
            if row_id is not None:
                with self._conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM products WHERE id = %s", (row_id,))

    def test_delete_row(self):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO products (name, price) VALUES (%s, %s)",
                    ("ps2-test-delete", "0.01"),
                )
                row_id = conn.insert_id()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM products WHERE id = %s", (row_id,))
                self.assertEqual(conn.affected_rows(), 1)
        # FOR UPDATE routes to writer (hg1) — ensures we verify against the
        # authoritative source, not a potentially-lagging replica
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM products WHERE id = %s FOR UPDATE", (row_id,))
                self.assertIsNone(cur.fetchone())


class TestMultiServer(unittest.TestCase):
    """Tests that verify proxyweb can manage two independent ProxySQL instances.

    proxysql_mysql    → mysql2/mysql3 (testdb2, user proxyuser2/proxypass2)
    proxysql_postgres → postgres/postgres2 (testdb_pg, user pguser/pgpass)
    proxysql_mysql ships with pre-seeded query rules (rule_id 1 and 2).
    """

    S1 = SERVER
    S2 = PG_SERVER
    DB = "main"

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()

    # ------------------------------------------------------------------
    # Server list / navigation
    # ------------------------------------------------------------------

    def test_home_page_lists_both_servers(self):
        """Both server names must appear in the navigation sidebar."""
        resp = self.s.get("/")
        self.assertIn(self.S1, resp.text)
        self.assertIn(self.S2, resp.text)

    def test_mysql_server_table_view_accessible(self):
        """proxysql_mysql's mysql_servers table view is accessible."""
        resp = self.s.get(f"/{self.S1}/{self.DB}/mysql_servers/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("hostname", resp.text)

    def test_pg_server_table_view_accessible(self):
        """proxysql_postgres's pgsql_servers table view is accessible."""
        resp = self.s.get(f"/{self.S2}/{self.DB}/pgsql_servers/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("hostname", resp.text)

    # ------------------------------------------------------------------
    # Backend server isolation
    # ------------------------------------------------------------------

    def test_mysql_server_has_mysql_backends(self):
        """proxysql_mysql should list mysql2 as its backend."""
        self.s.get(f"/{self.S1}/{self.DB}/mysql_servers/")
        data = self.s.get_table_data(self.S1, self.DB, "mysql_servers",
                                      **{"search[value]": "mysql2"})
        flat = str(data["data"])
        self.assertIn("mysql2", flat)

    def test_pg_server_has_postgres_backends(self):
        """proxysql_postgres should list postgres backends."""
        self.s.get(f"/{self.S2}/{self.DB}/pgsql_servers/")
        data = self.s.get_table_data(self.S2, self.DB, "pgsql_servers",
                                      **{"search[value]": "postgres"})
        flat = str(data["data"])
        self.assertIn("postgres", flat)

    # ------------------------------------------------------------------
    # Query rules: proxysql_mysql pre-seeded with 2 rules
    # ------------------------------------------------------------------

    def test_mysql_server_has_preseeded_query_rules(self):
        """proxysql_mysql was initialised with rule_id 1 and 2."""
        resp = self.s.get(f"/{self.S1}/{self.DB}/mysql_query_rules/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("rule_id", resp.text)
        data = self.s.get_table_data(self.S1, self.DB, "mysql_query_rules")
        rule_ids = [str(row[0]) for row in data["data"]]
        self.assertIn("1", rule_ids)
        self.assertIn("2", rule_ids)

    # ------------------------------------------------------------------
    # Config diff works independently per server
    # ------------------------------------------------------------------

    def test_config_diff_mysql_server(self):
        self.s.get(f"/{self.S1}/config_diff/")
        resp = self.s.post_json(f"/{self.S1}/config_diff/get", {})
        body = resp.json()
        self.assertTrue(body.get("success"), body.get("error"))

    def test_config_diff_pg_server(self):
        self.s.get(f"/{self.S2}/config_diff/")
        resp = self.s.post_json(f"/{self.S2}/config_diff/get", {})
        body = resp.json()
        self.assertTrue(body.get("success"), body.get("error"))

if __name__ == "__main__":
    unittest.main()