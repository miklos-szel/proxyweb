#!/usr/bin/env python3
"""Persistent per-server query history: dropdown, full page, clear, isolation."""

import re
import unittest

from testlib import (
    ProxyWebSession, BASE_URL, USERNAME, PASSWORD,
    SERVER, PG_SERVER, DATABASE,
)


class TestQueryHistory(unittest.TestCase):
    """Verify per-server persistent query history across two servers.

    Runs 26 unique queries per server (different templates and LIMIT values),
    checks that:
    - the Query History dropdown shows only the last 10 for each server
    - the Full Query History page shows all queries for each server
    - histories are isolated: server1 queries don't leak into server2
    - clear works per server without affecting the other
    """

    S1 = SERVER             # "proxysql_mysql"
    S2 = PG_SERVER          # "proxysql_postgres"
    TOTAL_PER_SERVER = 26

    # Server 1: each query selects different columns + different LIMIT
    S1_TEMPLATES = [
        "SELECT variable_name FROM global_variables LIMIT {n}",
        "SELECT variable_value FROM global_variables LIMIT {n}",
        "SELECT variable_name, variable_value FROM global_variables LIMIT {n}",
        "SELECT variable_name FROM global_variables WHERE variable_name LIKE 'mysql%%' LIMIT {n}",
        "SELECT variable_value FROM global_variables WHERE variable_name LIKE 'mysql%%' LIMIT {n}",
        "SELECT variable_name, variable_value FROM global_variables WHERE variable_name LIKE 'admin%%' LIMIT {n}",
        "SELECT variable_name FROM global_variables ORDER BY variable_name LIMIT {n}",
        "SELECT variable_value FROM global_variables ORDER BY variable_name LIMIT {n}",
        "SELECT variable_name, variable_value FROM global_variables ORDER BY variable_name LIMIT {n}",
        "SELECT variable_name FROM global_variables ORDER BY variable_name DESC LIMIT {n}",
        "SELECT variable_value FROM global_variables ORDER BY variable_name DESC LIMIT {n}",
        "SELECT variable_name, variable_value FROM global_variables ORDER BY variable_name DESC LIMIT {n}",
        "SELECT variable_name FROM global_variables ORDER BY variable_value LIMIT {n}",
        "SELECT variable_value FROM global_variables ORDER BY variable_value LIMIT {n}",
        "SELECT variable_name, variable_value FROM global_variables ORDER BY variable_value LIMIT {n}",
        "SELECT variable_name FROM global_variables ORDER BY variable_value DESC LIMIT {n}",
        "SELECT variable_value FROM global_variables ORDER BY variable_value DESC LIMIT {n}",
        "SELECT variable_name, variable_value FROM global_variables ORDER BY variable_value DESC LIMIT {n}",
        "SELECT COUNT(*) FROM global_variables WHERE variable_name LIKE 'mysql%%' LIMIT {n}",
        "SELECT COUNT(*) FROM global_variables WHERE variable_name LIKE 'admin%%' LIMIT {n}",
        "SELECT COUNT(*) FROM global_variables LIMIT {n}",
        "SELECT variable_name FROM global_variables WHERE variable_value != '' LIMIT {n}",
        "SELECT variable_value FROM global_variables WHERE variable_value != '' LIMIT {n}",
        "SELECT variable_name FROM global_variables WHERE variable_name LIKE 'admin%%' ORDER BY variable_name LIMIT {n}",
        "SELECT variable_value FROM global_variables WHERE variable_name LIKE 'admin%%' ORDER BY variable_name LIMIT {n}",
        "SELECT variable_name, variable_value FROM global_variables WHERE variable_value != '' ORDER BY variable_name LIMIT {n}",
    ]

    # Server 2: connection-pool-style queries, each different
    S2_TEMPLATES = [
        "SELECT ConnUsed FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnFree FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnOK FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnERR FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT MaxConnUsed FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT Queries FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT Bytes_data_sent FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnUsed, ConnFree FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnOK, ConnERR FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT MaxConnUsed, Queries FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnUsed, ConnFree, ConnOK FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnERR, MaxConnUsed, Queries FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT Bytes_data_sent, ConnUsed FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnUsed FROM stats_mysql_connection_pool ORDER BY ConnUsed LIMIT {n}",
        "SELECT ConnFree FROM stats_mysql_connection_pool ORDER BY ConnFree LIMIT {n}",
        "SELECT ConnOK FROM stats_mysql_connection_pool ORDER BY ConnOK LIMIT {n}",
        "SELECT ConnERR FROM stats_mysql_connection_pool ORDER BY ConnERR LIMIT {n}",
        "SELECT MaxConnUsed FROM stats_mysql_connection_pool ORDER BY MaxConnUsed LIMIT {n}",
        "SELECT Queries FROM stats_mysql_connection_pool ORDER BY Queries LIMIT {n}",
        "SELECT Bytes_data_sent FROM stats_mysql_connection_pool ORDER BY Bytes_data_sent LIMIT {n}",
        "SELECT ConnUsed, ConnFree FROM stats_mysql_connection_pool ORDER BY ConnUsed DESC LIMIT {n}",
        "SELECT ConnOK, ConnERR FROM stats_mysql_connection_pool ORDER BY ConnOK DESC LIMIT {n}",
        "SELECT MaxConnUsed, Queries FROM stats_mysql_connection_pool ORDER BY Queries DESC LIMIT {n}",
        "SELECT Bytes_data_sent FROM stats_mysql_connection_pool ORDER BY Bytes_data_sent DESC LIMIT {n}",
        "SELECT ConnUsed, ConnFree, ConnOK, ConnERR FROM stats_mysql_connection_pool LIMIT {n}",
        "SELECT ConnUsed, ConnFree, ConnOK, ConnERR, MaxConnUsed, Queries, Bytes_data_sent FROM stats_mysql_connection_pool LIMIT {n}",
    ]

    @classmethod
    def setUpClass(cls):
        """
        Prepare class-level test state by creating an authenticated ProxyWebSession and clearing query history on both test servers.
        
        Initializes cls.pw with a logged-in ProxyWebSession and removes any existing per-server query history for cls.S1 and cls.S2 so tests start with empty histories.
        """
        cls.pw = ProxyWebSession()
        cls.pw.login()
        # clear any pre-existing history on both servers
        cls.pw.post_json("/api/clear_query_history", {"server": cls.S1})
        cls.pw.post_json("/api/clear_query_history", {"server": cls.S2})

    def _execute_query(self, server, sql):
        """
        Execute a SELECT statement via the web UI SQL form for the specified ProxySQL server.
        
        Parameters:
            server (str): Name of the ProxySQL server as used in the URL path (e.g., "proxysql").
            sql (str): The SELECT SQL statement to submit (may include leading whitespace).
        
        Returns:
            response (requests.Response): HTTP response returned by the form submission.
        """
        return self.pw.post_form(
            f"/{server}/{DATABASE}/global_variables/sql/",
            data={"sql": sql},
        )

    def _get_dropdown_section(self, html):
        """
        Extracts the HTML fragment for the Query History dropdown from a table page.
        
        Parameters:
            html (str): Full page HTML containing the history dropdown.
        
        Returns:
            str: The HTML fragment starting at the `id="historyBtn"` marker and ending before the closing `</ul>` tag.
        
        Raises:
            IndexError: If the history dropdown marker is not found in `html`.
        """
        return html.split('id="historyBtn"')[1].split('</ul>')[0]

    def _get_history_table_section(self, html):
        """
        Return the HTML fragment corresponding to the query history table section from a full history page.
        
        Parameters:
            html (str): Full HTML of the query history page.
        
        Returns:
            str: The HTML fragment for the history table (portion from the table's start attributes up to but not including the closing `</table>`), or an empty string if the history table is not present.
        """
        if 'id="historyTable"' not in html:
            return ""
        return html.split('id="historyTable"')[1].split('</table>')[0]

    def _count_dropdown_items(self, html):
        """
        Count query history dropdown items in the given HTML page.
        
        Parameters:
            html (str): HTML content of the page to search for history dropdown entries.
        
        Returns:
            int: Number of history item links found.
        """
        return len(re.findall(
            r'onclick="loadQuery\(window\[\'history_item_\d+\'\]\)',
            html,
        ))

    def _get_history_js_vars(self, html):
        """
        Extract SQL strings stored in history_item JavaScript variables from the provided HTML.
        
        Parameters:
            html (str): HTML content to search for `window['history_item_<n>']` assignments.
        
        Returns:
            list[str]: SQL statements extracted from matching history_item variables, in the order found.
        """
        return re.findall(
            r"window\['history_item_\d+'\]\s*=\s*\"(.*?)\"",
            html,
        )

    def _get_history_limits(self, js_vars):
        """
        Extract trailing LIMIT values from a sequence of SQL strings.
        
        Parameters:
            js_vars (Iterable[str]): Iterable of SQL statement strings (typically from JS variables).
        
        Returns:
            list[int]: List of integers parsed from trailing `LIMIT N` clauses in the same order as inputs; entries without a trailing `LIMIT` are omitted.
        """
        limits = []
        for sql in js_vars:
            m = re.search(r'LIMIT (\d+)$', sql)
            if m:
                limits.append(int(m.group(1)))
        return limits

    def test_query_history_both_servers(self):
        """
        Verify per-server query history isolation and UI behavior after executing multiple queries.
        
        Executes 26 distinct queries on each of two servers, then verifies for each server that:
        - the query dropdown shows the most recent 10 entries,
        - the full history page lists all 26 queries,
        - no queries from the other server appear in either the dropdown or full history.
        Finally clears the first server's history and asserts it is emptied while the second server's history remains intact, then clears the second server.
        """

        # --- execute 26 distinct queries on server 1 ----------------------
        for n in range(1, self.TOTAL_PER_SERVER + 1):
            sql = self.S1_TEMPLATES[n - 1].format(n=n)
            resp = self._execute_query(self.S1, sql)
            self.assertEqual(resp.status_code, 200,
                             f"S1 query {n} HTTP failed: {resp.status_code}")
            self.assertNotIn("Query Error", resp.text,
                             f"S1 query {n} returned an error")

        # --- execute 26 distinct queries on server 2 ----------------------
        for n in range(1, self.TOTAL_PER_SERVER + 1):
            sql = self.S2_TEMPLATES[n - 1].format(n=n)
            resp = self._execute_query(self.S2, sql)
            self.assertEqual(resp.status_code, 200,
                             f"S2 query {n} HTTP failed: {resp.status_code}")
            self.assertNotIn("Query Error", resp.text,
                             f"S2 query {n} returned an error")

        # === Server 1 checks =============================================

        # -- dropdown: should show last 10 of 26 (queries 17..26) ----------
        resp = self.pw.get(f"/{self.S1}/{DATABASE}/global_variables/")
        s1_html = resp.text
        self.assertEqual(self._count_dropdown_items(s1_html), 10,
                         "S1 dropdown should have exactly 10 items")

        # Check full SQL via JS variables (dropdown display is truncated)
        s1_js_vars = self._get_history_js_vars(s1_html)
        s1_limits = self._get_history_limits(s1_js_vars)
        # last 10 should be present (LIMIT 17 through LIMIT 26)
        for n in range(self.TOTAL_PER_SERVER - 9, self.TOTAL_PER_SERVER + 1):
            self.assertIn(n, s1_limits,
                          f"S1 dropdown missing LIMIT {n}")
        # oldest 16 should NOT be in dropdown
        for n in range(1, self.TOTAL_PER_SERVER - 9):
            self.assertNotIn(n, s1_limits,
                             f"S1 dropdown should not contain LIMIT {n}")

        # -- S1 dropdown must NOT contain S2-specific text -----------------
        s1_js_text = "\n".join(s1_js_vars)
        self.assertNotIn("stats_mysql_connection_pool", s1_js_text,
                         "S1 dropdown leaks S2 queries")

        # -- full history page: all 26 ------------------------------------
        resp = self.pw.get(f"/{self.S1}/query_history/")
        s1_full = resp.text
        s1_table = self._get_history_table_section(s1_full)
        self.assertIn("26 queries", s1_full,
                       "S1 full history should show '26 queries'")
        for n in range(1, self.TOTAL_PER_SERVER + 1):
            self.assertRegex(s1_table, rf'LIMIT {n}\b',
                             f"S1 full history missing LIMIT {n}")
        # S2 queries must not appear on S1 full history table
        self.assertNotIn("stats_mysql_connection_pool", s1_table,
                         "S1 full history leaks S2 queries")

        # === Server 2 checks =============================================

        # -- dropdown: should show last 10 of 26 (queries 17..26) ----------
        resp = self.pw.get(f"/{self.S2}/{DATABASE}/global_variables/")
        s2_html = resp.text
        self.assertEqual(self._count_dropdown_items(s2_html), 10,
                         "S2 dropdown should have exactly 10 items")

        s2_js_vars = self._get_history_js_vars(s2_html)
        s2_limits = self._get_history_limits(s2_js_vars)
        for n in range(self.TOTAL_PER_SERVER - 9, self.TOTAL_PER_SERVER + 1):
            self.assertIn(n, s2_limits,
                          f"S2 dropdown missing LIMIT {n}")
        for n in range(1, self.TOTAL_PER_SERVER - 9):
            self.assertNotIn(n, s2_limits,
                             f"S2 dropdown should not contain LIMIT {n}")

        # -- S2 dropdown must NOT contain S1-specific text -----------------
        s2_js_text = "\n".join(s2_js_vars)
        self.assertNotIn("global_variables", s2_js_text,
                         "S2 dropdown leaks S1 queries")

        # -- full history page: all 26 ------------------------------------
        resp = self.pw.get(f"/{self.S2}/query_history/")
        s2_full = resp.text
        s2_table = self._get_history_table_section(s2_full)
        self.assertIn("26 queries", s2_full,
                       "S2 full history should show '26 queries'")
        for n in range(1, self.TOTAL_PER_SERVER + 1):
            self.assertRegex(s2_table, rf'LIMIT {n}\b',
                             f"S2 full history missing LIMIT {n}")
        self.assertNotIn("global_variables", s2_table,
                         "S2 full history leaks S1 queries")

        # === Clear server 1, verify server 2 is untouched ================
        clear_resp = self.pw.post_json(
            "/api/clear_query_history", {"server": self.S1}
        )
        self.assertTrue(clear_resp.json().get("success"))

        # S1 should be empty
        resp = self.pw.get(f"/{self.S1}/query_history/")
        self.assertIn("No query history", resp.text)
        self.assertIn("0 queries", resp.text)

        resp = self.pw.get(f"/{self.S1}/{DATABASE}/global_variables/")
        s1_dropdown = self._get_dropdown_section(resp.text)
        self.assertIn("No queries yet", s1_dropdown)

        # S2 should still have all its queries
        resp = self.pw.get(f"/{self.S2}/query_history/")
        self.assertIn("26 queries", resp.text,
                       "S2 history should survive S1 clear")

        # === Clean up: clear server 2 too ================================
        self.pw.post_json("/api/clear_query_history", {"server": self.S2})
        resp = self.pw.get(f"/{self.S2}/query_history/")
        self.assertIn("No query history", resp.text)

    def test_clear_query_history_malformed_json(self):
        """Sending an empty or malformed JSON body to clear_query_history
        should not cause a 500.  The endpoint uses get_json(silent=True)
        and falls back to the session server, so these return 200 when a
        valid server is in the session."""
        # Empty body with JSON content type — falls back to session server
        resp = self.pw.session.post(
            f"{BASE_URL}/api/clear_query_history",
            data="",
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": self.pw.csrf_token,
            },
            timeout=10,
        )
        self.assertNotEqual(resp.status_code, 500,
                            "Empty body should not cause a 500")

        # Malformed JSON — also falls back to session server
        resp = self.pw.session.post(
            f"{BASE_URL}/api/clear_query_history",
            data="{bad json",
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": self.pw.csrf_token,
            },
            timeout=10,
        )
        self.assertNotEqual(resp.status_code, 500,
                            "Malformed JSON should not cause a 500")

if __name__ == "__main__":
    unittest.main()
