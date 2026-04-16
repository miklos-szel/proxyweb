#!/usr/bin/env python3
"""PostgreSQL ProxySQL: navigation, servers, users, load/save, SQL, replication."""

import os
import subprocess
import time
import unittest

from testlib import (
    ProxyWebSession, BASE_URL, USERNAME, PASSWORD,
    SERVER, PG_SERVER, DATABASE,
)


class TestPgSQLNavigation(unittest.TestCase):
    """Verify that pgsql tables are visible on proxysql3 and hidden on proxysql."""

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()

    def test_pgsql_tables_hidden_on_mysql_server(self):
        """pgsql tables should be hidden on the MySQL-only proxysql server."""
        resp = self.s.get(f"/{SERVER}/{DATABASE}/mysql_servers/")
        self.assertNotIn("/main/pgsql_servers/", resp.text)

    def test_mysql_tables_hidden_on_pg_server(self):
        """mysql tables should be hidden on the PostgreSQL-only proxysql3 server."""
        resp = self.s.get(f"/{PG_SERVER}/{DATABASE}/pgsql_servers/")
        self.assertNotIn("/main/mysql_servers/", resp.text)

    def test_pgsql_servers_in_nav(self):
        """pgsql_servers should appear in the sidebar when browsing proxysql3."""
        resp = self.s.get(f"/{PG_SERVER}/{DATABASE}/pgsql_servers/")
        self.assertIn("pgsql_servers", resp.text)

    def test_pgsql_users_in_nav(self):
        """pgsql_users should appear in the sidebar when browsing proxysql3."""
        resp = self.s.get(f"/{PG_SERVER}/{DATABASE}/pgsql_users/")
        self.assertIn("pgsql_users", resp.text)

    def test_pgsql_servers_table_view(self):
        """Browsing pgsql_servers should return 200 and show column headers."""
        resp = self.s.get(f"/{PG_SERVER}/{DATABASE}/pgsql_servers/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("hostname", resp.text)
        self.assertIn("hostgroup_id", resp.text)

    def test_pgsql_users_table_view(self):
        """Browsing pgsql_users should return 200 and show column headers."""
        resp = self.s.get(f"/{PG_SERVER}/{DATABASE}/pgsql_users/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("username", resp.text)

    def test_pgsql_query_rules_table_view(self):
        """Browsing pgsql_query_rules should return 200."""
        resp = self.s.get(f"/{PG_SERVER}/{DATABASE}/pgsql_query_rules/")
        self.assertEqual(resp.status_code, 200)

    def test_pgsql_servers_show_backends(self):
        """The postgres publisher backend must appear in pgsql_servers."""
        resp = self.s.get(f"/{PG_SERVER}/{DATABASE}/pgsql_servers/")
        self.assertIn("postgres", resp.text)

    def test_pgsql_users_show_pguser(self):
        """The pguser registered via init must appear in pgsql_users."""
        self.s.get(f"/{PG_SERVER}/{DATABASE}/pgsql_users/")
        data = self.s.get_table_data(PG_SERVER, DATABASE, "pgsql_users",
                                      **{"search[value]": "pguser"})
        flat = str(data["data"])
        self.assertIn("pguser", flat)

    def test_runtime_pgsql_servers_table_view(self):
        """runtime_pgsql_servers should be browsable and show the publisher backend."""
        resp = self.s.get(f"/{PG_SERVER}/{DATABASE}/runtime_pgsql_servers/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("postgres", resp.text)


class TestPgSQLServers(unittest.TestCase):
    """CRUD operations on pgsql_servers via ProxyWeb API."""

    SERVER   = PG_SERVER
    DATABASE = "main"
    TABLE    = "pgsql_servers"

    TEST_HOST = "test-pg-server-host"
    TEST_HG   = 99
    TEST_PORT = 5433

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

    def test_insert_pgsql_server(self):
        """Insert a row into pgsql_servers and verify success."""
        result = self._insert()
        self.assertTrue(result.get("success"), result.get("error"))
        self._delete()

    def test_update_pgsql_server_weight(self):
        """Update the weight column of a pgsql_servers row."""
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

    def test_delete_pgsql_server(self):
        """Delete a row from pgsql_servers and verify success."""
        self._insert()
        result = self._delete()
        self.assertTrue(result.get("success"), result.get("error"))

    def test_table_view_shows_postgres_backends(self):
        """The postgres backends registered by init must appear."""
        resp = self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")
        self.assertIn("postgres", resp.text)


class TestPgSQLUsers(unittest.TestCase):
    """CRUD operations on pgsql_users via ProxyWeb API."""

    SERVER   = PG_SERVER
    DATABASE = "main"
    TABLE    = "pgsql_users"

    TEST_USER = "test_pg_crud_user"

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")

    def _pk(self):
        return {
            "username": self.TEST_USER,
            "backend":  "1",
        }

    def _insert(self):
        return self.s.post_json("/api/insert_row", {
            "server":      self.SERVER,
            "database":    self.DATABASE,
            "table":       self.TABLE,
            "columnNames": ["username", "password", "default_hostgroup"],
            "data": {
                "username":          self.TEST_USER,
                "password":          "testpass",
                "default_hostgroup": "10",
            },
        }).json()

    def _delete(self):
        return self.s.post_json("/api/delete_row", {
            "server":   self.SERVER,
            "database": self.DATABASE,
            "table":    self.TABLE,
            "pkValues": self._pk(),
        }).json()

    def test_insert_pgsql_user(self):
        """Insert a row into pgsql_users and verify success."""
        result = self._insert()
        self.assertTrue(result.get("success"), result.get("error"))
        self._delete()

    def test_delete_pgsql_user(self):
        """Delete a row from pgsql_users and verify success."""
        self._insert()
        result = self._delete()
        self.assertTrue(result.get("success"), result.get("error"))

    def test_table_view_shows_pguser(self):
        """The pguser registered by init must appear."""
        self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")
        data = self.s.get_table_data(self.SERVER, self.DATABASE, self.TABLE,
                                      **{"search[value]": "pguser"})
        flat = str(data["data"])
        self.assertIn("pguser", flat)


class TestPgSQLLoadSave(unittest.TestCase):
    """Test LOAD/SAVE commands for PostgreSQL config in ProxySQL."""

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        # Navigate to proxysql3 to set session server
        self.s.get(f"/{PG_SERVER}/{DATABASE}/pgsql_servers/")

    def test_load_pgsql_servers_to_runtime(self):
        """LOAD PGSQL SERVERS TO RUNTIME should succeed."""
        result = self.s.post_form("/api/execute_proxysql_command", {
            "sql": "LOAD PGSQL SERVERS TO RUNTIME",
        }).json()
        self.assertTrue(result.get("success"), result.get("error"))

    def test_save_pgsql_servers_to_disk(self):
        """SAVE PGSQL SERVERS TO DISK should succeed."""
        result = self.s.post_form("/api/execute_proxysql_command", {
            "sql": "SAVE PGSQL SERVERS TO DISK",
        }).json()
        self.assertTrue(result.get("success"), result.get("error"))

    def test_load_pgsql_users_to_runtime(self):
        """LOAD PGSQL USERS TO RUNTIME should succeed."""
        result = self.s.post_form("/api/execute_proxysql_command", {
            "sql": "LOAD PGSQL USERS TO RUNTIME",
        }).json()
        self.assertTrue(result.get("success"), result.get("error"))

    def test_save_pgsql_users_to_disk(self):
        """SAVE PGSQL USERS TO DISK should succeed."""
        result = self.s.post_form("/api/execute_proxysql_command", {
            "sql": "SAVE PGSQL USERS TO DISK",
        }).json()
        self.assertTrue(result.get("success"), result.get("error"))


class TestPgSQLQueryViaSQL(unittest.TestCase):
    """Query pgsql tables via the SQL form in ProxyWeb."""

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()

    def test_select_pgsql_servers(self):
        """SELECT from pgsql_servers via SQL form should return the publisher backend."""
        self.s.get(f"/{PG_SERVER}/{DATABASE}/pgsql_servers/")
        resp = self.s.post_form(
            f"/{PG_SERVER}/{DATABASE}/pgsql_servers/sql/",
            {"sql": "SELECT * FROM pgsql_servers"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("postgres", resp.text)

    def test_select_pgsql_users(self):
        """SELECT from pgsql_users via SQL form should return pguser."""
        self.s.get(f"/{PG_SERVER}/{DATABASE}/pgsql_users/")
        resp = self.s.post_form(
            f"/{PG_SERVER}/{DATABASE}/pgsql_users/sql/",
            {"sql": "SELECT * FROM pgsql_users"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("pguser", resp.text)

    def test_select_runtime_pgsql_servers(self):
        """SELECT from runtime_pgsql_servers should show ONLINE backends."""
        self.s.get(f"/{PG_SERVER}/{DATABASE}/runtime_pgsql_servers/")
        resp = self.s.post_form(
            f"/{PG_SERVER}/{DATABASE}/runtime_pgsql_servers/sql/",
            {"sql": "SELECT * FROM runtime_pgsql_servers"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("ONLINE", resp.text)


class TestPgSQLReplication(unittest.TestCase):
    """Verify PostgreSQL logical replication between postgres (publisher) and postgres2 (subscriber).

    Writes a row to the publisher's items_pg table and verifies it appears
    on the subscriber via logical replication. Runs the `psql` client against
    each postgres service over the Compose network — the test-runner container
    ships a postgresql-client package, so no Python PostgreSQL driver is needed.
    """

    _PG_PASSWORDS = {"pguser": "pgpass", "pguser2": "pgpass2"}

    @classmethod
    def _psql(cls, service, db, user, sql):
        """Run a psql command against a postgres service and return stdout."""
        env = {**os.environ, "PGPASSWORD": cls._PG_PASSWORDS[user]}
        result = subprocess.run(
            ["psql", "-h", service, "-U", user, "-d", db,
             "-t", "-A", "-c", sql],
            capture_output=True, text=True, timeout=15, env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"psql on {service} failed (rc={result.returncode}): {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def test_replicated_seed_data(self):
        """Seed data inserted on the publisher must appear on the subscriber."""
        rows = self._psql("postgres2", "testdb_pg2", "pguser2",
                          "SELECT count(*) FROM items_pg")
        self.assertGreaterEqual(int(rows), 3,
                                "Subscriber should have replicated seed rows from publisher")

    def test_insert_replicates_to_subscriber(self):
        """A row inserted on the publisher must replicate to the subscriber."""
        tag = f"repl-test-{int(time.time())}"
        try:
            # Insert on publisher
            self._psql("postgres", "testdb_pg", "pguser",
                       f"INSERT INTO items_pg (name, val) VALUES ('{tag}', 42)")

            # Wait for replication (logical replication is near-instant but not synchronous)
            found = False
            for _ in range(10):
                out = self._psql("postgres2", "testdb_pg2", "pguser2",
                                 f"SELECT name FROM items_pg WHERE name = '{tag}'")
                if tag in out:
                    found = True
                    break
                time.sleep(0.5)

            self.assertTrue(found,
                            f"Row '{tag}' did not replicate to subscriber within 5 seconds")
        finally:
            # Cleanup on publisher — DELETE also replicates
            self._psql("postgres", "testdb_pg", "pguser",
                       f"DELETE FROM items_pg WHERE name = '{tag}'")

    def test_delete_replicates_to_subscriber(self):
        """A row deleted on the publisher must be removed from the subscriber."""
        tag = f"repl-del-{int(time.time())}"
        # Insert and wait for replication
        self._psql("postgres", "testdb_pg", "pguser",
                   f"INSERT INTO items_pg (name, val) VALUES ('{tag}', 99)")
        for _ in range(10):
            out = self._psql("postgres2", "testdb_pg2", "pguser2",
                             f"SELECT name FROM items_pg WHERE name = '{tag}'")
            if tag in out:
                break
            time.sleep(0.5)

        # Delete on publisher
        self._psql("postgres", "testdb_pg", "pguser",
                   f"DELETE FROM items_pg WHERE name = '{tag}'")

        # Wait for delete to replicate
        gone = False
        for _ in range(10):
            out = self._psql("postgres2", "testdb_pg2", "pguser2",
                             f"SELECT name FROM items_pg WHERE name = '{tag}'")
            if tag not in out or out == "":
                gone = True
                break
            time.sleep(0.5)

        self.assertTrue(gone,
                        f"Deleted row '{tag}' still present on subscriber after 5 seconds")

    def test_update_replicates_to_subscriber(self):
        """A row updated on the publisher must reflect on the subscriber."""
        tag = f"repl-upd-{int(time.time())}"
        try:
            self._psql("postgres", "testdb_pg", "pguser",
                       f"INSERT INTO items_pg (name, val) VALUES ('{tag}', 1)")
            # Wait for insert to replicate
            for _ in range(10):
                out = self._psql("postgres2", "testdb_pg2", "pguser2",
                                 f"SELECT val FROM items_pg WHERE name = '{tag}'")
                if "1" in out:
                    break
                time.sleep(0.5)

            # Update on publisher
            self._psql("postgres", "testdb_pg", "pguser",
                       f"UPDATE items_pg SET val = 999 WHERE name = '{tag}'")

            # Wait for update to replicate
            updated = False
            for _ in range(10):
                out = self._psql("postgres2", "testdb_pg2", "pguser2",
                                 f"SELECT val FROM items_pg WHERE name = '{tag}'")
                if "999" in out:
                    updated = True
                    break
                time.sleep(0.5)

            self.assertTrue(updated,
                            "Updated value did not replicate to subscriber within 5 seconds")
        finally:
            self._psql("postgres", "testdb_pg", "pguser",
                       f"DELETE FROM items_pg WHERE name = '{tag}'")

if __name__ == "__main__":
    unittest.main()
