#!/usr/bin/env python3
"""Table rendering: digest_text, DataTables pagination, client/server-side mode, ProxySQL 3.x quirks."""

import os
import unittest

from testlib import HAS_PYMYSQL
if HAS_PYMYSQL:
    import pymysql

from testlib import (
    ProxyWebSession, BASE_URL, USERNAME, PASSWORD,
    SERVER, PG_SERVER, DATABASE,
)


@unittest.skipUnless(HAS_PYMYSQL, "pymysql not installed — skipping digest_text display tests")
class TestDigestTextDisplay(unittest.TestCase):
    """stats_mysql_query_digest page renders long digest_text with truncation and FA icons.

    Regression guard for the digest_text display fix in show_table_info.html:
    - digest_text must be trimmed (no leading ProxySQL whitespace)
    - truncated at 100 chars (not 60)
    - <td> must use white-space:normal (not pre-wrap)
    - toggle badge must use fa-chevron-right / fa-chevron-down (not raw Unicode arrows)

    Setup: runs several long SELECT queries through the ProxySQL 1 MySQL frontend
    so they appear in stats_mysql_query_digest with a digest_text > 100 chars.
    """

    # ProxySQL MySQL frontend (port 6033)
    HOST = os.environ.get("PROXYSQL_MYSQL_HOST", "127.0.0.1")
    PORT = int(os.environ.get("PROXYSQL_MYSQL_PORT", "6033"))
    USER = "proxyuser2"
    PASS = "proxypass2"
    DB   = "testdb2"

    # ProxyWeb path for the stats table
    SERVER   = SERVER
    DATABASE = "stats"
    TABLE    = "stats_mysql_query_digest"

    # Long queries whose normalized digest_text exceeds 100 chars.
    # ProxySQL replaces literals with ? but keeps identifiers and structure.
    LONG_QUERIES = [
        # ~120 chars after normalization (IN list becomes ?,?,... ; LIMIT becomes ?)
        (
            "SELECT id, name, price FROM products "
            "WHERE id IN (1,2,3,4,5,6,7,8,9,10) "
            "AND name IS NOT NULL AND price IS NOT NULL "
            "ORDER BY id DESC LIMIT 100"
        ),
        # ~115 chars after normalization
        (
            "SELECT id, name, price FROM products "
            "WHERE name LIKE 'test%' AND price BETWEEN 1 AND 9999 "
            "AND id > 0 AND id < 100000 "
            "ORDER BY name ASC, price DESC"
        ),
    ]

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        # Run the long queries through ProxySQL so they appear in stats
        conn = pymysql.connect(
            host=self.HOST, port=self.PORT,
            user=self.USER, password=self.PASS,
            database=self.DB, autocommit=True,
        )
        try:
            with conn.cursor() as cur:
                for query in self.LONG_QUERIES:
                    try:
                        cur.execute(query)
                        cur.fetchall()
                    except Exception:
                        pass  # query may return no rows — that's fine
        finally:
            conn.close()

    def _page(self):
        return self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")

    def test_page_loads(self):
        """stats_mysql_query_digest table page returns 200."""
        resp = self._page()
        self.assertEqual(resp.status_code, 200)

    def test_truncation_js_render_present(self):
        """Page JS must contain the digest-short/digest-full/digest-toggle render logic."""
        resp = self._page()
        html = resp.text
        self.assertIn('digest-short', html,
                      "digest-short class missing from JS render function")
        self.assertIn('digest-full', html,
                      "digest-full class missing from JS render function")
        self.assertIn('digest-toggle', html,
                      "digest-toggle class missing from JS render function")

    def test_toggle_uses_fa_chevron_not_unicode_arrow(self):
        """JS render function must use Font Awesome chevron, not raw Unicode arrow characters."""
        resp = self._page()
        html = resp.text
        self.assertIn('fa-chevron-right', html,
                      "fa-chevron-right icon missing from digest toggle JS render")
        self.assertNotIn('&#9654;', html,
                         "Raw Unicode arrow &#9654; (▶) must not appear in digest toggle")
        self.assertNotIn('\u25ba', html,
                         "Raw Unicode arrow ▶ must not appear in digest toggle")

    def test_td_style_in_js_render(self):
        """JS createdCell must set white-space:normal on digest_text cells."""
        resp = self._page()
        html = resp.text
        self.assertIn('white-space', html,
                      "white-space style missing from JS createdCell function")
        self.assertNotIn('white-space:pre-wrap', html,
                         "digest_text td must not use white-space:pre-wrap")

    def test_api_returns_digest_text_data(self):
        """The /api/table_data endpoint must return digest_text values for JS to render."""
        self._page()  # populate session
        body = self.s.get_table_data(self.SERVER, self.DATABASE, self.TABLE,
                                      length="25")
        self.assertGreater(body.get("recordsTotal", 0), 0,
                           "stats_mysql_query_digest should have rows")
        self.assertGreater(len(body.get("data", [])), 0,
                           "API should return at least one row")

    def test_truncation_at_100_via_substring(self):
        """JS render function must truncate at 100 chars (substring(0, 100))."""
        resp = self._page()
        self.assertIn('substring(0, 100)', resp.text,
                      "JS render must truncate digest_text at 100 chars")


