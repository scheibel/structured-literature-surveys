from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .eg import run_eg_search
from .pdfs import register_pdf, refresh_run_pdf_status, write_missing_pdf_report


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

    pdfs = subparsers.add_parser(
        "pdfs",
        help="Manage validly obtained local PDFs and missing-PDF reports.",
    )
    pdf_subparsers = pdfs.add_subparsers(dest="pdf_command", required=True)

    missing = pdf_subparsers.add_parser(
        "missing",
        help="Write a CSV report of candidates that still lack a local PDF.",
    )
    missing.add_argument(
        "--run-dir",
        help="Optional search run directory. Defaults to all runs under searches/.",
    )
    missing.add_argument(
        "--output",
        default="library/manifests/missing_pdfs.csv",
        help="Output CSV path. Defaults to library/manifests/missing_pdfs.csv.",
    )

    add = pdf_subparsers.add_parser(
        "add",
        help="Register a validly obtained PDF for one candidate.",
    )
    add.add_argument("--candidate-id", required=True, help="Candidate ID from merged_candidates.csv.")
    add.add_argument("--pdf", required=True, help="Path to the validly obtained PDF file.")
    add.add_argument(
        "--no-copy",
        action="store_true",
        help="Reference the PDF in place instead of copying it to library/pdfs/.",
    )
    add.add_argument("--note", default="", help="Optional manifest note.")
    add.add_argument(
        "--refresh-run",
        help="Optional run directory to refresh after registering the PDF.",
    )

    refresh = pdf_subparsers.add_parser(
        "refresh",
        help="Refresh local PDF status for one search run.",
    )
    refresh.add_argument("run_dir", help="Search run directory containing merged_candidates.csv.")

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

    if args.command == "pdfs":
        if args.pdf_command == "missing":
            rows = write_missing_pdf_report(
                Path(args.output),
                Path(args.run_dir) if args.run_dir else None,
            )
            print(f"output={args.output}")
            print(f"missing_count={len(rows)}")
            return 0

        if args.pdf_command == "add":
            registration = register_pdf(
                candidate_id=args.candidate_id,
                source_pdf=Path(args.pdf),
                copy=not args.no_copy,
                note=args.note,
            )
            print(f"candidate_id={registration.candidate_id}")
            print(f"pdf_path={registration.pdf_path}")
            print(f"checksum={registration.checksum}")
            print(f"file_size={registration.file_size}")
            if args.refresh_run:
                mapped = refresh_run_pdf_status(Path(args.refresh_run))
                print(f"refreshed_run={args.refresh_run}")
                print(f"mapped_pdf_count={mapped}")
            return 0

        if args.pdf_command == "refresh":
            mapped = refresh_run_pdf_status(Path(args.run_dir))
            print(f"run_dir={args.run_dir}")
            print(f"mapped_pdf_count={mapped}")
            return 0

    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
