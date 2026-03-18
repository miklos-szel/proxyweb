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
        """
        Initialize the session helper.
        
        Creates a new requests.Session for HTTP calls and initializes the CSRF token storage.
        
        Attributes:
            session (requests.Session): Persistent HTTP session used for requests to ProxyWeb.
            csrf_token (str): Current CSRF token extracted from responses; empty until refreshed.
        """
        self.session = requests.Session()
        self.csrf_token = ""

    def login(self, username=USERNAME, password=PASSWORD):
        """
        Authenticate to ProxyWeb using the given credentials and refresh the session's CSRF token.
        
        Returns:
            resp: The HTTP response object from the login POST request.
        
        Raises:
            requests.HTTPError: If the login request returns an HTTP error status.
        """
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
        """
        Perform an authenticated GET request against the test server and update the session CSRF token.
        
        Parameters:
            path (str): Path component to append to BASE_URL (e.g., "/login" or "/proxysql/main/").
            **kwargs: Passed through to requests.Session.get (common keys: params, headers, timeout).
        
        Returns:
            resp (requests.Response): The HTTP response object.
        
        Raises:
            requests.HTTPError: If the response status indicates an HTTP error (raised by resp.raise_for_status()).
        """
        kwargs.setdefault("timeout", 10)
        resp = self.session.get(f"{BASE_URL}{path}", **kwargs)
        resp.raise_for_status()
        self._refresh_csrf(resp.text)
        return resp

    def post_form(self, path, data=None, **kwargs):
        """
        Submit a form-encoded POST to the given path while automatically injecting the current CSRF token and refreshing it from the response.
        
        Parameters:
            path (str): URL path appended to the configured base URL.
            data (dict, optional): Form fields to send; a copy is made and `_csrf_token` is added.
            **kwargs: Passed through to requests.Session.post (e.g., headers, timeout).
        
        Returns:
            resp (requests.Response): The HTTP response object.
        
        Raises:
            requests.HTTPError: If the response status code indicates an error.
        """
        payload = dict(data or {})
        payload["_csrf_token"] = self.csrf_token
        kwargs.setdefault("timeout", 10)
        resp = self.session.post(f"{BASE_URL}{path}", data=payload, **kwargs)
        resp.raise_for_status()
        self._refresh_csrf(resp.text)
        return resp

    def post_json(self, path, body, **kwargs):
        """
        Send a JSON request to the given application path including the current CSRF token.
        
        Parameters:
            path (str): URL path to append to the configured BASE_URL (e.g., '/api/endpoint').
            body: JSON-serializable object to send as the request body.
            **kwargs: Passed to requests.post (common use: timeout). Default timeout is 10 seconds.
        
        Returns:
            requests.Response: The HTTP response object.
        
        Raises:
            requests.HTTPError: If the response status indicates an HTTP error (response.raise_for_status()).
        """
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
        """
        Extracts a CSRF token from the provided HTML and stores it on the instance as `csrf_token`.
        
        Parameters:
            html (str): HTML content to search for a `<meta name="csrf-token" content="...">` tag. If a matching meta tag is found, its `content` value is assigned to `self.csrf_token`; otherwise `self.csrf_token` is left unchanged.
        """
        m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
        if m:
            self.csrf_token = m.group(1)


# ---------------------------------------------------------------------------
# Wait-for-ready helper (called once before the test run)
# ---------------------------------------------------------------------------

def wait_for_proxyweb(timeout=120):
    """
    Wait until ProxyWeb becomes responsive by polling the /login endpoint.
    
    Parameters:
        timeout (int): Maximum number of seconds to wait before giving up.
    
    Raises:
        RuntimeError: If /login does not return HTTP 200 within `timeout` seconds.
            The exception message includes the last encountered `requests.RequestException`,
            if any occurred during polling.
    """
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
        """
        Verify the login page is reachable and contains a login indicator.
        
        Asserts that a GET request to /login returns HTTP 200 and that the response body (case-insensitive) includes the substring "login".
        """
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
        """
        Verify logout clears the authenticated session and protects the root route.
        
        Logs in, requests /logout and asserts the response URL points to the login page, then requests / without re-login and asserts the response is a redirect (301, 302, or 303).
        """
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
        """
        Prepare an authenticated ProxyWebSession and store it as self.s for use by the test methods.
        """
        self.s = ProxyWebSession()
        self.s.login()

    def test_home_page_lists_databases(self):
        """
        Verify the application home page shows configured ProxySQL databases.
        
        Asserts that a GET request to the root path returns HTTP 200 and that the response body includes the database name "main", which should appear in the site navigation/sidebar.
        """
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

    SERVER   = "proxysql"
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
        """
        Prepare an authenticated session and load the config diff page for tests.
        
        This sets up a ProxyWebSession, logs in with default credentials, and requests the
        / proxysql/config_diff/ page so subsequent tests have an authenticated session and
        a fresh CSRF token.
        """
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
        """
        Prepare an authenticated ProxyWebSession and load the target table page to prime CSRF state for tests.
        
        This creates a ProxyWebSession, logs in with default credentials, and fetches the configured server/database/table page so subsequent requests have a valid CSRF token and session.
        """
        self.s = ProxyWebSession()
        self.s.login()
        self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")

    def _pk(self):
        """
        Return the primary-key mapping for the test mysql_servers row.
        
        Returns:
            dict: Mapping of primary key column names to their values:
                - "hostgroup_id": string form of the test hostgroup id
                - "hostname": the test host name
                - "port": string form of the test port
        """
        return {
            "hostgroup_id": str(self.TEST_HG),
            "hostname":     self.TEST_HOST,
            "port":         str(self.TEST_PORT),
        }

    def _insert(self):
        """
        Insert a test row into the configured table using the /api/insert_row endpoint.
        
        Returns:
            dict: The parsed JSON response from the API.
        """
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
        """
        Request deletion of the test row via the ProxyWeb API.
        
        Returns:
            dict: Parsed JSON response from the /api/delete_row endpoint containing the server's result for the delete operation (e.g., success status and any related messages).
        """
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
        """
        Prepare an authenticated ProxyWebSession and load the target table page to prime CSRF state for tests.
        
        This creates a ProxyWebSession, logs in with default credentials, and fetches the configured server/database/table page so subsequent requests have a valid CSRF token and session.
        """
        self.s = ProxyWebSession()
        self.s.login()
        self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")

    def _pk(self):
        """
        Return the primary-key mapping for the test query rule.
        
        Returns:
            dict: A mapping with key "rule_id" and the test rule ID as a string.
        """
        return {"rule_id": str(self.TEST_RULE_ID)}

    def _col_names(self):
        """
        Ordered list of column names used for full-row updates of the mysql_query_rules table.
        
        Returns:
            list: Ordered list of column name strings in the exact sequence required for full updates via the API.
        """
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
        """
        Insert a test query rule into the configured server/table using the /api/insert_row endpoint.
        
        Returns:
            dict: Parsed JSON response from the API containing the result of the insert operation.
        """
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
        """
        Request deletion of the test row via the ProxyWeb API.
        
        Returns:
            dict: Parsed JSON response from the /api/delete_row endpoint containing the server's result for the delete operation (e.g., success status and any related messages).
        """
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
        Verify that selecting id, name, and val from the backend `items` table returns at least one row and that one of the returned rows has the name "alpha".
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, val FROM items ORDER BY id")
                rows = cur.fetchall()
        self.assertGreater(len(rows), 0, "Expected seed rows in testdb.items")
        names = [r[1] for r in rows]
        self.assertIn("alpha", names)

    def test_insert_row(self):
        """
        Inserts a row into the test `items` table, verifies the inserted row persists with the expected values, and removes it.
        
        Asserts that the returned insert ID is greater than zero, that a subsequent select returns the inserted row with name `"ps1-test-insert"` and value `42`, and cleans up by deleting the inserted row.
        """
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
            # FOR UPDATE routes to writer (hg1) to avoid replication lag on the reader
            with self._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT price FROM products WHERE id = %s FOR UPDATE", (row_id,)
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
        # FOR UPDATE routes to writer (hg1) — ensures we verify against the
        # authoritative source, not a potentially-lagging replica
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM products WHERE id = %s FOR UPDATE", (row_id,))
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
        """
        Prepare an authenticated ProxyWebSession and store it as self.s for use by the test methods.
        """
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
        """
        Verify server1's mysql_servers table view is accessible.
        
        Asserts the response status is 200 and the page contains the string "hostname".
        """
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
        """
        Assert that server1's mysql_servers page contains the backend host "mysql".
        """
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
        """
        Return the primary-key mapping for the server2 test query rule.
        
        Returns:
            dict: A mapping with key `"rule_id"` and the rule id as a string.
        """
        return {"rule_id": str(self.TEST_RULE_ID)}

    def _col_names(self):
        """
        Ordered list of column names used for full-row updates of the mysql_query_rules table.
        
        Returns:
            list: Ordered list of column name strings in the exact sequence required for full updates via the API.
        """
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
        """
        Insert a query rule on server S2's `mysql_query_rules` table and return the API response.
        
        The inserted rule uses `self.TEST_RULE_ID` for `rule_id` and sets `active=1`, `match_digest="^DELETE"`,
        `destination_hostgroup=0`, and `apply=1`.
        
        Returns:
            dict: Parsed JSON response returned by the `/api/insert_row` endpoint.
        """
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
        """
        Delete the test query rule on the second server using its primary key.
        
        Returns:
            dict: JSON response from the /api/delete_row endpoint containing the result of the delete operation.
        """
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
# Settings-save regression tests
# ---------------------------------------------------------------------------

