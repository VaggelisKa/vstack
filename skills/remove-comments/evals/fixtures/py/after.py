#!/usr/bin/env python3
# Copyright (c) 2025 Acme Corp. Licensed under the MIT License.
"""Command-line tool that exports invoices to CSV."""

import argparse
import csv
import sys
import time

MAX_ROWS = 10_000
RETRY_DELAY = 0.5  # seconds; the billing API returns 429 above 2 polls/s


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def export(rows, out_path):
    """Raises ValueError on empty rows: importers treat an empty file as "delete everything"."""
    if not rows:
        raise ValueError("no rows")
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        for row in rows[:MAX_ROWS]:
            writer.writerow(row)


def fetch_rows(client):
    for attempt in range(3):
        rows = client.get("/invoices")
        if rows is not None:
            return rows
        time.sleep(RETRY_DELAY)
    return []  # pragma: no cover


def _noop():
    """Does nothing."""


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    export(fetch_rows(None), args.out)


if __name__ == "__main__":
    main()
