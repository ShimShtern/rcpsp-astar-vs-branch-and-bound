#!/usr/bin/env python3
# Archived runner for the original full-start-vector B&B core.
"""Run RCPSP B&B using the original full-start-vector node representation.

The command-line interface and CSV format are shared with
``run_rcpsp_bb_py36.py``.  Only the selected B&B core differs.
"""
import run_rcpsp_bb_py36 as runner
from rcpsp_bb_core_py36 import branch_and_bound_rcpsp


def main():
    runner.branch_and_bound_rcpsp = branch_and_bound_rcpsp
    runner.SCRIPT_VERSION = runner.SCRIPT_VERSION + "-original-core"
    runner.main()


if __name__ == "__main__":
    main()