@unittest.skipUnless(HAS_PYMYSQL, "pymysql not installed — skipping pagination tests")
class TestZPagination(unittest.TestCase):
    """stats_mysql_query_digest page serves > 100 rows so DataTables activates pagination.

    Seeds ProxySQL with 1 050 structurally distinct queries via the MySQL frontend (port 6033)
    so that stats_mysql_query_digest has more rows than DataTables' pageLength (100).

    Queries use unique column aliases (col_1, col_2, …) so ProxySQL assigns a distinct digest
    to each one — without this, all `SELECT ? AS n` queries collapse to a single digest row.

    Pagination itself is rendered by DataTables in JavaScript after page load, so it is not
    present in the server-rendered HTML.  We instead assert that the server returns > 100
    <tr> rows in the table body, which guarantees DataTables will activate pagination on the
    client.
    """

    HOST = os.environ.get("PROXYSQL_MYSQL_HOST", "127.0.0.1")
    PORT = int(os.environ.get("PROXYSQL_MYSQL_PORT", "6033"))
    USER = "proxyuser2"
    PASS = "proxypass2"
    DB   = "testdb2"

    SERVER   = SERVER
    DATABASE = "stats"
    TABLE    = "stats_mysql_query_digest"

    QUERY_COUNT = 1050

    @classmethod
    def setUpClass(cls):
        """Fire QUERY_COUNT structurally distinct queries through the ProxySQL MySQL frontend."""
        conn = pymysql.connect(
            host=cls.HOST, port=cls.PORT,
            user=cls.USER, password=cls.PASS,
            database=cls.DB, autocommit=True,
        )
        try:
            with conn.cursor() as cur:
                for i in range(1, cls.QUERY_COUNT + 1):
                    # Unique column alias per query → unique digest in ProxySQL
                    # (ProxySQL normalises literals to ? but keeps identifiers as-is)
                    try:
                        cur.execute(f"SELECT 1 AS col_{i}")
                        cur.fetchall()
                    except Exception:
                        pass
        finally:
            conn.close()

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()

    def _page(self):
        return self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")

    def test_page_loads(self):
        """stats_mysql_query_digest page returns 200 after seeding > 1000 digests."""
        resp = self._page()
        self.assertEqual(resp.status_code, 200)

    def test_server_side_mode_active(self):
        """Page must enable DataTables server-side processing."""
        resp = self._page()
        self.assertIn('serverSideMode', resp.text,
                       "serverSideMode JS variable missing from page")
        self.assertIn('/api/table_data', resp.text,
                       "AJAX endpoint URL missing from page JS")

    def test_pagination_via_api(self):
        """The /api/table_data endpoint must report > 100 total rows and paginate correctly.

        With server-side DataTables the HTML tbody is empty (rows load via AJAX).
        Verify the API returns the correct total count and a page of the requested size.
        """
        self._page()  # populate session
        body = self.s.get_table_data(self.SERVER, self.DATABASE, self.TABLE,
                                      length="100")
        self.assertGreater(body.get("recordsTotal", 0), 100,
                           f"Expected > 100 total records after seeding {self.QUERY_COUNT} "
                           f"digests, got {body.get('recordsTotal', 0)}")
        self.assertEqual(len(body.get("data", [])), 100,
                         f"Expected exactly 100 rows in first page, "
                         f"got {len(body.get('data', []))}")


