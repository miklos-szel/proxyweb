#!/usr/bin/env python3
"""Servers defined through environment variables (PROXYWEB_SERVERS).

These run against the separate ``proxyweb-env`` instance, which shares
config.yml with ``proxyweb`` but is started with:

    PROXYWEB_SERVERS="envserver, bad/name"
    PROXYWEB_SERVER_ENVSERVER_HOST=proxysql2
    PROXYWEB_SERVER_ENVSERVER_USER=envadmin
    PROXYWEB_SERVER_ENVSERVER_PASSWORD=envsecret42

``envserver`` is not in config.yml and ``envadmin``/``envsecret42`` appear
nowhere in it, so any appearance of them in settings output is a leak.
"""

import unittest

import yaml

from testlib import ProxyWebSession, ENV_BASE_URL, DATABASE

ENV_SERVER = "envserver"
ENV_SECRET = "envsecret42"


class TestEnvDefinedServer(unittest.TestCase):
    """A server listed only in PROXYWEB_SERVERS must be usable.

    Bug guarded: PROXYWEB_SERVER_<NAME>_* only overrode servers that already
    existed in config.yml, and the shipped config has ``servers: {}``, so an
    env-only deployment (e.g. a k8s sidecar configured through a mounted .env)
    had no way to define a server at all.
    """

    def setUp(self):
        self.s = ProxyWebSession(ENV_BASE_URL)
        self.s.login()

    def test_env_server_listed_in_nav(self):
        html = self.s.get("/").text
        self.assertIn(f"/{ENV_SERVER}/", html,
                      "env-defined server missing from the server menu")

    def test_env_server_table_browsable(self):
        resp = self.s.get(f"/{ENV_SERVER}/{DATABASE}/mysql_servers/")
        self.assertEqual(resp.status_code, 200)
        data = self.s.get_table_data(ENV_SERVER, DATABASE, "mysql_servers")
        self.assertNotIn("error", data, f"table_data failed: {data.get('error')}")
        self.assertGreater(int(data.get("recordsTotal", 0)), 0,
                           "env server returned no mysql_servers rows")

    def test_invalid_name_is_skipped(self):
        """'bad/name' is not URL-safe and must not become a server."""
        html = self.s.get("/").text
        self.assertNotIn("bad/name", html)


class TestEnvServerNotPersisted(unittest.TestCase):
    """Env-supplied servers and secrets must never reach config.yml.

    Bug guarded: the structured settings editor (/settings/load_ui/) and
    Export read the env-overridden config, so a UI save or an export+import
    wrote env servers and passwords into config.yml.
    """

    def setUp(self):
        self.s = ProxyWebSession(ENV_BASE_URL)
        self.s.login()
        body = self.s.get("/settings/export/").json()
        self.assertTrue(body.get("success"), f"export failed: {body.get('error')}")
        self._original_yaml = body["yaml"]

    def tearDown(self):
        if hasattr(self, "_original_yaml"):
            self.s.post_form("/settings/save/", {"settings": self._original_yaml})

    def test_load_ui_excludes_env_server(self):
        resp = self.s.get("/settings/load_ui/")
        body = resp.json()
        self.assertTrue(body.get("success"), body.get("error"))
        self.assertNotIn(ENV_SERVER, body["config"].get("servers", {}))
        self.assertNotIn(ENV_SECRET, resp.text)

    def test_export_excludes_env_server(self):
        exported = yaml.safe_load(self._original_yaml)
        self.assertNotIn(ENV_SERVER, exported.get("servers", {}))
        self.assertNotIn(ENV_SECRET, self._original_yaml)

    def test_save_roundtrip_keeps_env_server_out_of_file(self):
        self.s.post_form("/settings/save/", {"settings": self._original_yaml})
        edit_html = self.s.get("/settings/edit/").text
        self.assertNotIn(ENV_SECRET, edit_html)
        self.assertNotIn(f"{ENV_SERVER}:", edit_html)
        # Still served from env after the save.
        resp = self.s.get(f"/{ENV_SERVER}/{DATABASE}/mysql_servers/")
        self.assertEqual(resp.status_code, 200)

    def test_settings_page_notes_env_server(self):
        html = self.s.get("/settings/edit/").text
        self.assertIn('id="env-servers-note"', html)
        self.assertIn(f"<code>{ENV_SERVER}</code>", html)
        self.assertNotIn("bad/name", html)


if __name__ == "__main__":
    unittest.main()
