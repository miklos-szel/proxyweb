"""Local history I/O regressions; run from the repository root with unittest."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestQueryHistoryReadFailures(unittest.TestCase):
    """Appending must preserve history when a read fails, while display stays tolerant."""

    def test_append_propagates_read_errors_without_writing(self):
        import mdb

        with tempfile.TemporaryDirectory() as directory, patch.object(mdb, 'HISTORY_DIR', directory):
            path = Path(directory) / 'test.json'
            path.write_text('[{"sql": "SELECT 1"}]')
            original = path.read_bytes()
            for error in (PermissionError('denied'), OSError('read failed'), ValueError('bad JSON')):
                with self.subTest(error=type(error).__name__), patch.object(mdb.json, 'load', side_effect=error):
                    with self.assertRaises(type(error)):
                        mdb.append_query_history('test', 'SELECT 2')
                    self.assertEqual(mdb.load_query_history('test'), [])
                self.assertEqual(path.read_bytes(), original)

    def test_append_initializes_missing_history(self):
        import mdb

        with tempfile.TemporaryDirectory() as directory, patch.object(mdb, 'HISTORY_DIR', directory):
            self.assertEqual(mdb.load_query_history('test'), [])
            mdb.append_query_history('test', 'SELECT 1')
            self.assertEqual([entry['sql'] for entry in mdb.load_query_history('test')], ['SELECT 1'])