class TestSettingsSave(unittest.TestCase):
    """
    Regression tests for /settings/save/.

    Primary bug guarded: _atomic_write() failed with EBUSY (errno 16) or EXDEV
    (errno 18) when the config file is a Docker bind-mount, causing every save
    to return 500.  Fixed by falling back to a direct open()+write() when
    os.replace() raises either errno.
    """

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        resp = self.s.get("/settings/export/")
        body = resp.json()
        self.assertTrue(body.get("success"), f"export failed: {body.get('error')}")
        self._original_yaml = body["yaml"]

    def tearDown(self):
        if hasattr(self, "_original_yaml"):
            payload = {"settings": self._original_yaml, "_csrf_token": self.s.csrf_token}
            self.s.session.post(f"{BASE_URL}/settings/save/", data=payload, timeout=10)

    def test_export_then_save_roundtrip(self):
        """Saving the unmodified exported YAML must succeed (200).
        Catches: _atomic_write EBUSY/EXDEV fallback regression."""
        payload = {"settings": self._original_yaml, "_csrf_token": self.s.csrf_token}
        resp = self.s.session.post(f"{BASE_URL}/settings/save/", data=payload, timeout=10)
        self.assertEqual(resp.status_code, 200,
                         f"save returned {resp.status_code}; body: {resp.text!r}")

    def test_save_invalid_yaml_returns_error(self):
        """Submitting broken YAML must not return 200."""
        payload = {"settings": "not: valid: yaml: [[[", "_csrf_token": self.s.csrf_token}
        resp = self.s.session.post(f"{BASE_URL}/settings/save/", data=payload, timeout=10)
        self.assertNotEqual(resp.status_code, 200,
                            "broken YAML was accepted without error")

    def test_save_missing_required_section_returns_error(self):
        """YAML missing a required section (auth) must not return 200."""
        bad_yaml = "global:\n  default_server: x\nflask:\n  SECRET_KEY: x\nservers:\n  x:\n    dsn: []\nmisc:\n  apply_config: []\n"
        payload = {"settings": bad_yaml, "_csrf_token": self.s.csrf_token}
        resp = self.s.session.post(f"{BASE_URL}/settings/save/", data=payload, timeout=10)
        self.assertNotEqual(resp.status_code, 200,
                            "YAML missing auth section was accepted without error")


# ---------------------------------------------------------------------------
# Hide-tables config tests
# ---------------------------------------------------------------------------

class TestHideTables(unittest.TestCase):
    """Verify that the hide_tables config removes tables from the nav and restores them."""

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        resp = self.s.get("/settings/export/")
        body = resp.json()
        self.assertTrue(body.get("success"), f"settings/export failed: {body.get('error')}")
        self._original_yaml = body["yaml"]

    def tearDown(self):
        if hasattr(self, "_original_yaml"):
            self.s.post_form("/settings/save/", {"settings": self._original_yaml})

    def _save_with_extra_hide_pattern(self, pattern):
        """Prepend *pattern* to the global hide_tables list and save the config."""
        # dict_to_yaml always indents list items under hide_tables with 4 spaces
        modified = self._original_yaml.replace(
            "hide_tables:\n",
            f"hide_tables:\n    - {pattern}\n",
            1,  # only the first occurrence (global section)
        )
        payload = {"settings": modified, "_csrf_token": self.s.csrf_token}
        raw = self.s.session.post(f"{BASE_URL}/settings/save/", data=payload, timeout=10)
        self.assertEqual(raw.status_code, 200,
                         f"POST /settings/save/ returned {raw.status_code}; "
                         f"body: {raw.text!r}")

    def test_hidden_table_absent_from_nav(self):
        """Adding a table to hide_tables must remove its nav link from the home page."""
        # Precondition: mysql_servers appears in the Memory dropdown before hiding
        resp = self.s.get("/")
        self.assertIn("/main/mysql_servers/", resp.text)

        self._save_with_extra_hide_pattern("mysql_servers")

        # GET / always rebuilds session['dblist'] from the updated config
        resp = self.s.get("/")
        self.assertNotIn("/main/mysql_servers/", resp.text)

    def test_unhidden_table_returns_to_nav(self):
        """Restoring the original config brings a previously hidden table back to the nav."""
        self._save_with_extra_hide_pattern("mysql_servers")

        # Confirm it is gone
        resp = self.s.get("/")
        self.assertNotIn("/main/mysql_servers/", resp.text)

        # Restore original config (hide_tables back to its previous state)
        self.s.post_form("/settings/save/", {"settings": self._original_yaml})

        resp = self.s.get("/")
        self.assertIn("/main/mysql_servers/", resp.text)


