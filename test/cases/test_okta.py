#!/usr/bin/env python3
"""Okta OIDC SSO: login button, authorization-code flow against the
mock-okta service, group->role mapping, disable_local_login, and settings
persistence of the auth.okta config block."""

import os
import unittest
from urllib.parse import urlsplit, parse_qs

import requests
import yaml

from testlib import ProxyWebSession, BASE_URL, USERNAME, PASSWORD

# Issuer as ProxyWeb (and the test-runner) reach the mock IdP on the compose
# network. Must match the mock's MOCK_ISSUER so the id_token 'iss' validates.
MOCK_ISSUER = os.environ.get("MOCK_OKTA_URL", "http://mock-okta:9000")
CLIENT_ID = "proxyweb-test-client"
CLIENT_SECRET = "mock-secret"
ADMIN_GROUP = "proxyweb-admins"
READONLY_GROUP = "proxyweb-readonly"


class _OktaConfigMixin:
    """Enable auth.okta against the mock IdP in setUp, restore in tearDown."""

    okta_overrides = {}

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        body = self.s.get("/settings/export/").json()
        self.assertTrue(body.get("success"), f"export failed: {body.get('error')}")
        self._original_yaml = body["yaml"]

        cfg = yaml.safe_load(self._original_yaml)
        okta = {
            "enabled": True,
            "issuer": MOCK_ISSUER,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "admin_group": ADMIN_GROUP,
            "readonly_group": READONLY_GROUP,
        }
        okta.update(self.okta_overrides)
        cfg.setdefault("auth", {})["okta"] = okta
        self._save_yaml(yaml.safe_dump(cfg, default_flow_style=False))

    def tearDown(self):
        if hasattr(self, "_original_yaml"):
            self._save_yaml(self._original_yaml)

    def _save_yaml(self, content):
        resp = self.s.session.post(
            f"{BASE_URL}/settings/save/",
            data={"settings": content, "_csrf_token": self.s.csrf_token},
            timeout=10,
        )
        self.assertEqual(resp.status_code, 200,
                         f"config save failed: {resp.text[:300]!r}")
        self.s._refresh_csrf(resp.text)

    # ------------------------------------------------------------------
    # Flow helper
    # ------------------------------------------------------------------

    def start_flow(self, sess):
        """GET /login/okta without following; return the authorize URL."""
        resp = sess.get(f"{BASE_URL}/login/okta", allow_redirects=False, timeout=10)
        self.assertEqual(resp.status_code, 302,
                         f"/login/okta did not redirect: {resp.status_code}")
        return resp.headers["Location"]

    def run_flow(self, mock_params=""):
        """
        Drive the full code flow with a fresh unauthenticated session,
        controlling the mock identity via mock_params (query-string suffix).
        Returns (session, final_redirect_location).
        """
        sess = requests.Session()
        authorize_url = self.start_flow(sess)
        idp = sess.get(authorize_url + mock_params, allow_redirects=False, timeout=10)
        self.assertEqual(idp.status_code, 302,
                         f"mock authorize did not redirect: {idp.status_code} {idp.text[:200]!r}")
        callback = sess.get(idp.headers["Location"], allow_redirects=False, timeout=10)
        self.assertEqual(callback.status_code, 302,
                         f"callback did not redirect: {callback.status_code}")
        return sess, callback.headers["Location"]

    def assert_not_logged_in(self, sess):
        resp = sess.get(f"{BASE_URL}/", timeout=10)
        self.assertIn("/login", resp.url,
                      "session unexpectedly authenticated after failed SSO")


