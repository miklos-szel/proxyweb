#!/usr/bin/env python3
"""
Integration tests for ProxyWeb.

Requires the docker-compose stack in this directory to be running.
Use run_tests.sh to bring the stack up and execute the tests automatically,
or set PROXYWEB_URL to point at an already-running instance.

Environment variables:
  PROXYWEB_URL     Base URL of proxyweb (default: http://localhost:5000)
  PROXYWEB_USER    Admin username       (default: admin)
  PROXYWEB_PASS    Admin password       (default: admin42)
  PROXYSQL1_HOST   ProxySQL 1 MySQL frontend host (default: 127.0.0.1)
  PROXYSQL1_PORT   ProxySQL 1 MySQL frontend port (default: 6033)
  PROXYSQL2_HOST   ProxySQL 2 MySQL frontend host (default: 127.0.0.1)
  PROXYSQL2_PORT   ProxySQL 2 MySQL frontend port (default: 6035)
"""

import os
import re
import sys
import time
import json
import unittest

import requests

try:
    import pymysql
    HAS_PYMYSQL = True
except ImportError:
    HAS_PYMYSQL = False

BASE_URL = os.environ.get("PROXYWEB_URL", "http://localhost:5000").rstrip("/")
USERNAME = os.environ.get("PROXYWEB_USER", "admin")
PASSWORD = os.environ.get("PROXYWEB_PASS", "admin42")

SERVER   = "proxysql"
DATABASE = "main"


# ---------------------------------------------------------------------------
# Session helper
# ---------------------------------------------------------------------------

class ProxyWebSession:
    """Authenticated requests session with automatic CSRF token handling."""

    def __init__(self):
        self.session = requests.Session()
        self.csrf_token = ""

    def login(self, username=USERNAME, password=PASSWORD):
        resp = self.session.post(
            f"{BASE_URL}/login",
            data={"username": username, "password": password},
            allow_redirects=True,
            timeout=10,
        )
        resp.raise_for_status()
        self._refresh_csrf(resp.text)
        return resp

    def get(self, path, **kwargs):
        kwargs.setdefault("timeout", 10)
        resp = self.session.get(f"{BASE_URL}{path}", **kwargs)
        resp.raise_for_status()
        self._refresh_csrf(resp.text)
        return resp

    def post_form(self, path, data=None, **kwargs):
        payload = dict(data or {})
        payload["_csrf_token"] = self.csrf_token
        kwargs.setdefault("timeout", 10)
        resp = self.session.post(f"{BASE_URL}{path}", data=payload, **kwargs)
        resp.raise_for_status()
        self._refresh_csrf(resp.text)
        return resp

    def post_json(self, path, body, **kwargs):
        kwargs.setdefault("timeout", 10)
        headers = {
            "Content-Type": "application/json",
            "X-CSRF-Token": self.csrf_token,
        }
        resp = self.session.post(
            f"{BASE_URL}{path}", json=body, headers=headers, **kwargs
        )
        resp.raise_for_status()
        return resp

    def _refresh_csrf(self, html):
        m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
        if m:
            self.csrf_token = m.group(1)


# ---------------------------------------------------------------------------
# Wait-for-ready helper (called once before the test run)
# ---------------------------------------------------------------------------

