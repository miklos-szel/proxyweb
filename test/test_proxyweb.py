#!/usr/bin/env python3
"""
Integration tests for ProxyWeb.

Requires the docker-compose stack in this directory to be running.
Use run_tests.sh to bring the stack up and execute the tests automatically,
or set PROXYWEB_URL to point at an already-running instance.

Environment variables:
  PROXYWEB_URL   Base URL of proxyweb (default: http://localhost:5000)
  PROXYWEB_USER  Admin username       (default: admin)
  PROXYWEB_PASS  Admin password       (default: admin42)
"""

import os
import re
import sys
import time
import json
import unittest

import requests

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
        resp = self.s.session.post(
            f"{BASE_URL}/api/update_row",
            data="not-json",
            headers={
                "Content-Type": "text/plain",
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
