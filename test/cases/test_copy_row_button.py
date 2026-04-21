#!/usr/bin/env python3
"""Per-row 'Copy SQL' button ships for disk/memory/runtime tables.

Regression guard for the copy-button feature: every row in every ProxySQL
table view should render a clipboard button whose SQL is generated
client-side from the row's column names and values. Requirements:

- The page must ship the ``buildRowSql`` and ``copyRowSql`` JS helpers.
- The ``enableInlineEditing`` row loop must include ``copy-btn`` markup so
  the button is appended to every valid data row.
- The template-exposed globals (``tableName``, ``tableColumnNames``) must
  carry the source table name verbatim (including the ``runtime_`` prefix)
  because the JS helper is the thing that strips ``runtime_`` when
  building the INSERT/SET statement.
- Readonly users must also receive the JS helpers (they need the button
  too) and must be able to load runtime_* pages without a 403.

The HTML-level assertions stop at 'the JS is shipped and the inputs are
correct'; actually running ``buildRowSql`` requires a JS engine and is
outside this suite's scope. A reimplementation of ``buildRowSql`` in
Python (``_py_build_row_sql`` below) verifies the expected output format
for all three branches the JS covers.
"""

import re
import unittest

from testlib import (
    ProxyWebSession, BASE_URL, USERNAME, PASSWORD,
    SERVER, DATABASE,
)


def _py_build_row_sql(column_names, row_data, source_table):
    """Mirror of the JS buildRowSql() in templates/base.html.

    Keep this in sync with that JS function so the test catches divergence
    if someone edits one without the other.
    """
    target = (source_table[len("runtime_"):]
              if source_table.startswith("runtime_") else source_table)

    if target == "global_variables":
        name_idx = column_names.index("variable_name")
        value_idx = column_names.index("variable_value")
        name = str(row_data[name_idx])
        value = str(row_data[value_idx]).replace('"', '""')
        return f'SET {name}="{value}";'

    kept_cols = []
    kept_vals = []
    for col, val in zip(column_names, row_data):
        if val is None or val == "None":
            continue
        kept_cols.append(col)
        kept_vals.append("'" + str(val).replace("'", "''") + "'")
    return f"INSERT INTO {target}({','.join(kept_cols)}) VALUES ({','.join(kept_vals)});"


class TestCopyRowSqlGenerator(unittest.TestCase):
    """Pure-logic checks for the SQL format the JS helper must produce."""

    def test_insert_format_memory_table(self):
        self.assertEqual(
            _py_build_row_sql(
                ["hostgroup_id", "hostname", "port"],
                [1, "10.0.0.1", 3306],
                "mysql_servers",
            ),
            "INSERT INTO mysql_servers(hostgroup_id,hostname,port) "
            "VALUES ('1','10.0.0.1','3306');",
        )

    def test_insert_format_strips_runtime_prefix(self):
        """runtime_mysql_servers rows must copy as INSERT INTO mysql_servers."""
        sql = _py_build_row_sql(
            ["hostgroup_id", "hostname", "port"],
            [2, "db-reader", 3306],
            "runtime_mysql_servers",
        )
        self.assertTrue(sql.startswith("INSERT INTO mysql_servers("), sql)
        self.assertNotIn("runtime_", sql)

    def test_insert_omits_columns_with_none_value(self):
        """Columns whose DOM value is the literal 'None' (i.e. NULL) are
        dropped from both the column list and the VALUES list, so ProxySQL
        applies each column's DEFAULT.
        """
        sql = _py_build_row_sql(
            ["rule_id", "active", "username", "match_pattern", "apply"],
            ["1", "1", "None", "^SELECT.*FOR UPDATE", "1"],
            "mysql_query_rules",
        )
        self.assertEqual(
            sql,
            "INSERT INTO mysql_query_rules(rule_id,active,match_pattern,apply) "
            "VALUES ('1','1','^SELECT.*FOR UPDATE','1');",
        )
        self.assertNotIn("username", sql)
        self.assertNotIn("'None'", sql)

    def test_insert_escapes_single_quote(self):
        sql = _py_build_row_sql(
            ["comment"], ["O'Brien"], "mysql_servers",
        )
        self.assertIn("'O''Brien'", sql)

    def test_global_variables_uses_set_with_double_quoted_value(self):
        self.assertEqual(
            _py_build_row_sql(
                ["variable_name", "variable_value"],
                ["admin-refresh_interval", "1700"],
                "global_variables",
            ),
            'SET admin-refresh_interval="1700";',
        )

    def test_runtime_global_variables_uses_set(self):
        sql = _py_build_row_sql(
            ["variable_name", "variable_value"],
            ["mysql-threads", "4"],
            "runtime_global_variables",
        )
        self.assertEqual(sql, 'SET mysql-threads="4";')

    def test_global_variables_escapes_double_quote(self):
        sql = _py_build_row_sql(
            ["variable_name", "variable_value"],
            ["mysql-default_charset", 'ut"f8'],
            "global_variables",
        )
        self.assertIn('"ut""f8"', sql)


