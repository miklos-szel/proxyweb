#!/usr/bin/env python3
"""Authentication, roles, default credentials, and no-servers redirect."""

import re
import unittest

import requests

from testlib import (
    ProxyWebSession, BASE_URL, USERNAME, PASSWORD,
    SERVER, PG_SERVER, DATABASE,
)


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
            "default_server: proxysql_mysql",
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

if __name__ == "__main__":
    unittest.main()