class TestOktaDisabled(unittest.TestCase):
    """With the default (okta disabled) config, SSO surfaces must be inert."""

    def test_login_page_has_no_okta_button(self):
        resp = requests.get(f"{BASE_URL}/login", timeout=10)
        self.assertNotIn("/login/okta", resp.text)

    def test_okta_login_route_redirects_to_login(self):
        resp = requests.get(f"{BASE_URL}/login/okta",
                            allow_redirects=False, timeout=10)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("sso_error=disabled", resp.headers["Location"])

    def test_callback_redirects_to_login(self):
        resp = requests.get(f"{BASE_URL}/login/okta/callback?code=x&state=y",
                            allow_redirects=False, timeout=10)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("sso_error=disabled", resp.headers["Location"])

    def test_disable_local_login_ignored_when_okta_disabled(self):
        """Lockout guard: disable_local_login must have no effect while
        okta.enabled is false — password login keeps working."""
        s = ProxyWebSession()
        s.login()
        body = s.get("/settings/export/").json()
        original = body["yaml"]
        cfg = yaml.safe_load(original)
        cfg["auth"]["okta"] = {"enabled": False, "disable_local_login": True}
        try:
            resp = s.session.post(
                f"{BASE_URL}/settings/save/",
                data={"settings": yaml.safe_dump(cfg), "_csrf_token": s.csrf_token},
                timeout=10)
            self.assertEqual(resp.status_code, 200)
            s2 = ProxyWebSession()
            s2.login()
            page = s2.session.get(f"{BASE_URL}/", timeout=10)
            self.assertNotIn("/login", page.url,
                             "password login was locked out although okta is disabled")
        finally:
            s.session.post(
                f"{BASE_URL}/settings/save/",
                data={"settings": original, "_csrf_token": s.csrf_token},
                timeout=10)


class TestOktaLoginFlow(_OktaConfigMixin, unittest.TestCase):
    """Full authorization-code flow against the mock IdP."""

    def test_login_page_shows_button_and_password_form(self):
        resp = requests.get(f"{BASE_URL}/login", timeout=10)
        self.assertIn("/login/okta", resp.text)
        self.assertIn('name="username"', resp.text)

    def test_authorize_redirect_params(self):
        sess = requests.Session()
        authorize_url = self.start_flow(sess)
        split = urlsplit(authorize_url)
        self.assertTrue(authorize_url.startswith(f"{MOCK_ISSUER}/authorize"),
                        f"unexpected authorize URL: {authorize_url}")
        q = parse_qs(split.query)
        self.assertEqual(q["client_id"], [CLIENT_ID])
        self.assertEqual(q["response_type"], ["code"])
        self.assertEqual(q["redirect_uri"], [f"{BASE_URL}/login/okta/callback"])
        self.assertIn("openid", q["scope"][0])
        self.assertIn("groups", q["scope"][0])
        self.assertTrue(q["state"][0])
        self.assertTrue(q["nonce"][0])

    def test_callback_state_mismatch_rejected(self):
        sess = requests.Session()
        self.start_flow(sess)  # sets oidc_state in the session cookie
        resp = sess.get(f"{BASE_URL}/login/okta/callback?code=x&state=wrong",
                        allow_redirects=False, timeout=10)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("sso_error=state", resp.headers["Location"])
        self.assert_not_logged_in(sess)

    def test_callback_without_prior_flow_rejected(self):
        sess = requests.Session()
        resp = sess.get(f"{BASE_URL}/login/okta/callback?code=x&state=y",
                        allow_redirects=False, timeout=10)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("sso_error=state", resp.headers["Location"])
        self.assert_not_logged_in(sess)

    def test_callback_okta_error_param(self):
        sess = requests.Session()
        resp = sess.get(
            f"{BASE_URL}/login/okta/callback?error=access_denied"
            "&error_description=user+denied",
            allow_redirects=False, timeout=10)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("sso_error=okta", resp.headers["Location"])

    def test_full_flow_admin_group(self):
        sess, location = self.run_flow(f"&mock_groups={ADMIN_GROUP}")
        self.assertNotIn("sso_error", location, f"flow failed: {location}")
        settings = sess.get(f"{BASE_URL}/settings/edit/", timeout=10)
        self.assertEqual(settings.status_code, 200,
                         "okta admin-group user should reach settings")

    def test_full_flow_readonly_group(self):
        sess, location = self.run_flow(f"&mock_groups={READONLY_GROUP}")
        self.assertNotIn("sso_error", location, f"flow failed: {location}")
        page = sess.get(f"{BASE_URL}/", timeout=10)
        self.assertNotIn("/login", page.url, "readonly SSO user not logged in")
        settings = sess.get(f"{BASE_URL}/settings/edit/", timeout=10)
        self.assertEqual(settings.status_code, 403,
                         "okta readonly-group user must not reach settings")

    def test_full_flow_both_groups_admin_wins(self):
        sess, location = self.run_flow(
            f"&mock_groups={ADMIN_GROUP},{READONLY_GROUP}")
        self.assertNotIn("sso_error", location)
        settings = sess.get(f"{BASE_URL}/settings/edit/", timeout=10)
        self.assertEqual(settings.status_code, 200)

    def test_full_flow_no_matching_group_denied(self):
        sess, location = self.run_flow("&mock_groups=some-other-team")
        self.assertIn("sso_error=not_authorized", location)
        self.assert_not_logged_in(sess)

    def test_full_flow_no_groups_at_all_denied(self):
        sess, location = self.run_flow()
        self.assertIn("sso_error=not_authorized", location)
        self.assert_not_logged_in(sess)

    def test_groups_via_userinfo_fallback(self):
        """When the id_token omits the groups claim, ProxyWeb must fall back
        to the userinfo endpoint (Okta org-authorization-server setups)."""
        sess, location = self.run_flow(
            f"&mock_groups={ADMIN_GROUP}&mock_no_groups_in_idtoken=1")
        self.assertNotIn("sso_error", location, f"userinfo fallback failed: {location}")
        settings = sess.get(f"{BASE_URL}/settings/edit/", timeout=10)
        self.assertEqual(settings.status_code, 200)

    def test_userinfo_sub_mismatch_rejected(self):
        """OIDC Core 5.3.2: the userinfo response 'sub' must match the id_token
        'sub'. A mismatched userinfo response (token substitution) must fail
        the sign-in closed, not authenticate the wrong subject."""
        sess, location = self.run_flow(
            f"&mock_groups={ADMIN_GROUP}&mock_no_groups_in_idtoken=1"
            "&mock_userinfo_wrong_sub=1")
        self.assertIn("sso_error=exchange", location,
                      f"userinfo sub mismatch not rejected: {location}")
        self.assert_not_logged_in(sess)