# ---------------------------------------------------------------------------
# Default server fallback tests
# ---------------------------------------------------------------------------

class TestDefaultServerFallback(unittest.TestCase):
    """
    Regression tests for the hardcoded 'proxysql' server name fallback.

    Bug guarded: app.py used session.get('server', 'proxysql') and
    render_list_dbs read global.default_server directly from config without
    checking whether that name actually exists in the servers dict.
    If the first/only server was not named 'proxysql', the app would crash
    with a KeyError whenever the session lacked a 'server' key.

    Fixed by introducing mdb.get_default_server() which returns the configured
    default_server if it exists in servers, otherwise falls back to the first
    server in the list.
    """

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        resp = self.s.get("/settings/export/")
        body = resp.json()
        self.assertTrue(body.get("success"), f"settings/export failed: {body.get('error')}")
        self._original_yaml = body["yaml"]

    def tearDown(self):
        if hasattr(self, "_original_yaml"):
            self.s.post_form("/settings/save/", {"settings": self._original_yaml})

    def test_home_loads_when_default_server_name_is_wrong(self):
        """/ must return 200 even if global.default_server names a non-existent server.

        Before the fix, render_list_dbs read global.default_server verbatim and
        passed it to get_all_dbs_and_tables, which crashed with a KeyError when
        the name was not in the servers dict.
        """
        # Point default_server at a name that does not exist in servers
        modified = self._original_yaml.replace(
            "default_server: proxysql",
            "default_server: nonexistent_server",
            1,
        )
        resp = self.s.post_form("/settings/save/", {"settings": modified})
        self.assertEqual(resp.status_code, 200,
                         f"save returned {resp.status_code}: {resp.text[:200]!r}")

        # Home page must still load (falls back to first real server)
        resp = self.s.get("/")
        self.assertEqual(resp.status_code, 200,
                         f"/ returned {resp.status_code} after bad default_server; "
                         f"body: {resp.text[:300]!r}")

    def test_execute_proxysql_command_works_without_session_server(self):
        """execute_proxysql_command must work even if 'server' is missing from session.

        Before the fix, session.get('server', 'proxysql') was used; if the only
        server was not named 'proxysql', the command would be executed against
        the wrong (non-existent) server entry and crash.
        """
        # Fresh session: visit /settings/edit/ only (no /) so session has no 'server'
        s2 = ProxyWebSession()
        s2.login()
        s2.get("/settings/edit/")

        # Now call execute_proxysql_command — it must not crash
        resp = s2.post_form(
            "/api/execute_proxysql_command",
            {"sql": "SELECT CONFIG VERSION INTO MEMORY"},
        )
        # We expect success OR a known non-crash error (e.g. read-only), not a 500
        self.assertNotEqual(resp.status_code, 500,
                            f"execute_proxysql_command returned 500 (crash): {resp.text[:300]!r}")


# ---------------------------------------------------------------------------
# Settings page recovery tests
# ---------------------------------------------------------------------------

class TestSettingsEditRecovery(unittest.TestCase):
    """
    Regression tests for the unrecoverable-state bug in base.html.

    Bug guarded: base.html used bare session['misc'], session['history'],
    session['server'], session['database'], session['table'] which raise
    KeyError when the session lacks those keys (e.g. after a broken config
    save or when navigating directly to /settings/edit/ without first
    visiting / to populate the session via render_list_dbs).

    Fixed by replacing all session['key'] accesses with session.get('key', default).
    """

    def test_settings_edit_accessible_without_prior_navigation(self):
        """GET /settings/edit/ with a fresh session (no / visit) must return 200.

        Before the fix, base.html raised KeyError on session['misc'] because
        render_list_dbs had never run, leaving the session empty.  This left
        the user with no way to fix a broken config — navigating to /settings/edit/
        itself crashed with a 500.
        """
        s = ProxyWebSession()
        s.login()
        # Do NOT visit / first — session has no server/table/misc keys
        resp = s.get("/settings/edit/")
        self.assertEqual(resp.status_code, 200,
                         f"/settings/edit/ returned {resp.status_code} on fresh session; "
                         f"body: {resp.text[:300]!r}")
        self.assertIn("settings", resp.text.lower(),
                      "/settings/edit/ response does not look like the settings page")

    def test_settings_edit_accessible_after_nav(self):
        """GET /settings/edit/ also works after visiting / (normal flow)."""
        s = ProxyWebSession()
        s.login()
        s.get("/")  # populate session via render_list_dbs
        resp = s.get("/settings/edit/")
        self.assertEqual(resp.status_code, 200,
                         f"/settings/edit/ returned {resp.status_code} after /; "
                         f"body: {resp.text[:300]!r}")


# ---------------------------------------------------------------------------
# Settings UI server card tests
# ---------------------------------------------------------------------------

