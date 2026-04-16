#!/usr/bin/env python3
"""
Shared test fixtures, constants, and runner for the ProxyWeb integration suite.

Each topical ``test_*.py`` file imports from here. Environment variables:

  PROXYWEB_URL     Base URL of proxyweb (default: http://localhost:5000)
  PROXYWEB_USER    Admin username       (default: admin)
  PROXYWEB_PASS    Admin password       (default: admin42)
  PROXYSQL_MYSQL_HOST   ProxySQL MySQL frontend host (default: 127.0.0.1)
  PROXYSQL_MYSQL_PORT   ProxySQL MySQL frontend port (default: 6033)
"""

import os
import re
import sys
import time
import unittest

import requests

try:
    import pymysql  # noqa: F401 — re-exported HAS_PYMYSQL drives test skips
    HAS_PYMYSQL = True
except ImportError:
    HAS_PYMYSQL = False

BASE_URL = os.environ.get("PROXYWEB_URL", "http://localhost:5000").rstrip("/")
USERNAME = os.environ.get("PROXYWEB_USER", "admin")
PASSWORD = os.environ.get("PROXYWEB_PASS", "admin42")

SERVER    = "proxysql_mysql"
PG_SERVER = "proxysql_postgres"
DATABASE  = "main"


# ---------------------------------------------------------------------------
# Session helper
# ---------------------------------------------------------------------------

class ProxyWebSession:
    """Authenticated requests session with automatic CSRF token handling."""

    def __init__(self):
        self.session = requests.Session()
        self.csrf_token = ""

    def _assert_authenticated(self, resp):
        """Raise an exception if the response is a redirect to the login page."""
        if "/login" in resp.url:
            raise AssertionError(
                f"Request redirected to login page: {resp.url}\n"
                "Session is not authenticated or has expired."
            )

    def login(self, username=USERNAME, password=PASSWORD):
        resp = self.session.post(
            f"{BASE_URL}/login",
            data={"username": username, "password": password},
            allow_redirects=True,
            timeout=10,
        )
        resp.raise_for_status()
        self._refresh_csrf(resp.text)
        return resp

    def get(self, path, **kwargs):
        kwargs.setdefault("timeout", 10)
        resp = self.session.get(f"{BASE_URL}{path}", **kwargs)
        resp.raise_for_status()
        self._assert_authenticated(resp)
        self._refresh_csrf(resp.text)
        return resp

    def post_form(self, path, data=None, **kwargs):
        payload = dict(data or {})
        payload["_csrf_token"] = self.csrf_token
        kwargs.setdefault("timeout", 10)
        resp = self.session.post(f"{BASE_URL}{path}", data=payload, **kwargs)
        resp.raise_for_status()
        self._assert_authenticated(resp)
        self._refresh_csrf(resp.text)
        return resp

    def post_json(self, path, body, **kwargs):
        kwargs.setdefault("timeout", 10)
        headers = {
            "Content-Type": "application/json",
            "X-CSRF-Token": self.csrf_token,
        }
        resp = self.session.post(
            f"{BASE_URL}{path}", json=body, headers=headers, **kwargs
        )
        resp.raise_for_status()
        self._assert_authenticated(resp)
        return resp

    def get_table_data(self, server, database, table, **params):
        """Fetch paginated table rows via /api/table_data (server-side DataTables)."""
        defaults = {
            "server": server, "database": database, "table": table,
            "draw": "1", "start": "0", "length": "100",
            "search[value]": "", "order[0][column]": "0",
            "order[0][dir]": "asc",
        }
        defaults.update(params)
        resp = self.session.get(f"{BASE_URL}/api/table_data",
                                params=defaults, timeout=10)
        resp.raise_for_status()
        self._assert_authenticated(resp)
        return resp.json()

    def _refresh_csrf(self, html):
        m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
        if m:
            self.csrf_token = m.group(1)


