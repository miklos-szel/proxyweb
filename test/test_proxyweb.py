#!/usr/bin/env python3
"""
Integration test entrypoint for ProxyWeb.

Waits for the ProxyWeb stack to become ready, then discovers and runs every
``test_*.py`` file under ``test/cases/`` with the shared colored runner from
``testlib``. Individual topical modules can also be run directly (each has its
own ``if __name__ == "__main__": unittest.main()`` footer).

Bring the stack up with ``run_tests.sh`` or set PROXYWEB_URL to point at an
already-running instance.
"""

import os
import sys
import unittest

from testlib import BASE_URL, ColoredRunner, wait_for_proxyweb


def main():
    print(f"Waiting for ProxyWeb at {BASE_URL} ...")
    try:
        wait_for_proxyweb()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print("ProxyWeb is ready. Running tests.\n")
    here = os.path.dirname(os.path.abspath(__file__))
    suite = unittest.TestLoader().discover(
        start_dir=os.path.join(here, "cases"),
        pattern="test_*.py",
        top_level_dir=here,
    )
    result = ColoredRunner().run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