class TestSmallTableClientSideMode(unittest.TestCase):
    """Small tables (< threshold) must render inline without an AJAX call.

    Regression: `get_table_metadata` compared `row_count` (str from ProxySQL
    COUNT(*)) against an int threshold, raising TypeError on every table
    view request and breaking DataTables with an empty grid / ajax error.
    """

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()

    def test_small_table_page_loads_200(self):
        """mysql_query_rules (3 rows) must render the table page successfully."""
        resp = self.s.get(f"/{SERVER}/{DATABASE}/mysql_query_rules/")
        self.assertEqual(resp.status_code, 200,
                         f"Small table page returned {resp.status_code}, "
                         "likely str<int TypeError in get_table_metadata")

    def test_small_table_renders_rows_inline(self):
        """Small tables must ship rows in the HTML, not rely on AJAX."""
        resp = self.s.get(f"/{SERVER}/{DATABASE}/mysql_query_rules/")
        self.assertEqual(resp.status_code, 200)
        # Client-side mode: serverSideMode should be false in the rendered JS
        self.assertIn("var serverSideMode = false", resp.text,
                      "Small tables must render in client-side mode")

    def test_global_variables_loads(self):
        """global_variables page must not 500 (has ~150 rows, depends on mode)."""
        resp = self.s.get(f"/{SERVER}/{DATABASE}/global_variables/")
        self.assertEqual(resp.status_code, 200)


