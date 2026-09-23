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


class TestReadOnlyServerSqlForm(unittest.TestCase):
    """A server flagged read_only must reject non-SELECT SQL from the form.

    Bug guarded: render_change only checked the readonly *role*; it never
    called mdb.get_read_only(server). The SQL editor is hidden in the template
    when the server is read-only, but an admin could still POST a write
    directly to /<server>/<db>/<table>/sql/ against a read_only: true server.
    SELECT statements must still be allowed.
    """

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        resp = self.s.get("/settings/export/")
        body = resp.json()
        self.assertTrue(body.get("success"), f"export failed: {body.get('error')}")
        self._original_yaml = body["yaml"]
        # Add a per-server read_only: true override on the MySQL ProxySQL server.
        modified = re.sub(
            r'(^  %s:\n)' % re.escape(SERVER),
            r'\1    read_only: true\n',
            self._original_yaml,
            count=1,
            flags=re.M,
        )
        self.assertIn("read_only: true", modified, "failed to inject read_only override")
        self.s.post_form("/settings/save/", {"settings": modified})

    LEAK_RULE_ID = 922

    def tearDown(self):
        if hasattr(self, "_original_yaml"):
            # Restore a writable config first, then clean up any row that
            # leaked through if the read-only block had regressed.
            self.s.post_form("/settings/save/", {"settings": self._original_yaml})
            self.s.post_json("/api/delete_row", {
                "server": SERVER, "database": DATABASE, "table": "mysql_query_rules",
                "pkValues": {"rule_id": str(self.LEAK_RULE_ID)},
            })

    def test_non_select_blocked_on_read_only_server(self):
        """An INSERT via the SQL form must not reach a read_only server.

        The editor is hidden in the UI for read-only servers, so we verify the
        security property directly: after POSTing the write, the row is absent.
        """
        self.s.get(f"/{SERVER}/{DATABASE}/mysql_query_rules/")
        self.s.post_form(
            f"/{SERVER}/{DATABASE}/mysql_query_rules/sql/",
            {"sql": f"INSERT INTO mysql_query_rules (rule_id, active, apply) "
                    f"VALUES ({self.LEAK_RULE_ID}, 1, 1)"},
        )
        data = self.s.get_table_data(SERVER, DATABASE, "mysql_query_rules",
                                     **{"length": "1000"})
        ids = {str(row[0]) for row in data.get("data", [])}
        self.assertNotIn(str(self.LEAK_RULE_ID), ids,
                         "write reached a read_only server through the SQL form")

    def test_select_allowed_on_read_only_server(self):
        """SELECT is still permitted against a read_only server and renders rows."""
        self.s.get(f"/{SERVER}/{DATABASE}/global_variables/")
        resp = self.s.post_form(
            f"/{SERVER}/{DATABASE}/global_variables/sql/",
            {"sql": "SELECT variable_name FROM global_variables LIMIT 1"},
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


class TestShellMetacharPayloadRoundTrip(unittest.TestCase):
    """Regression: values with shell metacharacters and newlines must be stored
    literally via /api/insert_row, not interpreted by a shell.

    ``execute_change`` previously invoked the mysql CLI with
    ``subprocess.Popen(cmd, shell=True)``, interpolating the SQL into a bash
    ``-c`` string. This test guards against regressions where somebody
    reintroduces shell-based execution: the inserted comment contains newlines,
    semicolons, pipes, redirections, backticks, ``$(...)`` and ``${...}``. If
    any shell expansion were to occur, the round-tripped value would differ
    from the original.
    """

    SERVER   = SERVER
    DATABASE = "main"
    TABLE    = "mysql_servers"

    TEST_HOST = "test-shellinj-host"
    TEST_HG   = 98
    TEST_PORT = 3398

    PAYLOAD = (
        "safe\n"
        "; touch /tmp/pwn_proxyweb_shellinj ;\n"
        "`touch /tmp/pwn_backtick` $(touch /tmp/pwn_cmdsub) "
        "${IFS}|&<>\"'\\"
    )

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")

    def _pk_values(self):
        return {
            "hostgroup_id": str(self.TEST_HG),
            "hostname":     self.TEST_HOST,
            "port":         str(self.TEST_PORT),
        }

    def tearDown(self):
        try:
            self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")
            self.s.post_json("/api/delete_row", {
                "server":   self.SERVER,
                "database": self.DATABASE,
                "table":    self.TABLE,
                "pkValues": self._pk_values(),
            })
        except Exception:
            pass

    def test_shell_metachars_in_comment_are_stored_literally(self):
        insert = self.s.post_json("/api/insert_row", {
            "server":      self.SERVER,
            "database":    self.DATABASE,
            "table":       self.TABLE,
            "columnNames": ["hostgroup_id", "hostname", "port", "comment"],
            "data": {
                "hostgroup_id": str(self.TEST_HG),
                "hostname":     self.TEST_HOST,
                "port":         str(self.TEST_PORT),
                "comment":      self.PAYLOAD,
            },
        }).json()
        self.assertTrue(insert.get("success"),
                        f"Insert failed: {insert.get('error')}")

        data = self.s.get_table_data(self.SERVER, self.DATABASE, self.TABLE,
                                     **{"search[value]": self.TEST_HOST})
        rows = data.get("data", [])
        cols = data.get("column_names", [])
        self.assertTrue(rows, "Inserted row not found via /api/table_data")

        host_idx    = cols.index("hostname")
        comment_idx = cols.index("comment")
        match = next((r for r in rows if r[host_idx] == self.TEST_HOST), None)
        self.assertIsNotNone(match,
                             f"No row with hostname={self.TEST_HOST} in {rows!r}")

        self.assertEqual(
            match[comment_idx], self.PAYLOAD,
            "Comment did not round-trip verbatim — shell or SQL quoting may "
            "have stripped/rewritten metacharacters.",
        )


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
                # mysql_users' primary key is (username, backend). Sending only
                # username made delete_row match `backend IS NULL` — zero rows —
                # so the user leaked into the next run's INSERT.
                "pkValues": {"username": self.TEST_USER, "backend": "1"},
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
            "columnNames": ["username", "password", "default_hostgroup",
                            "default_schema", "active"],
            "data": {
                "username":          self.TEST_USER,
                "password":          "difftest-pass",
                "default_hostgroup": "0",
                "default_schema":    "",
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


class ConfigDiffTestBase(unittest.TestCase):
    """Shared plumbing for tests that inspect POST /<server>/config_diff/get."""

    SERVER = SERVER
    DB     = "main"

    def _login(self, table):
        """Authenticated session with a fresh CSRF token and session['server'] set."""
        self.s = ProxyWebSession()
        self.s.login()
        self.s.get(f"/{self.SERVER}/{self.DB}/{table}/")

    def _run_sql(self, table, sql):
        """Execute a statement through the SQL editor route (admin only)."""
        self.s.get(f"/{self.SERVER}/{self.DB}/{table}/")
        return self.s.post_form(f"/{self.SERVER}/{self.DB}/{table}/sql/", {"sql": sql})

    def _admin_command(self, sql):
        """Run LOAD/SAVE statements through /api/execute_proxysql_command."""
        body = self.s.post_form("/api/execute_proxysql_command", {"sql": sql}).json()
        self.assertTrue(body.get("success"),
                        f"admin command failed ({sql}): {body.get('error')}")

    def _table_diff(self, table):
        """Return the config diff entry for one table."""
        self.s.get(f"/{self.SERVER}/config_diff/")
        body = self.s.post_json(f"/{self.SERVER}/config_diff/get", {}).json()
        self.assertTrue(body.get("success"), body.get("error"))
        entry = next((t for t in body.get("diff", {}).get("tables", [])
                      if t.get("table_name") == table), None)
        self.assertIsNotNone(entry, f"{table} missing from config diff tables list")
        return entry

    @staticmethod
    def _memory_vs_runtime(entry, key):
        return entry.get("differences", {}).get("memory_vs_runtime", {}).get(key, [])


class TestConfigDiffInactiveRows(ConfigDiffTestBase):
    """Rows with active=0 must not be reported as memory-vs-runtime drift.

    Regression: ProxySQL deliberately never loads a row with ``active = 0``
    into the runtime layer, so an inactive mysql_query_rules row present on
    disk and in memory is correctly absent from runtime_mysql_query_rules.
    The config diff counted that absence as a difference and flagged a fully
    in-sync server with "Changes Detected".
    """

    TABLE             = "mysql_query_rules"
    INACTIVE_RULE_ID  = 990
    ACTIVE_RULE_ID    = 991

    def setUp(self):
        self._login(self.TABLE)

    def tearDown(self):
        try:
            self._run_sql(self.TABLE,
                          f"DELETE FROM mysql_query_rules WHERE rule_id IN "
                          f"({self.INACTIVE_RULE_ID}, {self.ACTIVE_RULE_ID})")
            self._admin_command("LOAD MYSQL QUERY RULES TO RUNTIME; "
                                "SAVE MYSQL QUERY RULES TO DISK")
        except Exception:
            pass

    def _insert_rule(self, rule_id, active):
        self._run_sql(self.TABLE,
                      f"INSERT INTO mysql_query_rules "
                      f"(rule_id, active, match_digest, destination_hostgroup, apply) "
                      f"VALUES ({rule_id}, {active}, '^SELECT {rule_id}', 2, 1)")
        data = self.s.get_table_data(self.SERVER, self.DB, self.TABLE,
                                     **{"length": "1000"})
        ids = {str(row[0]) for row in data.get("data", [])}
        self.assertIn(str(rule_id), ids, f"rule {rule_id} was not inserted")

    def test_inactive_rule_not_reported_as_runtime_diff(self):
        """An inactive rule saved to disk and loaded to runtime is not a diff.

        LOAD MYSQL QUERY RULES TO RUNTIME skips active=0 rules by design, so
        the row is on disk and in memory but not in runtime. That gap must not
        appear in memory_vs_runtime.
        """
        self._insert_rule(self.INACTIVE_RULE_ID, 0)
        self._admin_command("SAVE MYSQL QUERY RULES TO DISK; "
                            "LOAD MYSQL QUERY RULES TO RUNTIME")

        entry = self._table_diff(self.TABLE)
        only_in_memory = [str(r.get("rule_id"))
                          for r in self._memory_vs_runtime(entry, "only_in_memory")]
        self.assertNotIn(
            str(self.INACTIVE_RULE_ID), only_in_memory,
            f"inactive rule {self.INACTIVE_RULE_ID} reported as a memory-vs-runtime "
            f"difference; only_in_memory={only_in_memory}",
        )

    def test_active_rule_missing_from_runtime_still_reported(self):
        """Guard against over-suppression: active=1 rules must still be diffed.

        Same insert, but active=1 and no LOAD TO RUNTIME — this is real drift
        and has to stay visible.
        """
        self._insert_rule(self.ACTIVE_RULE_ID, 1)

        entry = self._table_diff(self.TABLE)
        only_in_memory = [str(r.get("rule_id"))
                          for r in self._memory_vs_runtime(entry, "only_in_memory")]
        self.assertIn(
            str(self.ACTIVE_RULE_ID), only_in_memory,
            f"active rule {self.ACTIVE_RULE_ID} not loaded to runtime should be "
            f"reported as a difference; only_in_memory={only_in_memory}",
        )


class TestConfigDiffNullDefaultSchema(ConfigDiffTestBase):
    """mysql_users.default_schema NULL (disk/memory) vs '' (runtime) is not drift.

    Regression: the runtime layer stores an empty string where disk and memory
    hold NULL, and runtime_mysql_users carries a separate frontend and backend
    row per user. Both are representation artefacts, but the diff reported the
    user as memory-only / runtime-only and highlighted default_schema in red.
    """

    TABLE     = "mysql_users"
    TEST_USER = "difftest-schema"

    def setUp(self):
        self._login(self.TABLE)

    def tearDown(self):
        try:
            self._run_sql(self.TABLE,
                          f"DELETE FROM mysql_users WHERE username = '{self.TEST_USER}'")
            self._admin_command("LOAD MYSQL USERS TO RUNTIME; SAVE MYSQL USERS TO DISK")
        except Exception:
            pass

    def test_null_default_schema_matches_empty_runtime(self):
        self._run_sql(self.TABLE,
                      f"INSERT INTO mysql_users "
                      f"(username, password, default_hostgroup, default_schema, active) "
                      f"VALUES ('{self.TEST_USER}', 'difftest-pass', 1, NULL, 1)")
        self._admin_command("SAVE MYSQL USERS TO DISK; LOAD MYSQL USERS TO RUNTIME")

        entry = self._table_diff(self.TABLE)
        only_in_memory = [r.get("username")
                          for r in self._memory_vs_runtime(entry, "only_in_memory")]
        only_in_runtime = [r.get("username")
                           for r in self._memory_vs_runtime(entry, "only_in_runtime")]

        self.assertNotIn(
            self.TEST_USER, only_in_memory,
            f"user with a NULL default_schema reported as memory-only; "
            f"only_in_memory={only_in_memory}",
        )
        self.assertNotIn(
            self.TEST_USER, only_in_runtime,
            f"user with a NULL default_schema reported as runtime-only; "
            f"only_in_runtime={only_in_runtime}",
        )


class TestConfigDiffBackendOnlyUser(ConfigDiffTestBase):
    """A backend-only mysql_users row (frontend=0) in sync is not drift.

    Regression: the diff kept only runtime_mysql_users rows with frontend=1.
    A backend-only user has just a frontend=0 row at runtime, so it was
    filtered out and the user was reported as memory-only.
    """

    TABLE     = "mysql_users"
    TEST_USER = "difftest-backend-only"

    def setUp(self):
        self._login(self.TABLE)

    def tearDown(self):
        try:
            self._run_sql(self.TABLE,
                          f"DELETE FROM mysql_users WHERE username = '{self.TEST_USER}'")
            self._admin_command("LOAD MYSQL USERS TO RUNTIME; SAVE MYSQL USERS TO DISK")
        except Exception:
            pass

    def test_backend_only_user_not_reported(self):
        self._run_sql(self.TABLE,
                      f"INSERT INTO mysql_users "
                      f"(username, password, default_hostgroup, frontend, backend, active) "
                      f"VALUES ('{self.TEST_USER}', 'difftest-pass', 1, 0, 1, 1)")
        self._admin_command("SAVE MYSQL USERS TO DISK; LOAD MYSQL USERS TO RUNTIME")

        entry = self._table_diff(self.TABLE)
        for key in ("only_in_memory", "only_in_runtime"):
            users = [r.get("username") for r in self._memory_vs_runtime(entry, key)]
            self.assertNotIn(self.TEST_USER, users,
                             f"in-sync backend-only user reported in {key}: {users}")


class TestConfigDiffIdentityColumnsServed(ConfigDiffTestBase):
    """The diff payload carries the per-table identity (primary key) map.

    Regression: mdb._DIFF_IDENTITY_COLUMNS and a hand-copied IDENTIFYING_COLUMNS
    constant in config_diff.html had drifted apart (the JS copy lacked
    restapi_routes, the Python one the *_variables tables). The backend now
    sends its map with the diff and the UI reads it from there.
    """

    def test_identity_columns_in_payload(self):
        self._login("mysql_users")
        body = self.s.post_json(f"/{self.SERVER}/config_diff/get", {}).json()
        self.assertTrue(body.get("success"), body)
        identity = body["diff"].get("identity_columns")
        self.assertIsInstance(identity, dict, "diff payload has no identity_columns map")
        self.assertEqual(identity.get("mysql_users"), ["username"])
        self.assertEqual(identity.get("mysql_servers"), ["hostgroup_id", "hostname", "port"])
        self.assertEqual(identity.get("global_variables"), ["variable_name"])

    def test_template_has_no_hardcoded_identity_map(self):
        self._login("mysql_users")
        html = self.s.get(f"/{self.SERVER}/config_diff/").text
        self.assertNotIn("const IDENTIFYING_COLUMNS", html)
        self.assertNotIn("|| 'proxysql'", html)


if __name__ == "__main__":
    unittest.main()
