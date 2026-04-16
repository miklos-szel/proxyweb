#!/usr/bin/env python3
"""CRUD against typical ProxySQL admin tables via the ProxyWeb API."""

import unittest

from testlib import (
    ProxyWebSession, BASE_URL, USERNAME, PASSWORD,
    SERVER, PG_SERVER, DATABASE,
)


class TestMySQLServers(unittest.TestCase):
    """CRUD operations on mysql_servers via ProxyWeb API."""

    SERVER   = SERVER
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

    SERVER   = SERVER
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


class TestInlinePrimaryKeyUpdate(unittest.TestCase):
    """Updates must persist for ProxySQL tables whose PK is declared inline.

    Regression: ``get_primary_key_columns`` only parsed the block form
    ``PRIMARY KEY (col, ...)`` and returned ``[]`` for ProxySQL's SQLite-style
    inline PKs (e.g. ``rule_id INTEGER PRIMARY KEY AUTOINCREMENT`` on
    ``mysql_query_rules``). ``update_row`` then fell back to using *every*
    column sent by the browser as the WHERE clause. Because the browser
    captures cell text — NULL rendered by Jinja as the literal ``"None"`` —
    the WHERE matched zero rows. ``execute_change`` reported no SQL error,
    so the API returned ``success=True`` but ProxySQL was never modified,
    and the edit vanished on the next page load.
    """

    SERVER   = SERVER
    DATABASE = "main"
    TABLE    = "mysql_query_rules"
    TEST_RULE_ID = 981

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")
        # Clean up any stragglers from prior failed runs
        self._delete(ignore_errors=True)

    def tearDown(self):
        self._delete(ignore_errors=True)

    def _delete(self, ignore_errors=False):
        try:
            resp = self.s.post_json("/api/delete_row", {
                "server":   self.SERVER,
                "database": self.DATABASE,
                "table":    self.TABLE,
                "pkValues": {"rule_id": str(self.TEST_RULE_ID)},
            })
            return resp.json()
        except Exception:
            if not ignore_errors:
                raise

    def _fetch_row(self):
        """Return the test row as {col: value}, or None if not present."""
        body = self.s.get_table_data(
            self.SERVER, self.DATABASE, self.TABLE,
            **{"search[value]": str(self.TEST_RULE_ID), "length": "100"},
        )
        cols = body["column_names"]
        rule_id_idx = cols.index("rule_id")
        for row in body["data"]:
            if str(row[rule_id_idx]) == str(self.TEST_RULE_ID):
                return dict(zip(cols, row))
        return None

    @staticmethod
    def _browser_style_pk_values(row):
        """Mirror base.html's enableInlineEditing(): every column in the row,
        with Python ``None`` rendered by Jinja as the literal string ``"None"``.
        """
        return {
            col: ("None" if val is None else str(val))
            for col, val in row.items()
        }

    def _insert(self):
        return self.s.post_json("/api/insert_row", {
            "server":      self.SERVER,
            "database":    self.DATABASE,
            "table":       self.TABLE,
            "columnNames": ["rule_id", "active", "match_pattern", "apply"],
            "data": {
                "rule_id":       str(self.TEST_RULE_ID),
                "active":        "1",
                "match_pattern": "original",
                "apply":         "1",
            },
        }).json()

    def test_update_persists_with_browser_style_pkvalues(self):
        """Simulate the browser: send every column as ``pkValues``, including
        NULL columns rendered as the literal string ``"None"``. With a correct
        PK lookup the backend must filter the WHERE to ``rule_id`` only and
        the update must be visible on the next read."""

        insert = self._insert()
        self.assertTrue(insert.get("success"), insert.get("error"))

        row = self._fetch_row()
        self.assertIsNotNone(row, "inserted test row not readable back")
        self.assertEqual(row["match_pattern"], "original")

        pk_values    = self._browser_style_pk_values(row)
        column_names = list(row.keys())

        upd = self.s.post_json("/api/update_row", {
            "server":      self.SERVER,
            "database":    self.DATABASE,
            "table":       self.TABLE,
            "pkValues":    pk_values,
            "columnNames": column_names,
            "data":        {"match_pattern": "updated"},
        }).json()
        self.assertTrue(upd.get("success"), upd.get("error"))

        refreshed = self._fetch_row()
        self.assertIsNotNone(refreshed)
        self.assertEqual(
            refreshed["match_pattern"], "updated",
            "UPDATE returned success=True but the change did not persist — "
            "WHERE clause likely included non-PK columns with 'None' literals "
            "(inline PRIMARY KEY not detected in get_primary_key_columns)",
        )

    def test_delete_persists_with_browser_style_pkvalues(self):
        """Regression: the browser sends every column (including NULL cells
        rendered as ``"None"``) in ``pkValues`` for DELETE too. The backend
        must narrow the WHERE to the real PK columns; otherwise the API
        returns ``success=True`` while ProxySQL keeps the row, and it
        reappears on the next page load."""

        insert = self._insert()
        self.assertTrue(insert.get("success"), insert.get("error"))

        row = self._fetch_row()
        self.assertIsNotNone(row, "inserted test row not readable back")

        delete = self.s.post_json("/api/delete_row", {
            "server":   self.SERVER,
            "database": self.DATABASE,
            "table":    self.TABLE,
            "pkValues": self._browser_style_pk_values(row),
        }).json()
        self.assertTrue(delete.get("success"), delete.get("error"))

        self.assertIsNone(
            self._fetch_row(),
            "DELETE returned success=True but the row is still present — "
            "WHERE clause likely matched zero rows because pkValues included "
            "non-PK columns with 'None' literals.",
        )