class TestServerSidePagination(unittest.TestCase):
    """Verify /api/table_data returns correct DataTables server-side JSON."""

    SERVER   = SERVER
    DATABASE = "main"
    TABLE    = "mysql_servers"

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        # Load table page to populate session
        self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")

    def _api(self, **overrides):
        params = {
            "server": self.SERVER, "database": self.DATABASE,
            "table": self.TABLE, "draw": "1", "start": "0",
            "length": "25", "search[value]": "",
            "order[0][column]": "0", "order[0][dir]": "asc",
        }
        params.update(overrides)
        return self.s.session.get(f"{BASE_URL}/api/table_data",
                                  params=params, timeout=10)

    def test_response_format(self):
        """API must return draw, recordsTotal, recordsFiltered, and data keys."""
        body = self._api().json()
        for key in ("draw", "recordsTotal", "recordsFiltered", "data"):
            self.assertIn(key, body, f"Missing key {key!r} in response")
        self.assertIsInstance(body["data"], list)

    def test_draw_echo(self):
        """draw parameter must be echoed back."""
        body = self._api(draw="42").json()
        self.assertEqual(body["draw"], 42)

    def test_page_size(self):
        """Returned data length must not exceed requested length."""
        body = self._api(length="2").json()
        self.assertLessEqual(len(body["data"]), 2)

    def test_length_capped_at_1000(self):
        """Requesting length > 1000 must be capped to 1000."""
        body = self._api(length="99999").json()
        self.assertLessEqual(len(body["data"]), 1000)

    def test_search_filters(self):
        """Searching for a nonexistent term must return zero matches."""
        filtered = self._api(**{"search[value]": "ZZZZNONEXISTENT99999"}).json()
        self.assertEqual(filtered["recordsFiltered"], 0,
                         "Nonexistent search term should yield 0 filtered records")
        self.assertEqual(len(filtered["data"]), 0,
                         "Nonexistent search term should return no rows")

    def test_sort_direction(self):
        """Sort direction parameter must produce opposite row ordering."""
        asc = self._api(**{"order[0][dir]": "asc", "order[0][column]": "0"}).json()
        desc = self._api(**{"order[0][dir]": "desc", "order[0][column]": "0"}).json()
        self.assertEqual(asc["recordsTotal"], desc["recordsTotal"])
        if len(asc["data"]) > 1 and len(desc["data"]) > 1:
            # First column values should be in opposite order
            asc_vals = [row[0] for row in asc["data"]]
            desc_vals = [row[0] for row in desc["data"]]
            self.assertEqual(asc_vals, list(reversed(desc_vals)),
                             "asc and desc should produce reversed row order")

    def test_missing_server_returns_error(self):
        """Missing server parameter should return an error response."""
        body = self._api(server="").json()
        self.assertIn("error", body)

    def test_invalid_server_returns_error(self):
        """A server name not in config must return an error, not crash."""
        body = self._api(server="nonexistent_server_xyz").json()
        self.assertIn("error", body)
        self.assertEqual(body["recordsTotal"], 0)

    def test_missing_table_returns_error(self):
        """Missing table parameter should return an error response."""
        body = self._api(table="").json()
        self.assertIn("error", body)

    def test_invalid_order_dir_defaults_to_asc(self):
        """Invalid order direction should not crash; server normalises to 'asc'."""
        body = self._api(**{"order[0][dir]": "DROP TABLE"}).json()
        self.assertNotIn("error", body)
        self.assertIsInstance(body["data"], list)

    def test_negative_start_clamped(self):
        """Negative start offset should be clamped to 0, not error."""
        body = self._api(start="-5").json()
        self.assertNotIn("error", body)
        self.assertIsInstance(body["data"], list)

    def test_zero_length_clamped(self):
        """Zero length should be clamped to 1, not return empty or error."""
        body = self._api(length="0").json()
        self.assertNotIn("error", body)
        self.assertLessEqual(len(body["data"]), 1)

    def test_second_page_offset(self):
        """Requesting start > 0 should return a different page of data."""
        page1 = self._api(start="0", length="2").json()
        page2 = self._api(start="2", length="2").json()
        # Both must succeed
        self.assertIsInstance(page1["data"], list)
        self.assertIsInstance(page2["data"], list)
        if page1["recordsTotal"] > 2:
            self.assertNotEqual(page1["data"], page2["data"],
                                "Page 1 and page 2 should contain different rows")

    def test_search_known_value(self):
        """Searching for a value present in the table should return matching rows."""
        full = self._api().json()
        if full["recordsTotal"] > 0:
            # Pick a value from the first row to search for
            first_row = full["data"][0]
            search_val = str(first_row[0])
            filtered = self._api(**{"search[value]": search_val}).json()
            self.assertGreater(filtered["recordsFiltered"], 0,
                               f"Searching for '{search_val}' should find at least one row")
            # Verify returned rows actually contain the search value
            for row in filtered["data"]:
                row_str = " ".join(str(c) for c in row)
                self.assertIn(search_val, row_str,
                              f"Row {row} should contain search term '{search_val}'")

    def test_non_numeric_draw_returns_error(self):
        """Non-numeric draw parameter should return gracefully."""
        resp = self._api(draw="notanumber")
        body = resp.json()
        # Should either error cleanly or default — must not 500
        self.assertIn(resp.status_code, (200, 400))