class TestSettingsUIServer(unittest.TestCase):
    """
    Regression tests for the structured settings editor — server card bugs.

    Bugs guarded:
    1. dict_to_yaml() rendered DSN list entries as inline JSON-like mappings
       ({"host": ..., "port": ...}) instead of proper YAML block style.
       Fixed by replacing dict_to_yaml_inline() with block rendering in dict_to_yaml().

    2. addServer() in settings.js created a nameless card when the user clicked
       "Add Server" without typing a name — the backend silently dropped the whole
       server entry. Fixed by prompting for the name before card creation and
       aborting if it is empty.
    """

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        resp = self.s.get("/settings/export/")
        body = resp.json()
        self.assertTrue(body.get("success"), f"export failed: {body.get('error')}")
        self._original_yaml = body["yaml"]

    def tearDown(self):
        if hasattr(self, "_original_yaml"):
            self.s.post_form("/settings/save/", {"settings": self._original_yaml})

    def test_add_server_via_ui_form_produces_block_yaml(self):
        """The exported config YAML must use block format for DSN list entries.

        Before the fix, dict_to_yaml() called dict_to_yaml_inline() for dict items in lists,
        producing: - {"host": "proxysql", "port": 6032}
        After the fix the output is proper block YAML:
          - host: proxysql
            user: radmin
            ...
        The test config already has DSN entries, so we can verify by just exporting.
        """
        export = self.s.get("/settings/export/").json()
        self.assertTrue(export.get("success"), f"export failed: {export.get('error')}")
        yaml_text = export["yaml"]

        # The config must have at least one DSN entry
        self.assertIn("dsn:", yaml_text, "No DSN section found in exported YAML")
        # No line should start with '- {' (inline dict format)
        for line in yaml_text.splitlines():
            stripped = line.lstrip()
            self.assertFalse(stripped.startswith("- {"),
                             f"Found inline dict in YAML output: {line!r}")
        # Block format: host key must appear on its own line under dsn:
        self.assertRegex(yaml_text, r"- host: \S",
                         "DSN host not found in block format (expected '- host: ...')")

    def test_add_server_with_empty_name_is_rejected_or_ignored(self):
        """A server submitted with an empty name must not appear in the saved config.

        Before the UI prompt fix, addServer() could create a card with an empty name
        field. The backend's form_data_to_yaml() silently skipped it (if not server_name:
        continue), but the DSN data was lost. This test guards the backend behaviour:
        an empty server name must never produce a config entry.
        """
        form_data = {
            "server_count": "2",
            "server_0_name": "proxysql",
            "server_0_dsn_count": "1",
            "server_0_dsn_0_host": "proxysql",
            "server_0_dsn_0_user": "radmin",
            "server_0_dsn_0_passwd": "radmin",
            "server_0_dsn_0_port": "6032",
            "server_0_dsn_0_db": "main",
            "server_1_name": "",          # empty — should be skipped
            "server_1_dsn_count": "1",
            "server_1_dsn_0_host": "1.2.3.4",
            "server_1_dsn_0_user": "radmin",
            "server_1_dsn_0_passwd": "secret",
            "server_1_dsn_0_port": "6032",
            "server_1_dsn_0_db": "main",
            "global_default_server": "proxysql",
            "auth_admin_user": "admin",
            "auth_admin_password": "admin42",
            "flask_SECRET_KEY": "12345678901234567890",
        }
        # Use raw session post to avoid raise_for_status — the endpoint may
        # reject or accept the form; we care about the resulting config.
        resp = self.s.session.post(
            f"{BASE_URL}/settings/ui_save/",
            data={**form_data, "_csrf_token": self.s.csrf_token},
            timeout=10,
        )
        # Either rejected (4xx) or accepted (200/302) — both are valid outcomes
        if resp.status_code in (200, 302):
            export = self.s.get("/settings/export/").json()
            if export.get("success"):
                yaml_text = export["yaml"]
                # An empty key would appear as "'': " or bare ": "
                self.assertNotIn("\n  '': ", yaml_text,
                                 "Empty server name appeared as quoted empty key in YAML")
                self.assertNotRegex(yaml_text, r"\n  : ",
                                    "Empty server name appeared as bare empty key in YAML")
                # The phantom DSN host (1.2.3.4) must not appear
                self.assertNotIn("1.2.3.4", yaml_text,
                                 "DSN from empty-named server leaked into the YAML output")


# ---------------------------------------------------------------------------
# digest_text display: truncation + toggle badge rendering
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_PYMYSQL, "pymysql not installed — skipping digest_text display tests")
class TestDigestTextDisplay(unittest.TestCase):
    """stats_mysql_query_digest page renders long digest_text with truncation and FA icons.

    Regression guard for the digest_text display fix in show_table_info.html:
    - digest_text must be trimmed (no leading ProxySQL whitespace)
    - truncated at 100 chars (not 60)
    - <td> must use white-space:normal (not pre-wrap)
    - toggle badge must use fa-chevron-right / fa-chevron-down (not raw Unicode arrows)

    Setup: runs several long SELECT queries through the ProxySQL 1 MySQL frontend
    so they appear in stats_mysql_query_digest with a digest_text > 100 chars.
    """

    # ProxySQL MySQL frontend (port 6033) — same as TestProxySQL1BackendSQL
    HOST = os.environ.get("PROXYSQL1_HOST", "127.0.0.1")
    PORT = int(os.environ.get("PROXYSQL1_PORT", "6033"))
    USER = "proxyuser"
    PASS = "proxypass"
    DB   = "testdb"

    # ProxyWeb path for the stats table
    SERVER   = "proxysql"
    DATABASE = "stats"
    TABLE    = "stats_mysql_query_digest"

    # Long queries whose normalized digest_text exceeds 100 chars.
    # ProxySQL replaces literals with ? but keeps identifiers and structure.
    LONG_QUERIES = [
        # ~120 chars after normalization (IN list becomes ?,?,... ; LIMIT becomes ?)
        (
            "SELECT id, name, val FROM items "
            "WHERE id IN (1,2,3,4,5,6,7,8,9,10) "
            "AND name IS NOT NULL AND val IS NOT NULL "
            "ORDER BY id DESC LIMIT 100"
        ),
        # ~115 chars after normalization
        (
            "SELECT id, name, val FROM items "
            "WHERE name LIKE 'test%' AND val BETWEEN 1 AND 9999 "
            "AND id > 0 AND id < 100000 "
            "ORDER BY name ASC, val DESC"
        ),
    ]

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        # Run the long queries through ProxySQL so they appear in stats
        conn = pymysql.connect(
            host=self.HOST, port=self.PORT,
            user=self.USER, password=self.PASS,
            database=self.DB, autocommit=True,
        )
        try:
            with conn.cursor() as cur:
                for query in self.LONG_QUERIES:
                    try:
                        cur.execute(query)
                        cur.fetchall()
                    except Exception:
                        pass  # query may return no rows — that's fine
        finally:
            conn.close()

    def _page(self):
        return self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")

    def test_page_loads(self):
        """stats_mysql_query_digest table page returns 200."""
        resp = self._page()
        self.assertEqual(resp.status_code, 200)

    def test_truncation_elements_present(self):
        """Long digest_text rows must render digest-short, digest-full, and digest-toggle."""
        resp = self._page()
        html = resp.text
        self.assertIn('class="digest-short"', html,
                      "digest-short span missing — long digest_text rows need truncation markup")
        self.assertIn('class="digest-full"', html,
                      "digest-full span missing — long digest_text rows need expand markup")
        self.assertIn('digest-toggle', html,
                      "digest-toggle link missing — long digest_text rows need a toggle")

    def test_toggle_uses_fa_chevron_not_unicode_arrow(self):
        """Toggle badge must use Font Awesome chevron, not raw Unicode arrow characters."""
        resp = self._page()
        html = resp.text
        self.assertIn('fa-chevron-right', html,
                      "fa-chevron-right icon missing from digest toggle badge")
        self.assertNotIn('&#9654;', html,
                         "Raw Unicode arrow &#9654; (▶) must not appear in digest toggle")
        self.assertNotIn('\u25ba', html,
                         "Raw Unicode arrow ▶ must not appear in digest toggle")

    def test_no_leading_whitespace_in_short_span(self):
        """digest-short span content must not start with whitespace (|trim must be applied)."""
        resp = self._page()
        # Match a digest-short span whose content begins with whitespace
        bad = re.search(r'<span class="digest-short">\s+\S', resp.text)
        self.assertIsNone(bad,
                          "digest-short span has leading whitespace — |trim not applied in template")

    def test_td_uses_white_space_normal_not_pre_wrap(self):
        """digest_text <td> must use white-space:normal so newlines are not preserved."""
        resp = self._page()
        html = resp.text
        self.assertIn('white-space:normal', html,
                      "digest_text td must use white-space:normal")
        self.assertNotIn('white-space:pre-wrap', html,
                         "digest_text td must not use white-space:pre-wrap")

    def test_short_span_truncated_at_100(self):
        """digest-short span text must be at most 100 chars (not the old 60-char limit).

        HTML entities (&gt;, &lt;, &amp;) are decoded before measuring length because
        Jinja2 truncates the raw string at 100 chars then auto-escapes, which can inflate
        the HTML representation beyond 100 bytes.
        """
        import html as html_mod
        resp = self._page()
        # Extract all digest-short span contents
        spans = re.findall(r'<span class="digest-short">(.*?)</span>', resp.text)
        self.assertTrue(len(spans) > 0, "No digest-short spans found on page")
        for span_text in spans:
            decoded = html_mod.unescape(span_text)
            self.assertLessEqual(len(decoded), 100,
                                 f"digest-short span is longer than 100 chars: {decoded!r}")