def wait_for_proxyweb(timeout=120):
    """Poll /login until proxyweb responds or timeout is reached."""
    deadline = time.monotonic() + timeout
    last_err = None
    while time.monotonic() < deadline:
        try:
            r = requests.get(f"{BASE_URL}/login", timeout=5)
            if r.status_code == 200:
                return
        except requests.RequestException as exc:
            last_err = exc
        time.sleep(3)
    raise RuntimeError(
        f"ProxyWeb did not become ready within {timeout}s. Last error: {last_err}"
    )


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestAuth(unittest.TestCase):
    """Authentication: login, logout, access control."""

    def test_login_page_returns_200(self):
        resp = requests.get(f"{BASE_URL}/login", timeout=10)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("login", resp.text.lower())

    def test_unauthenticated_root_redirects_to_login(self):
        resp = requests.get(f"{BASE_URL}/", allow_redirects=False, timeout=10)
        self.assertIn(resp.status_code, (301, 302, 303))
        self.assertIn("login", resp.headers.get("Location", "").lower())

    def test_wrong_credentials_stay_on_login(self):
        s = requests.Session()
        resp = s.post(
            f"{BASE_URL}/login",
            data={"username": "wrong", "password": "wrong"},
            allow_redirects=True,
            timeout=10,
        )
        self.assertEqual(resp.status_code, 200)
        # Still on the login page
        self.assertIn("login", resp.url)

    def test_correct_credentials_redirect_away_from_login(self):
        s = ProxyWebSession()
        resp = s.login()
        self.assertNotIn("/login", resp.url)
        self.assertEqual(resp.status_code, 200)

    def test_logout_clears_session(self):
        s = ProxyWebSession()
        s.login()
        # After logout, accessing / should redirect to login
        resp = s.session.get(f"{BASE_URL}/logout", allow_redirects=True, timeout=10)
        self.assertIn("login", resp.url)
        # Without re-login, / should be protected
        resp2 = s.session.get(f"{BASE_URL}/", allow_redirects=False, timeout=10)
        self.assertIn(resp2.status_code, (301, 302, 303))


