from __future__ import annotations

import argparse
import sys

from .eg import run_eg_search


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sls",
        description="Structured literature search spike tooling.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    eg = subparsers.add_parser(
        "eg-search",
        help="Run the EG Digital Library spike workflow.",
    )
    eg.add_argument(
        "--query",
        required=True,
        help="Generic Boolean query, e.g. \"('temporal' OR 'dynamic') AND 'treemap'\".",
    )
    eg.add_argument(
        "--slug",
        help="Human-readable search slug. Defaults to a slug derived from the query.",
    )
    eg.add_argument(
        "--date",
        help="Run date in YYYY-MM-DD form. Defaults to today's local date.",
    )
    eg.add_argument(
        "--max-results",
        type=int,
        help="Maximum records to import for the spike. Defaults to all EG results.",
    )
    eg.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="EG API page size. Defaults to 100.",
    )
    eg.add_argument(
        "--overwrite-derived",
        action="store_true",
        help="Allow regenerating derived artifacts in an existing run directory.",
    )
    eg.add_argument(
        "--dry-run",
        action="store_true",
        help="Create the run skeleton and query reports without contacting EG.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "eg-search":
        result = run_eg_search(
            generic_query=args.query,
            slug=args.slug,
            run_date=args.date,
            max_results=args.max_results,
            page_size=args.page_size,
            overwrite_derived=args.overwrite_derived,
            dry_run=args.dry_run,
        )
        print(f"run_dir={result.run_dir}")
        print(f"status={result.status}")
        print(f"source_reported_count={result.source_reported_count}")
        print(f"imported_count={result.imported_count}")
        print(f"deduplicated_count={result.deduplicated_count}")
        return 0

    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
