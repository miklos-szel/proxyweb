#!/usr/bin/env python3
"""Page navigation: list_dbs and sidebar behavior."""

import unittest

from testlib import (
    ProxyWebSession, BASE_URL, USERNAME, PASSWORD,
    SERVER, PG_SERVER, DATABASE,
)


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
        resp = self.s.get(f"/{SERVER}/config_diff/")
        self.assertEqual(resp.status_code, 200)

if __name__ == "__main__":
    unittest.main()