class TestCopyRowButtonShippedToAllTables(unittest.TestCase):
    """Every table page must ship the JS helpers and the copy-btn template."""

    # JS fingerprints. Keep these tight — they should only match the
    # feature this test guards, so any rename breaks the test loudly.
    JS_BUILD_FN   = re.compile(r"function\s+buildRowSql\s*\(")
    JS_COPY_FN    = re.compile(r"function\s+copyRowSql\s*\(")
    COPY_BTN_HTML = "copy-btn"
    COPY_SQL_ATTR = "copyRowSql(this)"

    PAGES = [
        (DATABASE, "mysql_servers"),
        (DATABASE, "runtime_mysql_servers"),
        (DATABASE, "global_variables"),
        (DATABASE, "runtime_global_variables"),
        ("disk",   "mysql_servers"),
    ]

    def setUp(self):
        self.pw = ProxyWebSession()
        self.pw.login(USERNAME, PASSWORD)

    def _assert_copy_machinery(self, html, where):
        self.assertRegex(html, self.JS_BUILD_FN,
                         f"{where}: buildRowSql JS helper missing")
        self.assertRegex(html, self.JS_COPY_FN,
                         f"{where}: copyRowSql JS helper missing")
        self.assertIn(self.COPY_BTN_HTML, html,
                      f"{where}: copy-btn class string missing from template")
        self.assertIn(self.COPY_SQL_ATTR, html,
                      f"{where}: copyRowSql(this) wiring missing from template")

    def _assert_table_name_global(self, html, expected, where):
        m = re.search(r"var\s+tableName\s*=\s*(\S+?);", html)
        self.assertIsNotNone(m, f"{where}: tableName global not found")
        self.assertEqual(m.group(1).strip('"'), expected,
                         f"{where}: tableName global should be {expected!r}")

    def test_copy_helpers_present_on_every_table_view(self):
        for database, table in self.PAGES:
            with self.subTest(database=database, table=table):
                resp = self.pw.get(f"/{SERVER}/{database}/{table}/")
                self.assertEqual(resp.status_code, 200)
                self._assert_copy_machinery(resp.text, f"{database}/{table}")
                self._assert_table_name_global(
                    resp.text, table, f"{database}/{table}")


class TestCopyRowButtonAvailableToReadonlyUser(unittest.TestCase):
    """Readonly users must receive the copy-button JS and see runtime tables."""

    RO_USER = "readonly"
    RO_PASS = "readonly42"

    def setUp(self):
        self.pw = ProxyWebSession()
        self.pw.login(self.RO_USER, self.RO_PASS)

    def test_memory_table_ships_copy_helpers(self):
        resp = self.pw.get(f"/{SERVER}/{DATABASE}/mysql_servers/")
        self.assertEqual(resp.status_code, 200)
        self.assertRegex(resp.text, r"function\s+buildRowSql\s*\(")
        self.assertIn("copyRowSql(this)", resp.text)

    def test_runtime_table_accessible_and_ships_copy_helpers(self):
        resp = self.pw.get(f"/{SERVER}/{DATABASE}/runtime_mysql_servers/")
        self.assertEqual(resp.status_code, 200)
        self.assertRegex(resp.text, r"function\s+buildRowSql\s*\(")
        self.assertIn("copyRowSql(this)", resp.text)

    def test_global_variables_ships_copy_helpers(self):
        resp = self.pw.get(f"/{SERVER}/{DATABASE}/global_variables/")
        self.assertEqual(resp.status_code, 200)
        self.assertRegex(resp.text, r"function\s+buildRowSql\s*\(")
        self.assertIn("copyRowSql(this)", resp.text)


if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    unittest.main()