class TestOktaMultiGroupMapping(_OktaConfigMixin, unittest.TestCase):
    """admin_group / readonly_group accept multiple comma-separated group
    names; membership in any of them grants the corresponding role."""

    okta_overrides = {
        "admin_group": f"dba-team, {ADMIN_GROUP}",
        "readonly_group": f"support-team,{READONLY_GROUP}",
    }

    def test_second_admin_group_grants_admin(self):
        sess, location = self.run_flow(f"&mock_groups={ADMIN_GROUP}")
        self.assertNotIn("sso_error", location, f"flow failed: {location}")
        self.assertEqual(sess.get(f"{BASE_URL}/settings/edit/", timeout=10).status_code, 200)

    def test_first_admin_group_grants_admin(self):
        sess, location = self.run_flow("&mock_groups=dba-team")
        self.assertNotIn("sso_error", location, f"flow failed: {location}")
        self.assertEqual(sess.get(f"{BASE_URL}/settings/edit/", timeout=10).status_code, 200)

    def test_any_readonly_group_grants_readonly(self):
        sess, location = self.run_flow("&mock_groups=support-team")
        self.assertNotIn("sso_error", location, f"flow failed: {location}")
        self.assertEqual(sess.get(f"{BASE_URL}/settings/edit/", timeout=10).status_code, 403)

    def test_unlisted_group_still_denied(self):
        sess, location = self.run_flow("&mock_groups=unrelated-team")
        self.assertIn("sso_error=not_authorized", location)
        self.assert_not_logged_in(sess)

    def test_admin_wins_across_multi_group_lists(self):
        sess, location = self.run_flow("&mock_groups=support-team,dba-team")
        self.assertNotIn("sso_error", location)
        self.assertEqual(sess.get(f"{BASE_URL}/settings/edit/", timeout=10).status_code, 200)