class TestCrossPkStyleEditing(unittest.TestCase):
    """Browser-style UPDATE must work for every PK declaration ProxySQL uses.

    ``TestInlinePrimaryKeyUpdate`` covers a single-column inline PK
    (``mysql_query_rules.rule_id``). This class guards the other two styles
    that ProxySQL's SQLite-backed admin schema mixes in:

    * **Block-form composite PK** — ``mysql_servers`` declares
      ``PRIMARY KEY (hostgroup_id, hostname, port)``.
    * **Inline autoinc PK** — ``scheduler`` declares
      ``id INTEGER PRIMARY KEY AUTOINCREMENT``.

    Each test mimics the browser: the DOM scrape produces pkValues for
    *every* column, with NULLs rendered as the literal string ``"None"``.
    If the backend fails to narrow that down to the real PK, the WHERE
    matches nothing and the edit silently vanishes on refresh.
    """

    SERVER   = SERVER
    DATABASE = "main"

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()

    @staticmethod
    def _browser_style_pk_values(row):
        return {
            col: ("None" if val is None else str(val))
            for col, val in row.items()
        }

    def _fetch_row(self, table, key_col, key_val):
        self.s.get(f"/{self.SERVER}/{self.DATABASE}/{table}/")
        body = self.s.get_table_data(
            self.SERVER, self.DATABASE, table,
            **{"search[value]": str(key_val), "length": "100"},
        )
        cols = body["column_names"]
        idx  = cols.index(key_col)
        for row in body["data"]:
            if str(row[idx]) == str(key_val):
                return dict(zip(cols, row))
        return None

    # ------------------------------------------------------------------
    # Block-form composite PK: mysql_servers
    # ------------------------------------------------------------------

    def test_update_block_form_composite_pk(self):
        """mysql_servers: PRIMARY KEY (hostgroup_id, hostname, port)."""
        table = "mysql_servers"
        host, hg, port = "cross-pk-block-host", 99, 3311

        pk = {"hostgroup_id": str(hg), "hostname": host, "port": str(port)}
        self.s.get(f"/{self.SERVER}/{self.DATABASE}/{table}/")
        insert = self.s.post_json("/api/insert_row", {
            "server":      self.SERVER,
            "database":    self.DATABASE,
            "table":       table,
            "columnNames": ["hostgroup_id", "hostname", "port", "weight"],
            "data":        {**pk, "weight": "1"},
        }).json()
        self.assertTrue(insert.get("success"), insert.get("error"))

        try:
            row = self._fetch_row(table, "hostname", host)
            self.assertIsNotNone(row, "inserted mysql_servers row not readable back")
            self.assertEqual(str(row["weight"]), "1")

            upd = self.s.post_json("/api/update_row", {
                "server":      self.SERVER,
                "database":    self.DATABASE,
                "table":       table,
                "pkValues":    self._browser_style_pk_values(row),
                "columnNames": list(row.keys()),
                "data":        {"weight": "42"},
            }).json()
            self.assertTrue(upd.get("success"), upd.get("error"))

            refreshed = self._fetch_row(table, "hostname", host)
            self.assertIsNotNone(refreshed)
            self.assertEqual(
                str(refreshed["weight"]), "42",
                "Block-form PK UPDATE returned success=True but did not persist.",
            )
        finally:
            self.s.get(f"/{self.SERVER}/{self.DATABASE}/{table}/")
            self.s.post_json("/api/delete_row", {
                "server":   self.SERVER,
                "database": self.DATABASE,
                "table":    table,
                "pkValues": pk,
            })

    # ------------------------------------------------------------------
    # Inline autoinc PK: scheduler
    # ------------------------------------------------------------------

    def test_update_inline_autoinc_pk(self):
        """scheduler: id INTEGER PRIMARY KEY AUTOINCREMENT."""
        table = "scheduler"
        marker = "cross-pk-autoinc-test"

        self.s.get(f"/{self.SERVER}/{self.DATABASE}/{table}/")
        insert = self.s.post_json("/api/insert_row", {
            "server":      self.SERVER,
            "database":    self.DATABASE,
            "table":       table,
            "columnNames": ["active", "interval_ms", "filename", "comment"],
            "data": {
                "active":      "1",
                "interval_ms": "60000",
                "filename":    "/bin/true",
                "comment":     marker,
            },
        }).json()
        self.assertTrue(insert.get("success"), insert.get("error"))

        row = self._fetch_row(table, "comment", marker)
        self.assertIsNotNone(row, "inserted scheduler row not readable back")
        row_id = row["id"]

        try:
            upd = self.s.post_json("/api/update_row", {
                "server":      self.SERVER,
                "database":    self.DATABASE,
                "table":       table,
                "pkValues":    self._browser_style_pk_values(row),
                "columnNames": list(row.keys()),
                "data":        {"interval_ms": "90000"},
            }).json()
            self.assertTrue(upd.get("success"), upd.get("error"))

            refreshed = self._fetch_row(table, "id", row_id)
            self.assertIsNotNone(refreshed)
            self.assertEqual(
                str(refreshed["interval_ms"]), "90000",
                "Inline autoinc PK UPDATE returned success=True but did not persist.",
            )
        finally:
            self.s.get(f"/{self.SERVER}/{self.DATABASE}/{table}/")
            self.s.post_json("/api/delete_row", {
                "server":   self.SERVER,
                "database": self.DATABASE,
                "table":    table,
                "pkValues": {"id": str(row_id)},
            })


if __name__ == "__main__":
    unittest.main()