class TestNavigation(unittest.TestCase):
    """Page navigation: table list, table view, adhoc report."""

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()

    def test_home_page_lists_databases(self):
        resp = self.s.get("/")
        self.assertEqual(resp.status_code, 200)
        # The sidebar should reference known ProxySQL databases
        self.assertIn("main", resp.text)

    def test_global_variables_table_view(self):
        resp = self.s.get(f"/{SERVER}/{DATABASE}/global_variables/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("variable_name", resp.text)
        self.assertIn("variable_value", resp.text)

    def test_mysql_servers_table_view(self):
        resp = self.s.get(f"/{SERVER}/{DATABASE}/mysql_servers/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("hostname", resp.text)

    def test_mysql_users_table_view(self):
        resp = self.s.get(f"/{SERVER}/{DATABASE}/mysql_users/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("username", resp.text)

    def test_stats_mysql_query_digest_view(self):
        resp = self.s.get(f"/{SERVER}/stats/stats_mysql_query_digest/")
        self.assertEqual(resp.status_code, 200)

    def test_adhoc_report_page(self):
        resp = self.s.get(f"/{SERVER}/adhoc/")
        self.assertEqual(resp.status_code, 200)

    def test_settings_edit_page(self):
        resp = self.s.get("/settings/edit/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("global", resp.text)  # YAML content

    def test_config_diff_page(self):
        resp = self.s.get("/proxysql/config_diff/")
        self.assertEqual(resp.status_code, 200)


class TestSQLExecution(unittest.TestCase):
    """SQL form: SELECT routes to adhoc report, other statements execute as changes."""

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        # Fetch the table page to get a fresh CSRF token
        self.s.get(f"/{SERVER}/{DATABASE}/global_variables/")

    def test_select_via_sql_form(self):
        resp = self.s.post_form(
            f"/{SERVER}/{DATABASE}/global_variables/sql/",
            {"sql": "SELECT * FROM global_variables LIMIT 5"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("variable_name", resp.text)

    def test_select_with_leading_whitespace(self):
        """Regression for the re.match / re.M bug (F9): leading spaces must still
        be detected as SELECT."""
        resp = self.s.post_form(
            f"/{SERVER}/{DATABASE}/global_variables/sql/",
            {"sql": "  SELECT variable_name FROM global_variables LIMIT 1"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("variable_name", resp.text)


class TestAPIRowOperations(unittest.TestCase):
    """API endpoints: insert, update, delete a row in mysql_servers."""

    SERVER   = "proxysql"
    DATABASE = "main"
    TABLE    = "mysql_servers"

    # Test row we insert and then clean up
    TEST_HOST      = "test-integration-host"
    TEST_HG        = 99
    TEST_PORT      = 3399

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        # Fetch table page to populate CSRF token and session state
        self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _pk_values(self):
        return {
            "hostgroup_id": str(self.TEST_HG),
            "hostname":     self.TEST_HOST,
            "port":         str(self.TEST_PORT),
        }

    def _column_names(self):
        """Return the column list from the table page (order matters for pkValues)."""
        resp = self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")
        # Extract column names from <th> in thead
        return re.findall(r'<th[^>]*>(.*?)</th>', resp.text)

    def _insert_test_row(self):
        resp = self.s.post_json("/api/insert_row", {
            "server":      self.SERVER,
            "database":    self.DATABASE,
            "table":       self.TABLE,
            "columnNames": ["hostgroup_id", "hostname", "port"],
            "data":        {
                "hostgroup_id": str(self.TEST_HG),
                "hostname":     self.TEST_HOST,
                "port":         str(self.TEST_PORT),
            },
        })
        return resp.json()

    def _delete_test_row(self):
        resp = self.s.post_json("/api/delete_row", {
            "server":   self.SERVER,
            "database": self.DATABASE,
            "table":    self.TABLE,
            "pkValues": self._pk_values(),
        })
        return resp.json()

    # ------------------------------------------------------------------
    # tests
    # ------------------------------------------------------------------

    def test_insert_row(self):
        result = self._insert_test_row()
        self.assertTrue(result.get("success"), f"Insert failed: {result.get('error')}")
        # Clean up
        self._delete_test_row()

    def test_delete_row(self):
        self._insert_test_row()
        result = self._delete_test_row()
        self.assertTrue(result.get("success"), f"Delete failed: {result.get('error')}")

    def test_update_row(self):
        self._insert_test_row()
        try:
            resp = self.s.post_json("/api/update_row", {
                "server":      self.SERVER,
                "database":    self.DATABASE,
                "table":       self.TABLE,
                "pkValues":    self._pk_values(),
                "columnNames": ["hostgroup_id", "hostname", "port", "weight",
                                "status", "compression", "max_connections",
                                "max_replication_lag", "use_ssl", "max_latency_ms",
                                "comment"],
                "data":        {"weight": "5"},
            })
            result = resp.json()
            self.assertTrue(result.get("success"), f"Update failed: {result.get('error')}")
        finally:
            self._delete_test_row()

    def test_runtime_table_rejected(self):
        """runtime_ tables must be refused with 403."""
        resp = self.s.session.post(
            f"{BASE_URL}/api/delete_row",
            json={
                "server":   self.SERVER,
                "database": self.DATABASE,
                "table":    "runtime_mysql_servers",
                "pkValues": self._pk_values(),
            },
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": self.s.csrf_token,
            },
            timeout=10,
        )
        self.assertEqual(resp.status_code, 403)

    def test_missing_json_body_returns_400(self):
        """F8: missing JSON body must not crash the API (returns 400)."""
        # Send application/json with an empty body — get_json(silent=True)
        # returns None, the guard fires and returns 400.
        resp = self.s.session.post(
            f"{BASE_URL}/api/update_row",
            data=b"",
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": self.s.csrf_token,
            },
            timeout=10,
        )
        self.assertEqual(resp.status_code, 400)


class TestAPIConfigDiff(unittest.TestCase):
    """Config diff API returns structured data."""

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        self.s.get("/proxysql/config_diff/")

    def test_get_config_diff_returns_success(self):
        resp = self.s.post_json("/proxysql/config_diff/get", {})
        body = resp.json()
        self.assertTrue(body.get("success"), f"config diff failed: {body.get('error')}")
        self.assertIn("tables", body.get("diff", {}))

    def test_get_schema_returns_columns(self):
        resp = self.s.session.get(
            f"{BASE_URL}/api/get_schema",
            params={"table": "mysql_servers"},
            timeout=10,
        )
        body = resp.json()
        self.assertTrue(body.get("success"), f"get_schema failed: {body.get('error')}")
        self.assertIn("columns", body.get("schema", {}))


class TestMySQLServers(unittest.TestCase):
    """CRUD operations on mysql_servers via ProxyWeb API."""

    SERVER   = "proxysql"
    DATABASE = "main"
    TABLE    = "mysql_servers"

    TEST_HOST = "test-mysql-server-host"
    TEST_HG   = 98
    TEST_PORT = 3310

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")

    def _pk(self):
        return {
            "hostgroup_id": str(self.TEST_HG),
            "hostname":     self.TEST_HOST,
            "port":         str(self.TEST_PORT),
        }

    def _insert(self):
        return self.s.post_json("/api/insert_row", {
            "server":      self.SERVER,
            "database":    self.DATABASE,
            "table":       self.TABLE,
            "columnNames": ["hostgroup_id", "hostname", "port"],
            "data": {
                "hostgroup_id": str(self.TEST_HG),
                "hostname":     self.TEST_HOST,
                "port":         str(self.TEST_PORT),
            },
        }).json()

    def _delete(self):
        return self.s.post_json("/api/delete_row", {
            "server":   self.SERVER,
            "database": self.DATABASE,
            "table":    self.TABLE,
            "pkValues": self._pk(),
        }).json()

    def test_insert_mysql_server(self):
        result = self._insert()
        self.assertTrue(result.get("success"), result.get("error"))
        self._delete()

    def test_update_mysql_server_weight(self):
        self._insert()
        try:
            result = self.s.post_json("/api/update_row", {
                "server":      self.SERVER,
                "database":    self.DATABASE,
                "table":       self.TABLE,
                "pkValues":    self._pk(),
                "columnNames": ["hostgroup_id", "hostname", "port", "weight",
                                "status", "compression", "max_connections",
                                "max_replication_lag", "use_ssl",
                                "max_latency_ms", "comment"],
                "data": {"weight": "10"},
            }).json()
            self.assertTrue(result.get("success"), result.get("error"))
        finally:
            self._delete()

    def test_delete_mysql_server(self):
        self._insert()
        result = self._delete()
        self.assertTrue(result.get("success"), result.get("error"))

    def test_table_view_shows_mysql_server(self):
        """The backend mysql server registered by proxysql-init must appear."""
        resp = self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")
        self.assertIn("mysql", resp.text)   # hostname of the backend container


class TestQueryRules(unittest.TestCase):
    """CRUD operations on mysql_query_rules via ProxyWeb API."""

    SERVER   = "proxysql"
    DATABASE = "main"
    TABLE    = "mysql_query_rules"

    TEST_RULE_ID = 900

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")

    def _pk(self):
        return {"rule_id": str(self.TEST_RULE_ID)}

    def _col_names(self):
        return [
            "rule_id", "active", "username", "schemaname", "flagIN",
            "client_addr", "proxy_addr", "proxy_port", "digest",
            "match_digest", "match_pattern", "negate_match_pattern",
            "re_flags", "flagOUT", "replace_pattern",
            "destination_hostgroup", "cache_ttl", "cache_empty_result",
            "cache_timeout", "reconnect", "timeout", "retries", "delay",
            "next_query_flagIN", "mirror_flagOUT", "mirror_hostgroup",
            "error_msg", "OK_msg", "sticky_conn", "multiplex",
            "gtid_from_hostgroup", "log", "apply", "attributes", "comment",
        ]

    def _insert(self):
        return self.s.post_json("/api/insert_row", {
            "server":      self.SERVER,
            "database":    self.DATABASE,
            "table":       self.TABLE,
            "columnNames": ["rule_id", "active", "match_digest",
                            "destination_hostgroup", "apply"],
            "data": {
                "rule_id":               str(self.TEST_RULE_ID),
                "active":                "1",
                "match_digest":          "^SELECT.*FOR UPDATE",
                "destination_hostgroup": "0",
                "apply":                 "1",
            },
        }).json()

    def _delete(self):
        return self.s.post_json("/api/delete_row", {
            "server":   self.SERVER,
            "database": self.DATABASE,
            "table":    self.TABLE,
            "pkValues": self._pk(),
        }).json()

    def test_insert_query_rule(self):
        result = self._insert()
        self.assertTrue(result.get("success"), result.get("error"))
        self._delete()

    def test_update_query_rule(self):
        self._insert()
        try:
            result = self.s.post_json("/api/update_row", {
                "server":      self.SERVER,
                "database":    self.DATABASE,
                "table":       self.TABLE,
                "pkValues":    self._pk(),
                "columnNames": self._col_names(),
                "data":        {"match_digest": "^SELECT", "active": "0"},
            }).json()
            self.assertTrue(result.get("success"), result.get("error"))
        finally:
            self._delete()

    def test_delete_query_rule(self):
        self._insert()
        result = self._delete()
        self.assertTrue(result.get("success"), result.get("error"))

    def test_query_rules_table_view(self):
        resp = self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("rule_id", resp.text)

    def test_insert_update_delete_full_cycle(self):
        """End-to-end: insert a rule, update its digest, then delete it."""
        insert_result = self._insert()
        self.assertTrue(insert_result.get("success"), insert_result.get("error"))
        try:
            upd = self.s.post_json("/api/update_row", {
                "server":      self.SERVER,
                "database":    self.DATABASE,
                "table":       self.TABLE,
                "pkValues":    self._pk(),
                "columnNames": self._col_names(),
                "data":        {"match_digest": "^SELECT.*", "comment": "test-rule"},
            }).json()
            self.assertTrue(upd.get("success"), upd.get("error"))
        finally:
            del_result = self._delete()
            self.assertTrue(del_result.get("success"), del_result.get("error"))


# ---------------------------------------------------------------------------
# Direct MySQL DML through both ProxySQL frontend ports
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_PYMYSQL, "pymysql not installed — skipping backend SQL tests")
class TestProxySQL1BackendSQL(unittest.TestCase):
    """SELECT / INSERT / UPDATE / DELETE through ProxySQL 1 (port 6033).

    Connects as proxyuser/proxypass which is registered in ProxySQL 1's
    runtime_mysql_users and granted on the mysql backend's testdb.
    Table under test: testdb.items (id INT PK, name VARCHAR, val INT).
    """

    HOST = os.environ.get("PROXYSQL1_HOST", "127.0.0.1")
    PORT = int(os.environ.get("PROXYSQL1_PORT", "6033"))
    USER = "proxyuser"
    PASS = "proxypass"
    DB   = "testdb"

    def _conn(self):
        return pymysql.connect(
            host=self.HOST, port=self.PORT,
            user=self.USER, password=self.PASS,
            database=self.DB, autocommit=True,
        )

    def test_select_returns_rows(self):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, val FROM items ORDER BY id")
                rows = cur.fetchall()
        self.assertGreater(len(rows), 0, "Expected seed rows in testdb.items")
        names = [r[1] for r in rows]
        self.assertIn("alpha", names)

    def test_insert_row(self):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO items (name, val) VALUES (%s, %s)",
                    ("ps1-test-insert", 42),
                )
                inserted_id = conn.insert_id()
        self.assertGreater(inserted_id, 0)
        # Verify it persisted
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT name, val FROM items WHERE id = %s", (inserted_id,))
                row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "ps1-test-insert")
        self.assertEqual(row[1], 42)
        # Cleanup
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM items WHERE id = %s", (inserted_id,))

    def test_update_row(self):
        # Insert a row to update
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO items (name, val) VALUES (%s, %s)",
                    ("ps1-test-update", 1),
                )
                row_id = conn.insert_id()
        try:
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE items SET val = %s WHERE id = %s",
                        (99, row_id),
                    )
                    self.assertEqual(conn.affected_rows(), 1)
            # Verify
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT val FROM items WHERE id = %s", (row_id,))
                    self.assertEqual(cur.fetchone()[0], 99)
        finally:
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM items WHERE id = %s", (row_id,))

    def test_delete_row(self):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO items (name, val) VALUES (%s, %s)",
                    ("ps1-test-delete", 7),
                )
                row_id = conn.insert_id()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM items WHERE id = %s", (row_id,))
                self.assertEqual(conn.affected_rows(), 1)
        # Confirm gone
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM items WHERE id = %s", (row_id,))
                self.assertIsNone(cur.fetchone())