class TestOktaGroupYamlList(_OktaConfigMixin, unittest.TestCase):
    """Hand-edited configs may express the group settings as YAML lists
    instead of comma-separated strings — both forms must work."""

    okta_overrides = {
        "admin_group": ["dba-team", ADMIN_GROUP],
        "readonly_group": ["support-team"],
    }

    def test_list_form_admin_group(self):
        sess, location = self.run_flow(f"&mock_groups={ADMIN_GROUP}")
        self.assertNotIn("sso_error", location, f"flow failed: {location}")
        self.assertEqual(sess.get(f"{BASE_URL}/settings/edit/", timeout=10).status_code, 200)

    def test_list_form_readonly_group(self):
        sess, location = self.run_flow("&mock_groups=support-team")
        self.assertNotIn("sso_error", location, f"flow failed: {location}")
        self.assertEqual(sess.get(f"{BASE_URL}/settings/edit/", timeout=10).status_code, 403)


class TestOktaWrongClientSecret(_OktaConfigMixin, unittest.TestCase):
    """Token exchange rejected by the IdP must fail closed."""

    okta_overrides = {"client_secret": "definitely-wrong"}

    def test_exchange_failure_lands_on_login_error(self):
        sess, location = self.run_flow(f"&mock_groups={ADMIN_GROUP}")
        self.assertIn("sso_error=exchange", location)
        self.assert_not_logged_in(sess)


class TestOktaDisableLocalLogin(_OktaConfigMixin, unittest.TestCase):
    """okta.disable_local_login hides the password form and rejects
    password POSTs server-side (not just cosmetically)."""

    okta_overrides = {"disable_local_login": True}

    def test_login_page_has_only_okta(self):
        resp = requests.get(f"{BASE_URL}/login", timeout=10)
        self.assertIn("/login/okta", resp.text)
        self.assertNotIn('name="username"', resp.text)

    def test_password_post_rejected(self):
        sess = requests.Session()
        sess.post(f"{BASE_URL}/login",
                  data={"username": USERNAME, "password": PASSWORD},
                  timeout=10)
        page = sess.get(f"{BASE_URL}/", timeout=10)
        self.assertIn("/login", page.url,
                      "password login succeeded although disable_local_login is set")

    def test_okta_flow_still_works(self):
        sess, location = self.run_flow(f"&mock_groups={ADMIN_GROUP}")
        self.assertNotIn("sso_error", location)


