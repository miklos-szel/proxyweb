#!/usr/bin/env python3
"""Page navigation: list_dbs and sidebar behavior."""

import copy
import unittest

import yaml

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

    def test_settings_header_has_import_export_actions(self):
        """
        The Import/Export YAML actions live in the settings page header; the
        standalone "Configuration Management" bar was removed. Guards that the
        actions stayed reachable after the box was dropped.
        """
        resp = self.s.get("/settings/edit/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Export YAML", resp.text,
                      "Export YAML action missing from settings header")
        self.assertIn("Import YAML", resp.text,
                      "Import YAML action missing from settings header")
        self.assertNotIn("Configuration Management", resp.text,
                         "the removed Configuration Management box is still rendered")

    def test_config_diff_page(self):
        resp = self.s.get(f"/{SERVER}/config_diff/")
        self.assertEqual(resp.status_code, 200)

class TestProdWarningHeaderBorder(unittest.TestCase):
    """
    Feature coverage for the prod-server header highlight.

    When the optional global.prod_warning flag is enabled AND the currently
    selected server's name contains "prod" (case-insensitive), the shared page
    header (navbar in list_dbs.html) gets an extra ``prod-warning`` CSS class
    that draws a red border. The class must be absent when either condition is
    not met: flag off, or the selected server is not a prod server.
    """

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        # Snapshot the live config so we can restore it no matter what happens.
        resp = self.s.get("/settings/export/")
        body = resp.json()
        self.assertTrue(body.get("success"), f"export failed: {body.get('error')}")
        self._original_yaml = body["yaml"]
        self.addCleanup(self._restore_config)

    def _restore_config(self):
        payload = {"settings": self._original_yaml, "_csrf_token": self.s.csrf_token}
        self.s.session.post(f"{BASE_URL}/settings/save/", data=payload, timeout=10)

    def _save_config(self, cfg):
        # /settings/save/ renders the settings page as HTML (message="success"),
        # so like the other settings tests we only assert on the status code —
        # validation failures raise server-side and surface as a non-200.
        yaml_text = yaml.safe_dump(cfg, default_flow_style=False, sort_keys=False)
        resp = self.s.session.post(
            f"{BASE_URL}/settings/save/",
            data={"settings": yaml_text, "_csrf_token": self.s.csrf_token},
            timeout=10,
        )
        self.assertEqual(resp.status_code, 200,
                         f"settings/save returned {resp.status_code}; body: {resp.text!r}")

    def test_prod_warning_header_border(self):
        # (a) Baseline: stock test config has prod_warning off, so a normal
        #     page render must not carry the prod-warning class.
        resp = self.s.get(f"/{SERVER}/{DATABASE}/global_variables/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("prod-warning", resp.text,
                         "prod-warning class present with the flag off")

        # (b) Enable the flag and add a prod-named server that reuses the
        #     working proxysql_mysql DSN so its pages actually render.
        cfg = yaml.safe_load(self._original_yaml)
        cfg["global"]["prod_warning"] = True
        cfg["servers"]["proxysql_prod"] = {
            "dsn": copy.deepcopy(cfg["servers"][SERVER]["dsn"]),
        }
        self._save_config(cfg)

        # (c) Under the prod-named server, the class must appear.
        resp = self.s.get("/proxysql_prod/main/global_variables/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("prod-warning", resp.text,
                      "prod-warning class missing on a prod server with the flag on")

        # (d) Under the non-prod server, the class must be absent even with the
        #     flag on — the server name gates it.
        resp = self.s.get(f"/{SERVER}/{DATABASE}/global_variables/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("prod-warning", resp.text,
                         "prod-warning class present on a non-prod server")

        # (e) Turn the flag back off (server stays named proxysql_prod): the
        #     class must disappear again.
        cfg["global"]["prod_warning"] = False
        self._save_config(cfg)
        resp = self.s.get("/proxysql_prod/main/global_variables/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("prod-warning", resp.text,
                         "prod-warning class present after disabling the flag")


if __name__ == "__main__":
    unittest.main()