@unittest.skipUnless(HAS_PYMYSQL, "pymysql not installed — skipping backend SQL tests")
class TestProxySQL2BackendSQL(unittest.TestCase):
    """SELECT / INSERT / UPDATE / DELETE through ProxySQL 2 (port 6035).

    Connects as proxyuser2/proxypass2 which is registered in ProxySQL 2's
    runtime_mysql_users and granted on the mysql2 backend's testdb2.
    Table under test: testdb2.products (id INT PK, name VARCHAR, price DECIMAL).
    """

    HOST = os.environ.get("PROXYSQL2_HOST", "127.0.0.1")
    PORT = int(os.environ.get("PROXYSQL2_PORT", "6035"))
    USER = "proxyuser2"
    PASS = "proxypass2"
    DB   = "testdb2"

    def _conn(self):
        return pymysql.connect(
            host=self.HOST, port=self.PORT,
            user=self.USER, password=self.PASS,
            database=self.DB, autocommit=True,
        )

    def test_select_returns_rows(self):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, price FROM products ORDER BY id")
                rows = cur.fetchall()
        self.assertGreater(len(rows), 0, "Expected seed rows in testdb2.products")
        names = [r[1] for r in rows]
        self.assertIn("widget", names)

    def test_insert_row(self):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO products (name, price) VALUES (%s, %s)",
                    ("ps2-test-insert", "7.77"),
                )
                inserted_id = conn.insert_id()
        self.assertGreater(inserted_id, 0)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name, price FROM products WHERE id = %s", (inserted_id,)
                )
                row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "ps2-test-insert")
        self.assertAlmostEqual(float(row[1]), 7.77, places=2)
        # Cleanup
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM products WHERE id = %s", (inserted_id,))

    def test_update_row(self):
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO products (name, price) VALUES (%s, %s)",
                    ("ps2-test-update", "1.00"),
                )
                row_id = conn.insert_id()
        try:
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE products SET price = %s WHERE id = %s",
                        ("49.99", row_id),
                    )
                    self.assertEqual(conn.affected_rows(), 1)
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT price FROM products WHERE id = %s", (row_id,)
                    )
                    self.assertAlmostEqual(float(cur.fetchone()[0]), 49.99, places=2)
        finally:
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
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM products WHERE id = %s", (row_id,))
                self.assertIsNone(cur.fetchone())

    def test_proxysql2_data_isolated_from_proxysql1(self):
        """Data written through ProxySQL 2 must not appear through ProxySQL 1."""
        # Insert through ProxySQL 2
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO products (name, price) VALUES (%s, %s)",
                    ("isolation-check", "0.99"),
                )
                row_id = conn.insert_id()
        try:
            # ProxySQL 1 routes to testdb on mysql — the products table does not
            # exist there, so any attempt to query it must raise an error.
            ps1 = pymysql.connect(
                host=os.environ.get("PROXYSQL1_HOST", "127.0.0.1"),
                port=int(os.environ.get("PROXYSQL1_PORT", "6033")),
                user="proxyuser", password="proxypass",
                database="testdb", autocommit=True,
            )
            try:
                with ps1.cursor() as cur:
                    with self.assertRaises(pymysql.err.ProgrammingError):
                        cur.execute("SELECT id FROM products WHERE id = %s", (row_id,))
                        cur.fetchall()
            finally:
                ps1.close()
        finally:
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM products WHERE id = %s", (row_id,))