class TestOktaSettingsPersistence(_OktaConfigMixin, unittest.TestCase):
    """The auth.okta block must survive every config write path."""

    def test_export_save_roundtrip_preserves_okta(self):
        body = self.s.get("/settings/export/").json()
        self.assertTrue(body["success"])
        cfg = yaml.safe_load(body["yaml"])
        self.assertIn("okta", cfg["auth"], "export dropped auth.okta")
        self.assertIs(cfg["auth"]["okta"]["enabled"], True)

        # Save the export back and re-export: still there.
        self._save_yaml(body["yaml"])
        cfg2 = yaml.safe_load(self.s.get("/settings/export/").json()["yaml"])
        self.assertIn("okta", cfg2["auth"], "save/re-export dropped auth.okta")
        self.assertEqual(cfg2["auth"]["okta"]["client_id"], CLIENT_ID)

    def test_load_ui_includes_okta_but_not_secret(self):
        """/settings/load_ui/ feeds the structured settings UI, which renders
        the client_secret into a password field — so the secret must never be
        sent to the browser (blank or absent), unlike the raw YAML export."""
        body = self.s.get("/settings/load_ui/").json()
        self.assertTrue(body["success"])
        okta = body["config"]["auth"].get("okta")
        self.assertIsNotNone(okta, "/settings/load_ui/ missing auth.okta")
        self.assertEqual(okta["client_id"], CLIENT_ID)
        self.assertNotEqual(okta.get("client_secret"), CLIENT_SECRET,
                            "/settings/load_ui/ leaked the Okta client_secret")
        self.assertFalse(okta.get("client_secret"),
                         "client_secret must be blank/absent in load_ui")

    def test_ui_save_reconstructs_okta(self):
        """/settings/ui_save/ rebuilds the whole config from form fields —
        guard against it dropping auth.okta, and against checkbox 'on'
        strings being persisted instead of real YAML booleans."""
        cfg = yaml.safe_load(self._original_yaml)
        form = {
            "global_default_server": cfg["global"]["default_server"],
            "auth_admin_user": cfg["auth"]["admin_user"],
            "auth_admin_password": cfg["auth"]["admin_password"],
            "auth_readonly_user": cfg["auth"].get("readonly_user", "readonly"),
            "auth_readonly_password": cfg["auth"].get("readonly_password", ""),
            "flask_SECRET_KEY": cfg["flask"]["SECRET_KEY"],
            "server_count": "0",
            "auth_okta_enabled": "on",
            "auth_okta_issuer": MOCK_ISSUER,
            "auth_okta_client_id": CLIENT_ID,
            "auth_okta_client_secret": CLIENT_SECRET,
            "auth_okta_admin_group": ADMIN_GROUP,
            "auth_okta_readonly_group": READONLY_GROUP,
            "auth_okta_scopes": "openid profile email groups",
            "auth_okta_disable_local_login": "on",
        }
        resp = self.s.post_form("/settings/ui_save/", form)
        body = resp.json()
        self.assertTrue(body.get("success"), f"ui_save failed: {body.get('error')}")

        saved = yaml.safe_load(self.s.get("/settings/export/").json()["yaml"])
        okta = saved["auth"].get("okta")
        self.assertIsNotNone(okta, "ui_save dropped auth.okta")
        self.assertIs(okta["enabled"], True,
                      f"enabled saved as {okta['enabled']!r}, not boolean True")
        self.assertIs(okta["disable_local_login"], True)
        self.assertEqual(okta["issuer"], MOCK_ISSUER)
        self.assertEqual(okta["client_id"], CLIENT_ID)
        self.assertEqual(okta["client_secret"], CLIENT_SECRET)
        self.assertEqual(okta["admin_group"], ADMIN_GROUP)
        self.assertEqual(okta["readonly_group"], READONLY_GROUP)

    def test_ui_save_blank_secret_preserves_existing(self):
        """load_ui never returns the client_secret, so an unchanged save from
        the settings UI submits it blank. ui_save must preserve the stored
        secret instead of wiping it (regression: blank field zeroed it out)."""
        cfg = yaml.safe_load(self._original_yaml)
        form = {
            "global_default_server": cfg["global"]["default_server"],
            "auth_admin_user": cfg["auth"]["admin_user"],
            "auth_admin_password": cfg["auth"]["admin_password"],
            "flask_SECRET_KEY": cfg["flask"]["SECRET_KEY"],
            "server_count": "0",
            "auth_okta_enabled": "on",
            "auth_okta_issuer": MOCK_ISSUER,
            "auth_okta_client_id": CLIENT_ID,
            "auth_okta_client_secret": "",  # left blank -> preserve stored value
            "auth_okta_admin_group": ADMIN_GROUP,
            "auth_okta_readonly_group": READONLY_GROUP,
        }
        resp = self.s.post_form("/settings/ui_save/", form)
        body = resp.json()
        self.assertTrue(body.get("success"), f"ui_save failed: {body.get('error')}")

        saved = yaml.safe_load(self.s.get("/settings/export/").json()["yaml"])
        okta = saved["auth"].get("okta")
        self.assertIsNotNone(okta, "ui_save dropped auth.okta")
        self.assertEqual(okta.get("client_secret"), CLIENT_SECRET,
                         "blank client_secret in ui_save wiped the stored secret")

    def test_ui_save_without_okta_fields_omits_block(self):
        """A form with no okta fields at all (older UI, curl) must not emit
        a stray half-empty okta block."""
        cfg = yaml.safe_load(self._original_yaml)
        form = {
            "global_default_server": cfg["global"]["default_server"],
            "auth_admin_user": cfg["auth"]["admin_user"],
            "auth_admin_password": cfg["auth"]["admin_password"],
            "flask_SECRET_KEY": cfg["flask"]["SECRET_KEY"],
            "server_count": "0",
        }
        resp = self.s.post_form("/settings/ui_save/", form)
        self.assertTrue(resp.json().get("success"))
        saved = yaml.safe_load(self.s.get("/settings/export/").json()["yaml"])
        self.assertNotIn("okta", saved["auth"])


if __name__ == "__main__":
    unittest.main()