# ---------------------------------------------------------------------------
# Wait-for-ready helper (called once before the test run)
# ---------------------------------------------------------------------------

def wait_for_proxyweb(timeout=120):
    deadline = time.monotonic() + timeout
    last_err = None
    while time.monotonic() < deadline:
        try:
            r = requests.get(f"{BASE_URL}/login", timeout=5)
            if r.status_code == 200:
                return
        except requests.RequestException as exc:
            last_err = exc
        time.sleep(3)
    raise RuntimeError(
        f"ProxyWeb did not become ready within {timeout}s. Last error: {last_err}"
    )


# ---------------------------------------------------------------------------
# Colored + numbered test runner
# ---------------------------------------------------------------------------

class _ColoredResult(unittest.TestResult):
    """Prints a numbered, colored one-liner per test as it runs."""

    _GREEN  = '\033[32m'
    _RED    = '\033[31m'
    _YELLOW = '\033[33m'
    _CYAN   = '\033[36m'
    _BOLD   = '\033[1m'
    _RESET  = '\033[0m'

    def __init__(self, stream, total):
        super().__init__()
        self.stream = stream
        self.total  = total
        self._n     = 0
        self._t0    = None
        self._width = len(str(total))

    def startTest(self, test):
        super().startTest(test)
        self._n  += 1
        self._t0  = time.monotonic()
        counter   = f"{self._n:{self._width}}/{self.total}"
        name      = f"{test.__class__.__name__}.{test._testMethodName}"
        self.stream.write(f"  {self._CYAN}{counter}{self._RESET}  {name} ")
        self.stream.flush()

    def _elapsed(self):
        return f"({time.monotonic() - self._t0:.2f}s)"

    def addSuccess(self, test):
        self.stream.write(f"{self._GREEN}\u2713 pass{self._RESET}  {self._elapsed()}\n")
        self.stream.flush()

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.stream.write(f"{self._RED}\u2717 FAIL{self._RESET}  {self._elapsed()}\n")
        self.stream.flush()

    def addError(self, test, err):
        super().addError(test, err)
        self.stream.write(f"{self._RED}\u2717 ERROR{self._RESET} {self._elapsed()}\n")
        self.stream.flush()

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.stream.write(f"{self._YELLOW}\u2298 skip{self._RESET}  {self._elapsed()}  {reason}\n")
        self.stream.flush()

    def printErrors(self):
        if not (self.failures or self.errors):
            return
        self.stream.write("\n")
        for label, cases in [("FAIL", self.failures), ("ERROR", self.errors)]:
            for test, tb in cases:
                self.stream.write("=" * 70 + "\n")
                self.stream.write(f"{self._RED}{label}: {test}{self._RESET}\n")
                self.stream.write("-" * 70 + "\n")
                self.stream.write(tb)
                self.stream.write("\n")


class ColoredRunner:
    _GREEN = '\033[32m'
    _RED   = '\033[31m'
    _BOLD  = '\033[1m'
    _RESET = '\033[0m'

    def run(self, suite):
        total  = suite.countTestCases()
        out    = sys.stderr
        out.write(f"\n{self._BOLD}Running {total} tests...{self._RESET}\n\n")
        out.flush()

        result = _ColoredResult(out, total)
        t0     = time.monotonic()
        suite.run(result)
        elapsed = time.monotonic() - t0

        result.printErrors()

        passed  = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
        parts   = [f"{self._GREEN}{passed} passed{self._RESET}"]
        if result.failures:
            parts.append(f"{self._RED}{len(result.failures)} failed{self._RESET}")
        if result.errors:
            parts.append(f"{self._RED}{len(result.errors)} errors{self._RESET}")
        if result.skipped:
            parts.append(f"{len(result.skipped)} skipped")

        out.write(f"\n{self._BOLD}Results:{self._RESET}  {', '.join(parts)}  ({elapsed:.1f}s)\n\n")
        out.flush()
        return result