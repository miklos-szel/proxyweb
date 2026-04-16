#!/usr/bin/env python3
"""Settings page: raw YAML save, structured UI form, hide_tables, recovery."""

import unittest

from testlib import (
    ProxyWebSession, BASE_URL, USERNAME, PASSWORD,
    SERVER, PG_SERVER, DATABASE,
)


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
            self.assertTrue(export.get("success"),
                            f"settings export failed after ui_save accepted the form: {export.get('error')}")
            yaml_text = export["yaml"]
            # An empty key would appear as "'': " or bare ": "
            self.assertNotIn("\n  '': ", yaml_text,
                             "Empty server name appeared as quoted empty key in YAML")
            self.assertNotRegex(yaml_text, r"\n  : ",
                                "Empty server name appeared as bare empty key in YAML")
            # The phantom DSN host (1.2.3.4) must not appear
            self.assertNotIn("1.2.3.4", yaml_text,
                             "DSN from empty-named server leaked into the YAML output")

if __name__ == "__main__":
    unittest.main()