class TestWildcardSearchEscape(unittest.TestCase):
    """DataTables search must treat '%' and '_' as literal characters.

    Regression guard: the LIKE clause in `get_table_content_paginated` used
    an unescaped search value, so a search for "%" expanded into a SQL
    wildcard and matched every row instead of only rows literally
    containing "%". The fix escapes '%', '_' and '!' via `ESCAPE '!'`.
    """

    SERVER   = SERVER
    DATABASE = "main"
    TABLE    = "mysql_servers"

    PCT_HG,  PCT_HOST,  PCT_PORT  = 97, "pct-marker-host", 3311
    UND_HG,  UND_HOST,  UND_PORT  = 96, "und-marker-host", 3312
    BNG_HG,  BNG_HOST,  BNG_PORT  = 95, "bng-marker-host", 3313
    PCT_COMMENT = "literal%pct%only"
    UND_COMMENT = "literal_und_only"
    BNG_COMMENT = "literal!bang!only"

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()
        self.s.get(f"/{self.SERVER}/{self.DATABASE}/{self.TABLE}/")
        self._insert(self.PCT_HG, self.PCT_HOST, self.PCT_PORT, self.PCT_COMMENT)
        self._insert(self.UND_HG, self.UND_HOST, self.UND_PORT, self.UND_COMMENT)
        self._insert(self.BNG_HG, self.BNG_HOST, self.BNG_PORT, self.BNG_COMMENT)

    def tearDown(self):
        self._delete(self.PCT_HG, self.PCT_HOST, self.PCT_PORT)
        self._delete(self.UND_HG, self.UND_HOST, self.UND_PORT)
        self._delete(self.BNG_HG, self.BNG_HOST, self.BNG_PORT)

    def _insert(self, hg, host, port, comment):
        self.s.post_json("/api/insert_row", {
            "server":      self.SERVER,
            "database":    self.DATABASE,
            "table":       self.TABLE,
            "columnNames": ["hostgroup_id", "hostname", "port", "comment"],
            "data": {
                "hostgroup_id": str(hg),
                "hostname":     host,
                "port":         str(port),
                "comment":      comment,
            },
        })

    def _delete(self, hg, host, port):
        self.s.post_json("/api/delete_row", {
            "server":   self.SERVER,
            "database": self.DATABASE,
            "table":    self.TABLE,
            "pkValues": {
                "hostgroup_id": str(hg),
                "hostname":     host,
                "port":         str(port),
            },
        })

    def _search(self, value):
        return self.s.get_table_data(self.SERVER, self.DATABASE, self.TABLE,
                                     **{"search[value]": value})

    def test_percent_is_literal_not_wildcard(self):
        body = self._search("%")
        self.assertGreater(body["recordsFiltered"], 0,
                           "'%' must match the seeded row containing '%'")
        self.assertLess(body["recordsFiltered"], body["recordsTotal"],
                        "'%' must not match every row — it should be a literal")
        for row in body["data"]:
            self.assertIn("%", " ".join(str(c) for c in row))

    def test_underscore_is_literal_not_single_char_wildcard(self):
        body = self._search("_")
        self.assertGreater(body["recordsFiltered"], 0,
                           "'_' must match the seeded row containing '_'")
        self.assertLess(body["recordsFiltered"], body["recordsTotal"],
                        "'_' must not match every row — it should be a literal")
        for row in body["data"]:
            self.assertIn("_", " ".join(str(c) for c in row))

    def test_escape_char_is_literal(self):
        body = self._search("!")
        self.assertGreater(body["recordsFiltered"], 0,
                           "'!' must match the seeded row containing '!'")
        self.assertLess(body["recordsFiltered"], body["recordsTotal"],
                        "'!' must not match every row — it should be a literal")
        for row in body["data"]:
            self.assertIn("!", " ".join(str(c) for c in row))


class TestProxySQL3Autocommit(unittest.TestCase):
    """ProxySQL 3.x admin rejects SET @@session.autocommit which
    mysql-connector-python sends internally when setting the autocommit
    property.  The fix removes the autocommit setter from db_connect()
    entirely since it is unnecessary for ProxySQL admin connections.

    With the test stack running ProxySQL 3.x, every page load exercises
    this code path.  This test explicitly verifies that browsing a table
    and running SQL (which trigger db_connect) succeed rather than
    returning a 500.
    """

    def setUp(self):
        self.s = ProxyWebSession()
        self.s.login()

    def test_table_browse_succeeds_on_proxysql3(self):
        """Browsing a table on ProxySQL 3.x must not crash due to autocommit."""
        resp = self.s.get(f"/{SERVER}/{DATABASE}/global_variables/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("500 Internal Server Error", resp.text)
        # The page should contain actual table data, not an error
        self.assertIn("variable_name", resp.text.lower())

    def test_sql_query_succeeds_on_proxysql3(self):
        """Running a SELECT via the SQL form must work on ProxySQL 3.x."""
        resp = self.s.post_form(
            f"/{SERVER}/{DATABASE}/global_variables/sql/",
            data={"sql": "SELECT * FROM global_variables LIMIT 5"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("Query Error", resp.text)

if __name__ == "__main__":
    unittest.main()
