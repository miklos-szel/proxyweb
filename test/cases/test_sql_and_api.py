#!/usr/bin/env python3
"""SQL form, /api/update_row|insert_row|delete_row, and config-diff API."""

import re
import unittest

from testlib import (
    ProxyWebSession, BASE_URL, USERNAME, PASSWORD,
    SERVER, PG_SERVER, DATABASE,
)


class TestSQLExecution(unittest.TestCase):
    """SQL form: SELECT routes to adhoc report, other statements execute as changes."""

    def setUp(self):
        """
        Prepare an authenticated ProxyWebSession and refresh its CSRF token for use by tests.
        
        Creates self.s as a logged-in ProxyWebSession and loads the global_variables table page to ensure the session has a current CSRF token.
        """
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

    SERVER   = SERVER
    DATABASE = "main"
    TABLE    = "mysql_servers"

    # Test row we insert and then clean up
    TEST_HOST      = "test-integration-host"
    TEST_HG        = 99
    TEST_PORT      = 3399

    def setUp(self):
        """
        Set up an authenticated ProxyWebSession and preload the table page to initialize CSRF token and session state.
        
        Creates a ProxyWebSession, logs in using the configured credentials, and fetches the table view for self.SERVER/self.DATABASE/self.TABLE so the session cookies and CSRF token are populated for subsequent requests.
        """
        self.s = ProxyWebSession()
        self.s.login()
        # Fetch table page to populate CSRF token and session state
        self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _pk_values(self):
        """
        Return the primary key mapping for the test mysql_servers row.
        
        Returns:
            dict: Mapping with keys "hostgroup_id", "hostname", and "port". Numeric values are converted to strings:
                - "hostgroup_id": string representation of the test hostgroup id
                - "hostname": test host name
                - "port": string representation of the test port
        """
        return {
            "hostgroup_id": str(self.TEST_HG),
            "hostname":     self.TEST_HOST,
            "port":         str(self.TEST_PORT),
        }

    def _column_names(self):
        """
        Get the ordered list of column names as shown on the table page.
        
        Returns:
            list[str]: Column names in header order (order matters when constructing primary-key value lists).
        """
        resp = self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")
        # Extract column names from <th> in thead
        return re.findall(r'<th[^>]*>(.*?)</th>', resp.text)

    def _insert_test_row(self):
        """
        Insert a predefined test row into the configured server/database/table via the API and return the API response.
        
        The inserted row uses the test host, hostgroup, and port values defined on the test class.
        
        Returns:
            dict: Parsed JSON response from the API describing the result of the insert operation.
        """
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
        """
        Delete the test row from the configured server/database/table via the API.

        Returns:
            dict: The parsed JSON response from the `/api/delete_row` endpoint.
        """
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
        try:
            self.assertTrue(result.get("success"), f"Insert failed: {result.get('error')}")
        finally:
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
        """
        Prepare an authenticated session and load the config diff page for tests.
        
        This sets up a ProxyWebSession, logs in with default credentials, and requests the
        / proxysql/config_diff/ page so subsequent tests have an authenticated session and
        a fresh CSRF token.
        """
        self.s = ProxyWebSession()
        self.s.login()
        self.s.get(f"/{SERVER}/config_diff/")

    def test_get_config_diff_returns_success(self):
        resp = self.s.post_json(f"/{SERVER}/config_diff/get", {})
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


class TestConfigDiffMemoryRuntime(unittest.TestCase):
    """Config diff must detect gaps between mysql_users memory and runtime layers.

    Regression: changes made to mysql_users without LOAD TO RUNTIME were not
    appearing in the diff output.
    """

    SERVER = SERVER
    DB     = "main"
    TABLE  = "mysql_users"
    TEST_USER = "difftest-user"

    def setUp(self):
        """
        Prepare the test by creating an authenticated ProxyWeb session and loading the table page so a valid CSRF token is available for subsequent requests.
        
        This logs in with default credentials and performs a GET of the test table view.
        """
        self.s = ProxyWebSession()
        self.s.login()
        self.s.get(f"/{self.SERVER}/{self.DB}/{self.TABLE}/")

    def tearDown(self):
        # Remove test user regardless of test outcome so it does not pollute state.
        """
        Clean up the test user from the mysql_users table to avoid leaving test state behind.

        Attempts to load the table page (to refresh CSRF) and delete the row identified by `TEST_USER`. Any errors during cleanup are suppressed so teardown does not raise.
        """
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

if __name__ == "__main__":
    unittest.main()