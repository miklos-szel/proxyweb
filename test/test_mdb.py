#!/usr/bin/env python3
"""
Unit tests for the query-history functions added to mdb.py.

These tests are self-contained and do NOT require a running ProxyWeb or
ProxySQL instance.  They patch mdb.HISTORY_DIR to a temporary directory so
the real data/ tree is never touched.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

# Make sure we can import mdb from the repo root regardless of cwd.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# mdb.py imports mysql.connector at the top level; stub it out so these
# unit tests run without the mysql-connector-python package installed.
sys.modules.setdefault("mysql", MagicMock())
sys.modules.setdefault("mysql.connector", MagicMock())

import mdb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_history_dir(tmp_dir):
    """Return a context manager that redirects mdb.HISTORY_DIR to tmp_dir."""
    return patch.object(mdb, "HISTORY_DIR", tmp_dir)


# ---------------------------------------------------------------------------
# Tests for _valid_history_server
# ---------------------------------------------------------------------------

class TestValidHistoryServer(unittest.TestCase):
    """_valid_history_server must accept safe names and reject path-traversal."""

    def test_simple_name_is_valid(self):
        self.assertTrue(mdb._valid_history_server("proxysql"))

    def test_name_with_dash_is_valid(self):
        self.assertTrue(mdb._valid_history_server("proxysql-primary"))

    def test_name_with_underscore_is_valid(self):
        self.assertTrue(mdb._valid_history_server("my_server_1"))

    def test_name_with_colon_port_is_valid(self):
        """Host:port style server names must be accepted."""
        self.assertTrue(mdb._valid_history_server("127.0.0.1:6032"))

    def test_empty_string_is_invalid(self):
        self.assertFalse(mdb._valid_history_server(""))

    def test_none_is_invalid(self):
        self.assertFalse(mdb._valid_history_server(None))

    def test_forward_slash_is_invalid(self):
        self.assertFalse(mdb._valid_history_server("some/server"))

    def test_backslash_is_invalid(self):
        self.assertFalse(mdb._valid_history_server("some\\server"))

    def test_dotdot_is_invalid(self):
        self.assertFalse(mdb._valid_history_server("../etc"))

    def test_dotdot_embedded_is_invalid(self):
        self.assertFalse(mdb._valid_history_server("server..name"))

    def test_dotdot_with_slash_is_invalid(self):
        self.assertFalse(mdb._valid_history_server("../../etc/passwd"))


# ---------------------------------------------------------------------------
# Tests for append_query_history / load_query_history / clear_query_history
# ---------------------------------------------------------------------------

class TestQueryHistoryFunctions(unittest.TestCase):
    """File-level tests for the three public history functions in mdb.py."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ load

    def test_load_returns_empty_list_when_no_file(self):
        with _patch_history_dir(self.tmp):
            result = mdb.load_query_history("no_such_server")
        self.assertEqual(result, [])

    def test_load_invalid_server_returns_empty_list(self):
        with _patch_history_dir(self.tmp):
            result = mdb.load_query_history("../evil")
        self.assertEqual(result, [])

    # ----------------------------------------------------------------- append

    def test_append_creates_file_on_first_call(self):
        with _patch_history_dir(self.tmp):
            mdb.append_query_history("srv1", "SELECT 1")
            path = os.path.join(self.tmp, "srv1.json")
        self.assertTrue(os.path.exists(path))

    def test_append_writes_valid_json(self):
        with _patch_history_dir(self.tmp):
            mdb.append_query_history("srv1", "SELECT 1")
            path = os.path.join(self.tmp, "srv1.json")
            with open(path) as f:
                data = json.load(f)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)

    def test_append_stores_sql_field(self):
        with _patch_history_dir(self.tmp):
            mdb.append_query_history("srv1", "SELECT version()")
            history = mdb.load_query_history("srv1")
        self.assertEqual(history[0]["sql"], "SELECT version()")

    def test_append_default_user_is_admin(self):
        with _patch_history_dir(self.tmp):
            mdb.append_query_history("srv1", "SELECT 1")
            history = mdb.load_query_history("srv1")
        self.assertEqual(history[0]["user"], "admin")

    def test_append_custom_user_is_stored(self):
        with _patch_history_dir(self.tmp):
            mdb.append_query_history("srv1", "SELECT 1", user="readonly")
            history = mdb.load_query_history("srv1")
        self.assertEqual(history[0]["user"], "readonly")

    def test_append_stores_iso_timestamp(self):
        with _patch_history_dir(self.tmp):
            mdb.append_query_history("srv1", "SELECT 1")
            history = mdb.load_query_history("srv1")
        ts = history[0]["timestamp"]
        # Must parse as a valid ISO datetime without raising
        datetime.fromisoformat(ts)

    def test_append_multiple_entries_are_ordered(self):
        with _patch_history_dir(self.tmp):
            for i in range(5):
                mdb.append_query_history("srv1", f"SELECT {i}")
            history = mdb.load_query_history("srv1")
        self.assertEqual(len(history), 5)
        sqls = [e["sql"] for e in history]
        self.assertEqual(sqls, [f"SELECT {i}" for i in range(5)])

    def test_append_does_nothing_for_invalid_server(self):
        with _patch_history_dir(self.tmp):
            mdb.append_query_history("../evil", "SELECT 1")
        self.assertEqual(os.listdir(self.tmp), [])

    def test_append_creates_history_dir_if_missing(self):
        nested = os.path.join(self.tmp, "history")
        # nested does not exist yet
        self.assertFalse(os.path.exists(nested))
        with _patch_history_dir(nested):
            mdb.append_query_history("srv1", "SELECT 1")
        self.assertTrue(os.path.exists(nested))

    def test_append_uses_atomic_replace(self):
        """After append the .tmp file must be gone (atomic write succeeded)."""
        with _patch_history_dir(self.tmp):
            mdb.append_query_history("srv1", "SELECT 1")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "srv1.json.tmp")))

    # ----------------------------------------------------------- load + limit

    def test_load_limit_returns_last_n(self):
        with _patch_history_dir(self.tmp):
            for i in range(15):
                mdb.append_query_history("srv1", f"SELECT {i}")
            result = mdb.load_query_history("srv1", limit=10)
        self.assertEqual(len(result), 10)
        sqls = [e["sql"] for e in result]
        # Should be the last 10 entries (indices 5..14)
        expected = [f"SELECT {i}" for i in range(5, 15)]
        self.assertEqual(sqls, expected)

    def test_load_limit_zero_returns_all(self):
        """limit=0 is falsy; the function should return the full history."""
        with _patch_history_dir(self.tmp):
            for i in range(5):
                mdb.append_query_history("srv1", f"SELECT {i}")
            result = mdb.load_query_history("srv1", limit=0)
        self.assertEqual(len(result), 5)

    def test_load_limit_larger_than_history_returns_all(self):
        with _patch_history_dir(self.tmp):
            for i in range(3):
                mdb.append_query_history("srv1", f"SELECT {i}")
            result = mdb.load_query_history("srv1", limit=100)
        self.assertEqual(len(result), 3)

    def test_load_limit_one_returns_last_entry(self):
        with _patch_history_dir(self.tmp):
            for i in range(5):
                mdb.append_query_history("srv1", f"SELECT {i}")
            result = mdb.load_query_history("srv1", limit=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["sql"], "SELECT 4")

    # ------------------------------------------------------------------ clear

    def test_clear_removes_history_file(self):
        with _patch_history_dir(self.tmp):
            mdb.append_query_history("srv1", "SELECT 1")
            mdb.clear_query_history("srv1")
            result = mdb.load_query_history("srv1")
        self.assertEqual(result, [])

    def test_clear_nonexistent_history_does_not_raise(self):
        with _patch_history_dir(self.tmp):
            try:
                mdb.clear_query_history("ghost_server")
            except Exception as exc:
                self.fail(f"clear_query_history raised unexpectedly: {exc}")

    def test_clear_invalid_server_does_nothing(self):
        """clear_query_history must silently ignore invalid server names."""
        with _patch_history_dir(self.tmp):
            try:
                mdb.clear_query_history("../../etc/passwd")
            except Exception as exc:
                self.fail(f"clear_query_history raised unexpectedly: {exc}")
        # No files should have been created or removed
        self.assertEqual(os.listdir(self.tmp), [])

    # --------------------------------------------------------- server isolation

    def test_two_servers_have_independent_files(self):
        with _patch_history_dir(self.tmp):
            mdb.append_query_history("alpha", "SELECT * FROM alpha_table")
            mdb.append_query_history("beta", "SELECT * FROM beta_table")
            alpha = mdb.load_query_history("alpha")
            beta = mdb.load_query_history("beta")
        self.assertEqual(len(alpha), 1)
        self.assertEqual(len(beta), 1)
        self.assertIn("alpha_table", alpha[0]["sql"])
        self.assertIn("beta_table", beta[0]["sql"])

    def test_clear_one_server_leaves_other_intact(self):
        with _patch_history_dir(self.tmp):
            mdb.append_query_history("alpha", "SELECT 1")
            mdb.append_query_history("beta", "SELECT 2")
            mdb.clear_query_history("alpha")
            alpha = mdb.load_query_history("alpha")
            beta = mdb.load_query_history("beta")
        self.assertEqual(alpha, [])
        self.assertEqual(len(beta), 1)

    def test_alpha_queries_do_not_appear_in_beta(self):
        with _patch_history_dir(self.tmp):
            for i in range(5):
                mdb.append_query_history("alpha", f"SELECT alpha_{i}")
            for i in range(5):
                mdb.append_query_history("beta", f"SELECT beta_{i}")
            beta = mdb.load_query_history("beta")
        beta_sqls = " ".join(e["sql"] for e in beta)
        self.assertNotIn("alpha_", beta_sqls)

    # --------------------------------------------------------- edge / boundary

    def test_append_empty_sql_is_stored(self):
        """Empty SQL string is technically valid input; must not raise."""
        with _patch_history_dir(self.tmp):
            mdb.append_query_history("srv1", "")
            history = mdb.load_query_history("srv1")
        self.assertEqual(history[0]["sql"], "")

    def test_append_sql_with_special_characters(self):
        sql = "SELECT 'it''s a test' AS msg, 1/0, \"\\\""
        with _patch_history_dir(self.tmp):
            mdb.append_query_history("srv1", sql)
            history = mdb.load_query_history("srv1")
        self.assertEqual(history[0]["sql"], sql)

    def test_load_without_limit_returns_all_entries(self):
        with _patch_history_dir(self.tmp):
            for i in range(30):
                mdb.append_query_history("srv1", f"SELECT {i}")
            result = mdb.load_query_history("srv1")
        self.assertEqual(len(result), 30)

    def test_append_many_queries_preserves_order(self):
        """Entries must remain in insertion order (most recent last)."""
        sqls = [f"SELECT {i} FROM t" for i in range(20)]
        with _patch_history_dir(self.tmp):
            for sql in sqls:
                mdb.append_query_history("srv1", sql)
            history = mdb.load_query_history("srv1")
        self.assertEqual([e["sql"] for e in history], sqls)


# ---------------------------------------------------------------------------
# Entry point (so the file can be run directly)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()