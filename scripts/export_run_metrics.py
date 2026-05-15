#!/usr/bin/env python3
# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Export run cost/payload metrics to JSONL and compare before/after snapshots."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "persistence"))

from run_metrics_export import (  # noqa: E402
    compare_run_exports,
    export_runs_to_jsonl,
    format_comparison_report,
    read_jsonl,
    summarize_runs,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export and compare PHYLUM run metrics (JSONL).")
    parser.add_argument(
        "--db",
        default=None,
        help="Path to agent_state.db (default: Persistence default).",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Write metrics JSONL to this file.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to --output instead of overwriting.",
    )
    parser.add_argument(
        "--request-id",
        action="append",
        dest="request_ids",
        help="Export specific request_id(s) only.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max runs to export (newest first).",
    )
    parser.add_argument(
        "--include-without-cost",
        action="store_true",
        help="Include runs with no cost metrics recorded.",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("BEFORE.jsonl", "AFTER.jsonl"),
        help="Compare two JSONL exports and print a table.",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        help="Print JSON summary for a single JSONL file.",
    )
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    if args.compare:
        before_path, after_path = (Path(p) for p in args.compare)
        comparison = compare_run_exports(read_jsonl(before_path), read_jsonl(after_path))
        print(format_comparison_report(comparison))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(comparison, indent=2, ensure_ascii=False), encoding="utf-8")
        return 0

    if args.summary:
        summary = summarize_runs(read_jsonl(args.summary))
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    if not args.output:
        print("error: --output is required unless using --compare or --summary", file=sys.stderr)
        return 2

    count = await export_runs_to_jsonl(
        args.output,
        db_path=args.db,
        request_ids=args.request_ids,
        limit=args.limit,
        append=args.append,
        include_without_cost=args.include_without_cost,
    )
    print(f"Wrote {count} run(s) to {args.output}")
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
