#!/usr/bin/env python3
"""Misc → Dump Database: data-only mysqldump download of ProxySQL's main db.

Guards the feature contract of GET /<server>/dump/:

- returns the dump as an ``application/sql`` attachment whose filename is
  timestamped: ``proxysql_<server>_<yyyymmdd-hhmmss>.sql``
- the dump is produced with mysqldump in data-only mode and contains the
  config tables (e.g. ``mysql_servers``) but **no** ``runtime_*`` tables —
  the original manual command dumped those too, which is the bug this
  feature fixes
- admin-only: readonly users get 403 and do not see the menu item;
  unauthenticated requests are redirected to /login
- unknown server names get 404
"""

import re
import unittest

import requests

from testlib import ProxyWebSession, BASE_URL, USERNAME, PASSWORD, SERVER, PG_SERVER

RO_USER = "readonly"
RO_PASS = "readonly42"

FILENAME_RE = re.compile(
    r'attachment; filename="proxysql_%s_\d{8}-\d{6}\.sql"' % re.escape(SERVER)
)


class TestDumpDatabaseDownload(unittest.TestCase):
    """Admin download of the main-db dump, runtime tables excluded."""

    @classmethod
    def setUpClass(cls):
        cls.pw = ProxyWebSession()
        cls.pw.login(USERNAME, PASSWORD)
        cls.resp = cls.pw.get(f"/{SERVER}/dump/")

    def test_returns_timestamped_sql_attachment(self):
        self.assertEqual(self.resp.status_code, 200)
        disposition = self.resp.headers.get("Content-Disposition", "")
        self.assertRegex(disposition, FILENAME_RE)
        self.assertIn("application/sql",
                      self.resp.headers.get("Content-Type", ""))

    def test_dump_contains_config_table_data(self):
        """The test stack registers backends, so mysql_servers must have rows."""
        self.assertIn("INSERT INTO `mysql_servers`", self.resp.text)
        self.assertIn("INSERT INTO `mysql_users`", self.resp.text)

    def test_dump_excludes_runtime_tables(self):
        """No runtime_* table may appear anywhere in the dump."""
        self.assertNotIn("runtime_", self.resp.text)

    def test_dump_is_data_only(self):
        """--no-create-info: the dump must not contain DDL."""
        self.assertNotIn("CREATE TABLE", self.resp.text)

    def test_dump_works_for_postgres_flavored_proxysql(self):
        """The route is parametric; the PgSQL ProxySQL admin (MySQL protocol)
        must dump too."""
        resp = self.pw.get(f"/{PG_SERVER}/dump/")
        self.assertEqual(resp.status_code, 200)
        self.assertRegex(
            resp.headers.get("Content-Disposition", ""),
            r'attachment; filename="proxysql_%s_\d{8}-\d{6}\.sql"'
            % re.escape(PG_SERVER),
        )
        self.assertNotIn("runtime_", resp.text)

    def test_unknown_server_404(self):
        resp = self.pw.session.get(f"{BASE_URL}/no_such_server/dump/", timeout=10)
        self.assertEqual(resp.status_code, 404)

    def test_admin_sees_menu_item(self):
        resp = self.pw.get("/")
        self.assertIn(f"/{SERVER}/dump/", resp.text)
        self.assertIn("Dump Database", resp.text)


class TestDumpDatabaseAccessControl(unittest.TestCase):
    """Dump is admin-only."""

    def test_readonly_user_gets_403(self):
        s = ProxyWebSession()
        s.login(RO_USER, RO_PASS)
        resp = s.session.get(f"{BASE_URL}/{SERVER}/dump/", timeout=10)
        self.assertEqual(resp.status_code, 403)

    def test_readonly_user_does_not_see_menu_item(self):
        s = ProxyWebSession()
        s.login(RO_USER, RO_PASS)
        resp = s.get("/")
        self.assertNotIn("Dump Database", resp.text)
        self.assertNotIn(f"/{SERVER}/dump/", resp.text)

    def test_unauthenticated_redirects_to_login(self):
        resp = requests.get(f"{BASE_URL}/{SERVER}/dump/",
                            allow_redirects=False, timeout=10)
        self.assertIn(resp.status_code, (301, 302))
        self.assertIn("/login", resp.headers.get("Location", ""))


if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    unittest.main()