# ---------------------------------------------------------------------------
# Config diff memory-vs-runtime regression
# ---------------------------------------------------------------------------

class TestConfigDiffMemoryRuntime(unittest.TestCase):
    """Config diff must detect gaps between mysql_users memory and runtime layers.

    Regression: changes made to mysql_users without LOAD TO RUNTIME were not
    appearing in the diff output.
    """

    SERVER = "proxysql"
    DB     = "main"
    TABLE  = "mysql_users"
    TEST_USER = "difftest-user"

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        self.s.get(f"/{self.SERVER}/{self.DB}/{self.TABLE}/")

    def tearDown(self):
        # Remove test user regardless of test outcome so it does not pollute state.
        try:
            self.s.get(f"/{self.SERVER}/{self.DB}/{self.TABLE}/")
            self.s.post_json("/api/delete_row", {
                "server":   self.SERVER,
                "database": self.DB,
                "table":    self.TABLE,
                "pkValues": {"username": self.TEST_USER},
            })
        except Exception:
            pass

    def test_diff_detects_user_added_to_memory_only(self):
        """Insert a user into mysql_users without LOAD TO RUNTIME.
        The config diff API must report mysql_users as having differences
        between the memory and runtime layers."""
        insert = self.s.post_json("/api/insert_row", {
            "server":      self.SERVER,
            "database":    self.DB,
            "table":       self.TABLE,
            "columnNames": ["username", "password", "default_hostgroup", "active"],
            "data": {
                "username":          self.TEST_USER,
                "password":          "difftest-pass",
                "default_hostgroup": "0",
                "active":            "1",
            },
        }).json()
        self.assertTrue(insert.get("success"), f"Insert failed: {insert.get('error')}")

        # Fetch config diff — deliberately NOT loading to runtime first.
        self.s.get(f"/{self.SERVER}/config_diff/")
        body = self.s.post_json(f"/{self.SERVER}/config_diff/get", {}).json()
        self.assertTrue(body.get("success"), body.get("error"))

        tables = body.get("diff", {}).get("tables", [])
        users_entry = next(
            (t for t in tables if t.get("table_name") == "mysql_users"), None
        )
        self.assertIsNotNone(users_entry, "mysql_users missing from config diff tables list")

        has_diff = users_entry.get("stats", {}).get("has_differences", False)
        self.assertTrue(
            has_diff,
            "Config diff did not detect that mysql_users (memory) differs from "
            "runtime_mysql_users after inserting a user without LOAD TO RUNTIME",
        )

        # Confirm the new user appears in the memory-only list within the diff.
        only_in_memory = (
            users_entry.get("differences", {})
                       .get("memory_vs_runtime", {})
                       .get("only_in_memory", [])
        )
        usernames_in_memory = [row.get("username") for row in only_in_memory]
        self.assertIn(
            self.TEST_USER,
            usernames_in_memory,
            f"Expected {self.TEST_USER!r} to appear in memory-only diff rows, "
            f"got: {usernames_in_memory}",
        )


