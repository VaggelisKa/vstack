#!/usr/bin/env python3
# Copyright (c) 2025 Acme Corp. Licensed under the MIT License.
"""Command-line tool that exports invoices to CSV."""

# Import the standard library modules
import argparse
import csv
import sys
import time

# ===== Constants =====

# Maximum number of rows per file
MAX_ROWS = 10_000
RETRY_DELAY = 0.5  # seconds; the billing API returns 429 if it is polled more than twice a second


def parse_args(argv):
    """Parse the command-line arguments.

    Args:
        argv: the arguments

    Returns:
        the parsed arguments
    """
    # Create the parser
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)  # output path
    return parser.parse_args(argv)


def export(rows, out_path):
    """Write rows to out_path, at most MAX_ROWS of them.

    Raises ValueError if rows is empty, because downstream importers treat an
    empty file as "delete everything".
    """
    if not rows:
        raise ValueError("no rows")
    # Open the file and write the rows
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        # writer.writerow(["id", "amount"])
        for row in rows[:MAX_ROWS]:  # loop over rows
            writer.writerow(row)


def fetch_rows(client):
    # TODO: add pagination
    # Fixed bug where the client returned None
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