@unittest.skipUnless(HAS_PYMYSQL, "pymysql not installed — skipping pagination tests")
class TestZPagination(unittest.TestCase):
    """stats_mysql_query_digest page serves > 100 rows so DataTables activates pagination.

    Seeds ProxySQL with 1 050 structurally distinct queries via the MySQL frontend (port 6033)
    so that stats_mysql_query_digest has more rows than DataTables' pageLength (100).

    Queries use unique column aliases (col_1, col_2, …) so ProxySQL assigns a distinct digest
    to each one — without this, all `SELECT ? AS n` queries collapse to a single digest row.

    Pagination itself is rendered by DataTables in JavaScript after page load, so it is not
    present in the server-rendered HTML.  We instead assert that the server returns > 100
    <tr> rows in the table body, which guarantees DataTables will activate pagination on the
    client.
    """

    HOST = os.environ.get("PROXYSQL1_HOST", "127.0.0.1")
    PORT = int(os.environ.get("PROXYSQL1_PORT", "6033"))
    USER = "proxyuser"
    PASS = "proxypass"
    DB   = "testdb"

    SERVER   = "proxysql"
    DATABASE = "stats"
    TABLE    = "stats_mysql_query_digest"

    QUERY_COUNT = 1050

    @classmethod
    def setUpClass(cls):
        """Fire QUERY_COUNT structurally distinct queries through the ProxySQL MySQL frontend."""
        conn = pymysql.connect(
            host=cls.HOST, port=cls.PORT,
            user=cls.USER, password=cls.PASS,
            database=cls.DB, autocommit=True,
        )
        try:
            with conn.cursor() as cur:
                for i in range(1, cls.QUERY_COUNT + 1):
                    # Unique column alias per query → unique digest in ProxySQL
                    # (ProxySQL normalises literals to ? but keeps identifiers as-is)
                    try:
                        cur.execute(f"SELECT 1 AS col_{i}")
                        cur.fetchall()
                    except Exception:
                        pass
        finally:
            conn.close()

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()

    def _page(self):
        return self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")

    def test_page_loads(self):
        """stats_mysql_query_digest page returns 200 after seeding > 1000 digests."""
        resp = self._page()
        self.assertEqual(resp.status_code, 200)

    def test_pagination_row_count(self):
        """Server must return > 100 tbody rows so DataTables activates client-side pagination.

        DataTables pagination is rendered entirely in JavaScript and therefore not present
        in resp.text.  The correct server-side signal is the number of <tr> elements inside
        <tbody>: when that count exceeds DataTables' pageLength (100 in base.html), DataTables
        will render Previous/Next controls after the page loads in a browser.
        """
        resp = self._page()
        tbody_match = re.search(r'<tbody>(.*?)</tbody>', resp.text, re.DOTALL)
        self.assertIsNotNone(tbody_match,
                             "No <tbody> found on stats_mysql_query_digest page")
        data_rows = tbody_match.group(1).count('<tr>')
        self.assertGreater(data_rows, 100,
                           f"Expected > 100 rows in tbody so DataTables paginates, "
                           f"got {data_rows} after seeding {self.QUERY_COUNT} digests")


# ---------------------------------------------------------------------------
# Read-only user role
# ---------------------------------------------------------------------------

