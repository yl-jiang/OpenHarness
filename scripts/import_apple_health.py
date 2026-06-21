#!/usr/bin/env python3
"""
Import Apple Health export data into solo health_records table.

Usage:
    uv run python scripts/import_apple_health.py /path/to/export.zip
    uv run python scripts/import_apple_health.py /path/to/导出.xml
    uv run python scripts/import_apple_health.py /path/to/export.zip --dry-run
    uv run python scripts/import_apple_health.py /path/to/export.zip --date-from 2025-01-01
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from solo.core.store import SoloStore  # noqa: E402
from solo.core.models import SoloHealthRecord  # noqa: E402
from solo.core.apple_health import AppleHealthImporter  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Import Apple Health data")
    parser.add_argument("source", type=Path, help="Path to export.zip or export.xml")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workspace", default="~/.solo")
    parser.add_argument("--date-from", default=None, help="YYYY-MM-DD")
    args = parser.parse_args()

    if not args.source.exists():
        print(f"Not found: {args.source}")
        sys.exit(1)

    print(f"Parsing: {args.source}")
    file_bytes = args.source.read_bytes()
    importer = AppleHealthImporter()
    parsed = importer.parse(file_bytes, args.source.name, date_from=args.date_from)
    print(f"  {parsed.record_count} records parsed, {len(parsed.daily)} days, {len(parsed.workouts)} workout days")

    if not parsed.daily and not parsed.workouts:
        print("No health data found.")
        return

    # Build records
    if args.dry_run:
        records, result = importer.build_records(parsed)
        print(f"\n[DRY RUN] Would insert {len(records)} records:")
        for r in records[:15]:
            print(f"  {r['date']} | {r['category']:8s} | {r['item']}")
        if len(records) > 15:
            print(f"  ... and {len(records) - 15} more")
        print(f"\nBy type: {result.by_type}")
    else:
        store = SoloStore(Path(args.workspace).expanduser())
        # Delete all existing Apple Health records before re-importing
        existing = [
            r for r in store.list_health_records(subject="self")
            if r.source == "import" and "apple_health" in (r.tags or "")
        ]
        for r in existing:
            store.delete_health_record(r.id)
        if existing:
            print(f"  Deleted {len(existing)} existing Apple Health records")

        record_dicts, result = importer.build_records(parsed)
        print(f"\n{result.message}")
        if result.date_range:
            print(f"  Date range: {result.date_range}")
        if result.by_type:
            print(f"  By type: {result.by_type}")

        for i, rd in enumerate(record_dicts):
            store.add_health_record(SoloHealthRecord(**rd))
            if (i + 1) % 50 == 0:
                print(f"  ... inserted {i + 1}/{len(record_dicts)}")

        cats = store.health_record_categories()
        print(f"\nDone. Total health records: {sum(cats.values())}")
        for cat, count in sorted(cats.items()):
            print(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