# ---------------------------------------------------------------------------
# Multi-server tests
# ---------------------------------------------------------------------------

class TestMultiServer(unittest.TestCase):
    """Tests that verify proxyweb can manage two independent ProxySQL instances.

    proxysql  → mysql  (testdb,  user proxyuser/proxypass)
    proxysql2 → mysql2 (testdb2, user proxyuser2/proxypass2)
    proxysql2 ships with pre-seeded query rules (rule_id 1 and 2).
    """

    S1 = "proxysql"
    S2 = "proxysql2"
    DB = "main"

    # PK for a test query rule we CRUD on proxysql2
    TEST_RULE_ID = 800

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

    def test_server1_table_view_accessible(self):
        resp = self.s.get(f"/{self.S1}/{self.DB}/mysql_servers/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("hostname", resp.text)

    def test_server2_table_view_accessible(self):
        resp = self.s.get(f"/{self.S2}/{self.DB}/mysql_servers/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("hostname", resp.text)

    # ------------------------------------------------------------------
    # Backend server isolation — each ProxySQL points to a different MySQL
    # ------------------------------------------------------------------

    def test_server1_has_mysql_backend(self):
        """proxysql should list 'mysql' (not mysql2) as its backend."""
        resp = self.s.get(f"/{self.S1}/{self.DB}/mysql_servers/")
        self.assertIn("mysql", resp.text)

    def test_server2_has_mysql2_backend(self):
        """proxysql2 should list 'mysql2' as its backend."""
        resp = self.s.get(f"/{self.S2}/{self.DB}/mysql_servers/")
        self.assertIn("mysql2", resp.text)

    def test_server2_backend_is_not_server1_backend(self):
        """The mysql_servers table on proxysql2 must not contain the server1 host."""
        resp = self.s.get(f"/{self.S2}/{self.DB}/mysql_servers/")
        # proxysql2 knows about mysql2 but should have no row for plain 'mysql'
        # We look for the cell value being exactly "mysql" (surrounded by <td> tags)
        cells = re.findall(r'<td[^>]*>\s*(mysql)\s*</td>', resp.text)
        self.assertEqual(cells, [], "proxysql2 mysql_servers unexpectedly contains 'mysql' host")

    # ------------------------------------------------------------------
    # Query rules: proxysql2 pre-seeded with 2 rules; proxysql has none
    # ------------------------------------------------------------------

    def test_server1_has_no_preseeded_query_rules(self):
        """proxysql was initialised without query rules."""
        resp = self.s.get(f"/{self.S1}/{self.DB}/mysql_query_rules/")
        self.assertEqual(resp.status_code, 200)
        # Table should exist but have no data rows with rule_id 1 or 2
        self.assertNotIn(">1<", resp.text)

    def test_server2_has_preseeded_query_rules(self):
        """proxysql2 was initialised with rule_id 1 and 2."""
        resp = self.s.get(f"/{self.S2}/{self.DB}/mysql_query_rules/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("rule_id", resp.text)
        self.assertIn(">1<", resp.text)
        self.assertIn(">2<", resp.text)

    # ------------------------------------------------------------------
    # CRUD on proxysql2 query rules
    # ------------------------------------------------------------------

    def _s2_pk(self):
        return {"rule_id": str(self.TEST_RULE_ID)}

    def _col_names(self):
        return [
            "rule_id", "active", "username", "schemaname", "flagIN",
            "client_addr", "proxy_addr", "proxy_port", "digest",
            "match_digest", "match_pattern", "negate_match_pattern",
            "re_flags", "flagOUT", "replace_pattern",
            "destination_hostgroup", "cache_ttl", "cache_empty_result",
            "cache_timeout", "reconnect", "timeout", "retries", "delay",
            "next_query_flagIN", "mirror_flagOUT", "mirror_hostgroup",
            "error_msg", "OK_msg", "sticky_conn", "multiplex",
            "gtid_from_hostgroup", "log", "apply", "attributes", "comment",
        ]

    def _insert_s2_rule(self):
        self.s.get(f"/{self.S2}/{self.DB}/mysql_query_rules/")
        return self.s.post_json("/api/insert_row", {
            "server":      self.S2,
            "database":    self.DB,
            "table":       "mysql_query_rules",
            "columnNames": ["rule_id", "active", "match_digest",
                            "destination_hostgroup", "apply"],
            "data": {
                "rule_id":               str(self.TEST_RULE_ID),
                "active":                "1",
                "match_digest":          "^DELETE",
                "destination_hostgroup": "0",
                "apply":                 "1",
            },
        }).json()

    def _delete_s2_rule(self):
        self.s.get(f"/{self.S2}/{self.DB}/mysql_query_rules/")
        return self.s.post_json("/api/delete_row", {
            "server":   self.S2,
            "database": self.DB,
            "table":    "mysql_query_rules",
            "pkValues": self._s2_pk(),
        }).json()

    def test_insert_query_rule_on_server2(self):
        result = self._insert_s2_rule()
        self.assertTrue(result.get("success"), result.get("error"))
        self._delete_s2_rule()

    def test_update_query_rule_on_server2(self):
        self._insert_s2_rule()
        try:
            self.s.get(f"/{self.S2}/{self.DB}/mysql_query_rules/")
            result = self.s.post_json("/api/update_row", {
                "server":      self.S2,
                "database":    self.DB,
                "table":       "mysql_query_rules",
                "pkValues":    self._s2_pk(),
                "columnNames": self._col_names(),
                "data":        {"match_digest": "^DELETE.*", "comment": "s2-test"},
            }).json()
            self.assertTrue(result.get("success"), result.get("error"))
        finally:
            self._delete_s2_rule()

    def test_delete_query_rule_on_server2(self):
        self._insert_s2_rule()
        result = self._delete_s2_rule()
        self.assertTrue(result.get("success"), result.get("error"))

    def test_server2_rule_does_not_appear_on_server1(self):
        """A rule inserted on proxysql2 must not be visible on proxysql."""
        self._insert_s2_rule()
        try:
            resp = self.s.get(f"/{self.S1}/{self.DB}/mysql_query_rules/")
            self.assertNotIn(str(self.TEST_RULE_ID), resp.text)
        finally:
            self._delete_s2_rule()

    # ------------------------------------------------------------------
    # Config diff works independently per server
    # ------------------------------------------------------------------

    def test_config_diff_server1(self):
        self.s.get(f"/{self.S1}/config_diff/")
        resp = self.s.post_json(f"/{self.S1}/config_diff/get", {})
        body = resp.json()
        self.assertTrue(body.get("success"), body.get("error"))

    def test_config_diff_server2(self):
        self.s.get(f"/{self.S2}/config_diff/")
        resp = self.s.post_json(f"/{self.S2}/config_diff/get", {})
        body = resp.json()
        self.assertTrue(body.get("success"), body.get("error"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Waiting for ProxyWeb at {BASE_URL} ...")
    try:
        wait_for_proxyweb()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print("ProxyWeb is ready. Running tests.\n")
    unittest.main(verbosity=2)