class TestReadOnlyUser(unittest.TestCase):
    """Read-only user can browse but cannot modify data, settings, or execute non-SELECT SQL."""

    RO_USER = "readonly"
    RO_PASS = "readonly42"

    def _ro_session(self):
        """Return a ProxyWebSession logged in as the readonly user."""
        s = ProxyWebSession()
        s.login(username=self.RO_USER, password=self.RO_PASS)
        return s

    def test_readonly_login(self):
        """Readonly user can log in and sees tables."""
        s = self._ro_session()
        resp = s.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("main", resp.text)

    def test_readonly_cannot_insert_row(self):
        """API insert returns 403 for readonly user."""
        s = self._ro_session()
        s.get(f"/{SERVER}/{DATABASE}/global_variables/")
        resp = s.session.post(
            f"{BASE_URL}/api/insert_row",
            json={
                "server": SERVER, "database": DATABASE,
                "table": "global_variables",
                "columnNames": ["variable_name", "variable_value"],
                "data": ["test_var", "test_val"],
            },
            headers={"Content-Type": "application/json", "X-CSRF-Token": s.csrf_token},
            timeout=10,
        )
        self.assertEqual(resp.status_code, 403)

    def test_readonly_cannot_delete_row(self):
        """API delete returns 403 for readonly user."""
        s = self._ro_session()
        s.get(f"/{SERVER}/{DATABASE}/global_variables/")
        resp = s.session.post(
            f"{BASE_URL}/api/delete_row",
            json={
                "server": SERVER, "database": DATABASE,
                "table": "global_variables",
                "pkValues": {"variable_name": "nonexistent"},
            },
            headers={"Content-Type": "application/json", "X-CSRF-Token": s.csrf_token},
            timeout=10,
        )
        self.assertEqual(resp.status_code, 403)

    def test_readonly_cannot_update_row(self):
        """API update returns 403 for readonly user."""
        s = self._ro_session()
        s.get(f"/{SERVER}/{DATABASE}/global_variables/")
        resp = s.session.post(
            f"{BASE_URL}/api/update_row",
            json={
                "server": SERVER, "database": DATABASE,
                "table": "global_variables",
                "pkValues": ["mysql-threads"],
                "columnNames": ["variable_name", "variable_value"],
                "data": ["mysql-threads", "99"],
            },
            headers={"Content-Type": "application/json", "X-CSRF-Token": s.csrf_token},
            timeout=10,
        )
        self.assertEqual(resp.status_code, 403)

    def test_readonly_cannot_access_settings(self):
        """Settings pages return 403 for readonly user."""
        s = self._ro_session()
        resp = s.session.get(f"{BASE_URL}/settings/edit/", timeout=10)
        self.assertEqual(resp.status_code, 403)

    def test_readonly_can_view_config_diff(self):
        """Config diff page is accessible to readonly user."""
        s = self._ro_session()
        resp = s.get(f"/{SERVER}/config_diff/")
        self.assertEqual(resp.status_code, 200)

    def test_readonly_cannot_execute_proxysql_command(self):
        """LOAD/SAVE commands are blocked for readonly user."""
        s = self._ro_session()
        s.get(f"/{SERVER}/{DATABASE}/global_variables/")
        resp = s.session.post(
            f"{BASE_URL}/api/execute_proxysql_command",
            data={"sql": "LOAD MYSQL USERS TO RUNTIME", "_csrf_token": s.csrf_token},
            timeout=10,
        )
        data = resp.json()
        self.assertFalse(data.get("success"))

    def test_readonly_sees_sql_editor(self):
        """SQL editor card is present for readonly user despite read_only=True."""
        s = self._ro_session()
        resp = s.get(f"/{SERVER}/{DATABASE}/global_variables/")
        self.assertIn("SQL Query Editor", resp.text)

    def test_readonly_sql_select_allowed(self):
        """Readonly user can execute SELECT queries."""
        s = self._ro_session()
        s.get(f"/{SERVER}/{DATABASE}/global_variables/")
        resp = s.post_form(
            f"/{SERVER}/{DATABASE}/global_variables/sql/",
            data={"sql": "SELECT 1 AS test FROM global_variables LIMIT 1"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("Read-only user cannot execute", resp.text)

    def test_readonly_sql_insert_blocked(self):
        """Non-SELECT SQL is blocked for readonly user."""
        s = self._ro_session()
        s.get(f"/{SERVER}/{DATABASE}/global_variables/")
        resp = s.post_form(
            f"/{SERVER}/{DATABASE}/global_variables/sql/",
            data={"sql": "INSERT INTO global_variables VALUES ('test_ro', 'val')"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Read-only user cannot execute", resp.text)


# ---------------------------------------------------------------------------
# Default credentials hint on login page
# ---------------------------------------------------------------------------

class TestDefaultCredentialsHint(unittest.TestCase):
    """Login page shows a credential hint when default passwords are in use."""

    def setUp(self):
        self.s = ProxyWebSession()

    def test_login_shows_default_creds_hint(self):
        """With default config, GET /login contains 'Default credentials detected'."""
        resp = self.s.session.get(f"{BASE_URL}/login", timeout=10)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Default credentials detected", resp.text)
        self.assertIn("admin42", resp.text)

    def test_login_hides_hint_after_password_change(self):
        """Changing both passwords removes the default credentials hint."""
        self.s.login()
        resp = self.s.get("/settings/export/")
        body = resp.json()
        self.assertTrue(body.get("success"))
        original_yaml = body["yaml"]

        try:
            modified = original_yaml.replace("admin_password: admin42",
                                             "admin_password: changed123")
            modified = modified.replace("readonly_password: readonly42",
                                        "readonly_password: changed456")
            self.s.post_form("/settings/save/", {"settings": modified})

            resp2 = self.s.session.get(f"{BASE_URL}/login", timeout=10)
            self.assertNotIn("Default credentials detected", resp2.text)
        finally:
            # Visiting /login clears session; re-login with new creds to restore
            restore = ProxyWebSession()
            restore.login(username="admin", password="changed123")
            restore.post_form("/settings/save/", {"settings": original_yaml})


# ---------------------------------------------------------------------------
# No-servers redirect to settings
# ---------------------------------------------------------------------------

class TestNoServersRedirect(unittest.TestCase):
    """When servers: is empty, admin is redirected to settings; readonly gets an error."""

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        resp = self.s.get("/settings/export/")
        body = resp.json()
        self.assertTrue(body.get("success"))
        self._original_yaml = body["yaml"]

    def tearDown(self):
        if hasattr(self, "_original_yaml"):
            self.s.post_form("/settings/save/", {"settings": self._original_yaml})

    def _save_empty_servers(self):
        """Replace the servers section with an empty dict."""
        # Remove all server entries, leaving just "servers: {}"
        modified = re.sub(
            r'servers:\n(?:  \S.*\n(?:    .*\n)*)*',
            'servers: {}\n',
            self._original_yaml,
        )
        self.s.post_form("/settings/save/", {"settings": modified})

    def test_no_servers_redirects_admin_to_settings(self):
        """Admin GET / with empty servers redirects to /settings/edit/."""
        self._save_empty_servers()
        resp = self.s.session.get(f"{BASE_URL}/", timeout=10, allow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("/settings/", resp.url)

    def test_no_servers_readonly_gets_error(self):
        """Readonly GET / with empty servers returns error page."""
        self._save_empty_servers()
        ro = ProxyWebSession()
        # Login follows redirect to / which returns 503; don't raise
        resp = ro.session.post(
            f"{BASE_URL}/login",
            data={"username": "readonly", "password": "readonly42"},
            allow_redirects=True,
            timeout=10,
        )
        self.assertEqual(resp.status_code, 503)
        self.assertIn("No servers configured", resp.text)


# ---------------------------------------------------------------------------
# Colored + numbered test runner
# ---------------------------------------------------------------------------

class _ColoredResult(unittest.TestResult):
    """Prints a numbered, colored one-liner per test as it runs."""

    _GREEN  = '\033[32m'
    _RED    = '\033[31m'
    _YELLOW = '\033[33m'
    _CYAN   = '\033[36m'
    _BOLD   = '\033[1m'
    _RESET  = '\033[0m'

    def __init__(self, stream, total):
        super().__init__()
        self.stream = stream
        self.total  = total
        self._n     = 0
        self._t0    = None
        self._width = len(str(total))

    def startTest(self, test):
        super().startTest(test)
        self._n  += 1
        self._t0  = time.monotonic()
        counter   = f"{self._n:{self._width}}/{self.total}"
        name      = f"{test.__class__.__name__}.{test._testMethodName}"
        self.stream.write(f"  {self._CYAN}{counter}{self._RESET}  {name} ")
        self.stream.flush()

    def _elapsed(self):
        return f"({time.monotonic() - self._t0:.2f}s)"

    def addSuccess(self, test):
        self.stream.write(f"{self._GREEN}✓ pass{self._RESET}  {self._elapsed()}\n")
        self.stream.flush()

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.stream.write(f"{self._RED}✗ FAIL{self._RESET}  {self._elapsed()}\n")
        self.stream.flush()

    def addError(self, test, err):
        super().addError(test, err)
        self.stream.write(f"{self._RED}✗ ERROR{self._RESET} {self._elapsed()}\n")
        self.stream.flush()

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.stream.write(f"{self._YELLOW}⊘ skip{self._RESET}  {self._elapsed()}  {reason}\n")
        self.stream.flush()

    def printErrors(self):
        if not (self.failures or self.errors):
            return
        self.stream.write("\n")
        for label, cases in [("FAIL", self.failures), ("ERROR", self.errors)]:
            for test, tb in cases:
                self.stream.write("=" * 70 + "\n")
                self.stream.write(f"{self._RED}{label}: {test}{self._RESET}\n")
                self.stream.write("-" * 70 + "\n")
                self.stream.write(tb)
                self.stream.write("\n")


class _ColoredRunner:
    _GREEN = '\033[32m'
    _RED   = '\033[31m'
    _BOLD  = '\033[1m'
    _RESET = '\033[0m'

    def run(self, suite):
        total  = suite.countTestCases()
        out    = sys.stderr
        out.write(f"\n{self._BOLD}Running {total} tests...{self._RESET}\n\n")
        out.flush()

        result = _ColoredResult(out, total)
        t0     = time.monotonic()
        suite.run(result)
        elapsed = time.monotonic() - t0

        result.printErrors()

        passed  = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
        parts   = [f"{self._GREEN}{passed} passed{self._RESET}"]
        if result.failures:
            parts.append(f"{self._RED}{len(result.failures)} failed{self._RESET}")
        if result.errors:
            parts.append(f"{self._RED}{len(result.errors)} errors{self._RESET}")
        if result.skipped:
            parts.append(f"{len(result.skipped)} skipped")

        out.write(f"\n{self._BOLD}Results:{self._RESET}  {', '.join(parts)}  ({elapsed:.1f}s)\n\n")
        out.flush()
        return result


class TestQueryHistory(unittest.TestCase):
    """Verify per-server persistent query history across two servers.

    Runs 26 unique queries per server (different templates and LIMIT values),
    checks that:
    - the Query History dropdown shows only the last 10 for each server
    - the Full Query History page shows all queries for each server
    - histories are isolated: server1 queries don't leak into server2
    - clear works per server without affecting the other
    """

    S1 = SERVER            # "proxysql"
    S2 = "proxysql2"
    TOTAL_PER_SERVER = 26

    # Server 1: each query selects different columns + different LIMIT
    S1_TEMPLATES = [
        "SELECT variable_name FROM global_variables LIMIT {n}",
        "SELECT variable_value FROM global_variables LIMIT {n}",
        "SELECT variable_name, variable_value FROM global_variables LIMIT {n}",
        "SELECT variable_name FROM global_variables WHERE variable_name LIKE 'mysql%%' LIMIT {n}",
        "SELECT variable_value FROM global_variables WHERE variable_name LIKE 'mysql%%' LIMIT {n}",
        "SELECT variable_name, variable_value FROM global_variables WHERE variable_name LIKE 'admin%%' LIMIT {n}",
        "SELECT variable_name FROM global_variables ORDER BY variable_name LIMIT {n}",
        "SELECT variable_value FROM global_variables ORDER BY variable_name LIMIT {n}",
        "SELECT variable_name, variable_value FROM global_variables ORDER BY variable_name LIMIT {n}",
        "SELECT variable_name FROM global_variables ORDER BY variable_name DESC LIMIT {n}",
        "SELECT variable_value FROM global_variables ORDER BY variable_name DESC LIMIT {n}",
        "SELECT variable_name, variable_value FROM global_variables ORDER BY variable_name DESC LIMIT {n}",
        "SELECT variable_name FROM global_variables ORDER BY variable_value LIMIT {n}",
        "SELECT variable_value FROM global_variables ORDER BY variable_value LIMIT {n}",
        "SELECT variable_name, variable_value FROM global_variables ORDER BY variable_value LIMIT {n}",
        "SELECT variable_name FROM global_variables ORDER BY variable_value DESC LIMIT {n}",
        "SELECT variable_value FROM global_variables ORDER BY variable_value DESC LIMIT {n}",
        "SELECT variable_name, variable_value FROM global_variables ORDER BY variable_value DESC LIMIT {n}",
        "SELECT COUNT(*) FROM global_variables WHERE variable_name LIKE 'mysql%%' LIMIT {n}",
        "SELECT COUNT(*) FROM global_variables WHERE variable_name LIKE 'admin%%' LIMIT {n}",
        "SELECT COUNT(*) FROM global_variables LIMIT {n}",
        "SELECT variable_name FROM global_variables WHERE variable_value != '' LIMIT {n}",
        "SELECT variable_value FROM global_variables WHERE variable_value != '' LIMIT {n}",
        "SELECT variable_name FROM global_variables WHERE variable_name LIKE 'admin%%' ORDER BY variable_name LIMIT {n}",
        "SELECT variable_value FROM global_variables WHERE variable_name LIKE 'admin%%' ORDER BY variable_name LIMIT {n}",
        "SELECT variable_name, variable_value FROM global_variables WHERE variable_value != '' ORDER BY variable_name LIMIT {n}",
    ]

    # Server 2: connection-pool-style queries, each different
    S2_TEMPLATES = [
        "SELECT ConnUsed FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnFree FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnOK FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnERR FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT MaxConnUsed FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT Queries FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT Bytes_data_sent FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnUsed, ConnFree FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnOK, ConnERR FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT MaxConnUsed, Queries FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnUsed, ConnFree, ConnOK FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnERR, MaxConnUsed, Queries FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT Bytes_data_sent, ConnUsed FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnUsed FROM stats_mysql_connection_pool ORDER BY ConnUsed LIMIT {n}",
        "SELECT ConnFree FROM stats_mysql_connection_pool ORDER BY ConnFree LIMIT {n}",
        "SELECT ConnOK FROM stats_mysql_connection_pool ORDER BY ConnOK LIMIT {n}",
        "SELECT ConnERR FROM stats_mysql_connection_pool ORDER BY ConnERR LIMIT {n}",
        "SELECT MaxConnUsed FROM stats_mysql_connection_pool ORDER BY MaxConnUsed LIMIT {n}",
        "SELECT Queries FROM stats_mysql_connection_pool ORDER BY Queries LIMIT {n}",
        "SELECT Bytes_data_sent FROM stats_mysql_connection_pool ORDER BY Bytes_data_sent LIMIT {n}",
        "SELECT ConnUsed, ConnFree FROM stats_mysql_connection_pool ORDER BY ConnUsed DESC LIMIT {n}",
        "SELECT ConnOK, ConnERR FROM stats_mysql_connection_pool ORDER BY ConnOK DESC LIMIT {n}",
        "SELECT MaxConnUsed, Queries FROM stats_mysql_connection_pool ORDER BY Queries DESC LIMIT {n}",
        "SELECT Bytes_data_sent FROM stats_mysql_connection_pool ORDER BY Bytes_data_sent DESC LIMIT {n}",
        "SELECT ConnUsed, ConnFree, ConnOK, ConnERR FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnUsed, ConnFree, ConnOK, ConnERR, MaxConnUsed, Queries, Bytes_data_sent FROM stats_mysql_connection_pool LIMIT {n}",
    ]

    @classmethod
    def setUpClass(cls):
        cls.pw = ProxyWebSession()
        cls.pw.login()
        # clear any pre-existing history on both servers
        cls.pw.post_json("/api/clear_query_history", {"server": cls.S1})
        cls.pw.post_json("/api/clear_query_history", {"server": cls.S2})

    def _execute_query(self, server, sql):
        """Submit a SELECT through the SQL form on the given server."""
        return self.pw.post_form(
            f"/{server}/{DATABASE}/global_variables/sql/",
            data={"sql": sql},
        )

    def _get_dropdown_section(self, html):
        """Extract the Query History dropdown HTML from a table page."""
        return html.split('id="historyBtn"')[1].split('</ul>')[0]

    def _get_history_table_section(self, html):
        """Extract the history table body from the full query history page."""
        if 'id="historyTable"' not in html:
            return ""
        return html.split('id="historyTable"')[1].split('</table>')[0]

    def _count_dropdown_items(self, html):
        """Count history_item links in the page."""
        return len(re.findall(
            r'onclick="loadQuery\(window\[\'history_item_\d+\'\]\)',
            html,
        ))

    def _get_history_js_vars(self, html):
        """Extract the full SQL strings from history_item JS variables."""
        return re.findall(
            r"window\['history_item_\d+'\]\s*=\s*\"(.*?)\"",
            html,
        )

    def _get_history_limits(self, js_vars):
        """Extract the LIMIT N numbers from history JS variable strings."""
        limits = []
        for sql in js_vars:
            m = re.search(r'LIMIT (\d+)$', sql)
            if m:
                limits.append(int(m.group(1)))
        return limits

    def test_query_history_both_servers(self):
        """Execute 26 distinct queries on each server, verify dropdown
        and full history page per server, then clear and verify isolation."""

        # --- execute 26 distinct queries on server 1 ----------------------
        for n in range(1, self.TOTAL_PER_SERVER + 1):
            sql = self.S1_TEMPLATES[n - 1].format(n=n)
            resp = self._execute_query(self.S1, sql)
            self.assertEqual(resp.status_code, 200,
                             f"S1 query {n} HTTP failed: {resp.status_code}")
            self.assertNotIn("Query Error", resp.text,
                             f"S1 query {n} returned an error")

        # --- execute 26 distinct queries on server 2 ----------------------
        for n in range(1, self.TOTAL_PER_SERVER + 1):
            sql = self.S2_TEMPLATES[n - 1].format(n=n)
            resp = self._execute_query(self.S2, sql)
            self.assertEqual(resp.status_code, 200,
                             f"S2 query {n} HTTP failed: {resp.status_code}")
            self.assertNotIn("Query Error", resp.text,
                             f"S2 query {n} returned an error")

        # === Server 1 checks =============================================

        # -- dropdown: should show last 10 of 26 (queries 17..26) ----------
        resp = self.pw.get(f"/{self.S1}/{DATABASE}/global_variables/")
        s1_html = resp.text
        self.assertEqual(self._count_dropdown_items(s1_html), 10,
                         "S1 dropdown should have exactly 10 items")

        # Check full SQL via JS variables (dropdown display is truncated)
        s1_js_vars = self._get_history_js_vars(s1_html)
        s1_limits = self._get_history_limits(s1_js_vars)
        # last 10 should be present (LIMIT 17 through LIMIT 26)
        for n in range(self.TOTAL_PER_SERVER - 9, self.TOTAL_PER_SERVER + 1):
            self.assertIn(n, s1_limits,
                          f"S1 dropdown missing LIMIT {n}")
        # oldest 16 should NOT be in dropdown
        for n in range(1, self.TOTAL_PER_SERVER - 9):
            self.assertNotIn(n, s1_limits,
                             f"S1 dropdown should not contain LIMIT {n}")

        # -- S1 dropdown must NOT contain S2-specific text -----------------
        s1_js_text = "\n".join(s1_js_vars)
        self.assertNotIn("stats_mysql_connection_pool", s1_js_text,
                         "S1 dropdown leaks S2 queries")

        # -- full history page: all 26 ------------------------------------
        resp = self.pw.get(f"/{self.S1}/query_history/")
        s1_full = resp.text
        s1_table = self._get_history_table_section(s1_full)
        self.assertIn("26 queries", s1_full,
                       "S1 full history should show '26 queries'")
        for n in range(1, self.TOTAL_PER_SERVER + 1):
            self.assertRegex(s1_table, rf'LIMIT {n}\b',
                             f"S1 full history missing LIMIT {n}")
        # S2 queries must not appear on S1 full history table
        self.assertNotIn("stats_mysql_connection_pool", s1_table,
                         "S1 full history leaks S2 queries")

        # === Server 2 checks =============================================

        # -- dropdown: should show last 10 of 26 (queries 17..26) ----------
        resp = self.pw.get(f"/{self.S2}/{DATABASE}/global_variables/")
        s2_html = resp.text
        self.assertEqual(self._count_dropdown_items(s2_html), 10,
                         "S2 dropdown should have exactly 10 items")

        s2_js_vars = self._get_history_js_vars(s2_html)
        s2_limits = self._get_history_limits(s2_js_vars)
        for n in range(self.TOTAL_PER_SERVER - 9, self.TOTAL_PER_SERVER + 1):
            self.assertIn(n, s2_limits,
                          f"S2 dropdown missing LIMIT {n}")
        for n in range(1, self.TOTAL_PER_SERVER - 9):
            self.assertNotIn(n, s2_limits,
                             f"S2 dropdown should not contain LIMIT {n}")

        # -- S2 dropdown must NOT contain S1-specific text -----------------
        s2_js_text = "\n".join(s2_js_vars)
        self.assertNotIn("global_variables", s2_js_text,
                         "S2 dropdown leaks S1 queries")

        # -- full history page: all 26 ------------------------------------
        resp = self.pw.get(f"/{self.S2}/query_history/")
        s2_full = resp.text
        s2_table = self._get_history_table_section(s2_full)
        self.assertIn("26 queries", s2_full,
                       "S2 full history should show '26 queries'")
        for n in range(1, self.TOTAL_PER_SERVER + 1):
            self.assertRegex(s2_table, rf'LIMIT {n}\b',
                             f"S2 full history missing LIMIT {n}")
        self.assertNotIn("global_variables", s2_table,
                         "S2 full history leaks S1 queries")

        # === Clear server 1, verify server 2 is untouched ================
        clear_resp = self.pw.post_json(
            "/api/clear_query_history", {"server": self.S1}
        )
        self.assertTrue(clear_resp.json().get("success"))

        # S1 should be empty
        resp = self.pw.get(f"/{self.S1}/query_history/")
        self.assertIn("No query history", resp.text)
        self.assertIn("0 queries", resp.text)

        resp = self.pw.get(f"/{self.S1}/{DATABASE}/global_variables/")
        s1_dropdown = self._get_dropdown_section(resp.text)
        self.assertIn("No queries yet", s1_dropdown)

        # S2 should still have all its queries
        resp = self.pw.get(f"/{self.S2}/query_history/")
        self.assertIn("26 queries", resp.text,
                       "S2 history should survive S1 clear")

        # === Clean up: clear server 2 too ================================
        self.pw.post_json("/api/clear_query_history", {"server": self.S2})
        resp = self.pw.get(f"/{self.S2}/query_history/")
        self.assertIn("No query history", resp.text)


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
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromModule(sys.modules[__name__])
    result = _ColoredRunner().run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
