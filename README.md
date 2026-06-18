# Structured Literature Surveys

A repeatable, inspectable pipeline for structured literature search. It creates dated search-run directories, collects bibliographic metadata from digital libraries, deduplicates candidates, generates BibTeX, and reports what still needs manual attention.

## Prerequisites

- Python 3.10 or later (no extra packages required)
- Git (used to stamp the code version in run metadata)
- Run all commands from the project root directory

## Concepts

**Search run** — one execution of the pipeline for a given query. Each run gets its own directory under `searches/YYYY-MM-DD_slug/` so nothing is ever overwritten. You can have many runs for the same topic.

**Candidate** — a deduplicated literature record. Multiple source records that refer to the same paper (matched by DOI, then canonical URL, then normalized title + year) collapse into one candidate with a stable `candidate_id`.

**Candidate ID** — a human-readable key in the form `firstauthorYYYYshorttitle`, e.g. `firat2020treemapliteracyclassroom`. This is also used as the BibTeX citation key. It is stable across runs.

**Library** — project-level folders (`library/bibtex/`, `library/pdfs/`, `library/manifests/`) that accumulate across runs. BibTeX entries and PDF records are never stored only inside a dated run directory.

## End-to-End Workflow

### 1. Write your query

Queries use a small Boolean syntax: `AND`, `OR`, `NOT`, parentheses, bare terms, and quoted phrases. Single or double quotes are both accepted.

```text
('temporal' OR 'dynamic' OR 'animated') AND 'treemap'
```

### 2. Do a dry run first

A dry run creates the run directory and query files — including the translated query and the URL you would use to verify the search manually — without contacting any digital library.

```bash
python3 scripts/sls eg-search \
  --query "('temporal' OR 'dynamic' OR 'animated') AND 'treemap'" \
  --slug temporal-treemap \
  --dry-run
```

Open `searches/YYYY-MM-DD_temporal-treemap/query.md` to review the translated query and the manual verification URL before committing to a live run.

### 3. Run the search

```bash
python3 scripts/sls eg-search \
  --query "('temporal' OR 'dynamic' OR 'animated') AND 'treemap'" \
  --slug temporal-treemap
```

The script contacts the EG Digital Library API, collects all matching records, deduplicates them, generates BibTeX entries, and updates the project-level BibTeX database. It prints a summary when done:

```
run_dir=searches/2026-06-19_temporal-treemap
status=ok
source_reported_count=257
imported_count=257
deduplicated_count=201
```

Use `--max-results N` to limit the import during development or testing.

### 4. Review the outputs

Open the run directory. Everything needed to audit the run is there:

| File | What it contains |
|---|---|
| `query.md` | Generic query, translated query, and manual verification URL |
| `run_config.json` | Exact parameters used: page size, query text, code version, timestamp |
| `sources/eg/query_semantics.md` | Notes on what the translation preserved, approximated, or dropped |
| `sources/eg/source_manifest.json` | API URLs used, result count reported by EG, import count, timestamp |
| `sources/eg/raw/page_NNNN.json` | Raw API responses, preserved exactly as received |
| `sources/eg/eg_results.csv` | Normalized per-source records before deduplication |
| `merged_candidates.csv` | Deduplicated candidate list — the primary working file |
| `validation_report.md` | Count comparison, missing-field summary, any mismatches |
| `manual_action_queue.csv` | Candidates that still need a PDF, BibTeX, or manual review |
| `bibtex_update.bib` | BibTeX entries generated for this run (also merged into `library/bibtex/candidates.bib`) |
| `missing_pdfs.csv` | Shortlist of candidates without a local PDF |

### 5. Check the validation report

Open `validation_report.md`. If `source_reported_count` differs from `imported_count`, the report flags it. A mismatch expected when you used `--max-results`; otherwise it may indicate a pagination issue or an export cap to investigate.

### 6. Work through the manual action queue

Open `manual_action_queue.csv`. Each row is one action needed for one candidate. The `action` column indicates:

| Action | What to do |
|---|---|
| `missing_local_pdf` | Obtain the PDF through valid access and register it (see below) |
| `missing_bibtex` | BibTeX could not be generated — usually means a candidate is missing authors, year, or title |
| `review_duplicate` | Deduplication matched on title + year only (no DOI or URL available); confirm the merge is correct |

### 7. Obtain and register PDFs

The pipeline never downloads PDFs. After obtaining a PDF through a valid access route (institutional access, open access, author copy), register it:

```bash
python3 scripts/sls pdfs add \
  --candidate-id firat2020treemapliteracyclassroom \
  --pdf ~/Downloads/paper.pdf
```

The file is copied to `library/pdfs/<candidate-id>.pdf`, checksummed, and recorded in `library/manifests/pdfs.csv`. Use `--no-copy` to reference a PDF in its current location without copying.

To see which candidates across all runs are still missing a PDF:

```bash
python3 scripts/sls pdfs missing \
  --output library/manifests/missing_pdfs.csv
```

To scope the report to one run:

```bash
python3 scripts/sls pdfs missing \
  --run-dir searches/2026-06-19_temporal-treemap \
  --output searches/2026-06-19_temporal-treemap/missing_pdfs.csv
```

To refresh the PDF status in a run's `merged_candidates.csv` after registering PDFs (or after manually placing files named `<candidate-id>.pdf` in `library/pdfs/`):

```bash
python3 scripts/sls pdfs refresh searches/2026-06-19_temporal-treemap
```

## Currently Supported Sources

| Source | Mode | What is required |
|---|---|---|
| EG Digital Library (Eurographics) | Automated via DSpace REST API | Nothing — public API |
| ACM Digital Library | Manual export | Open in browser, search, export citation file |
| IEEE Xplore | API (not yet implemented) | API key |
| ScienceDirect | API (not yet implemented) | Elsevier API key |
| Scopus | API (not yet implemented) | Elsevier API key |
| Web of Science | API (not yet implemented) | Clarivate API key and license |
| Google Scholar | Not automated | Manual export only; no official bulk API |

For sources that require a manual export: run the pipeline with `--dry-run` first to get the translated query, execute the search in your browser, export using the library's own export controls, and place the export file in the appropriate `sources/<source>/` directory. Automated ingest of those exports is not yet implemented.

## Rerunning or Resuming a Search

By default the pipeline refuses to overwrite an existing run directory. To regenerate derived artifacts (CSV, reports, BibTeX update) while preserving raw API pages:

```bash
python3 scripts/sls eg-search \
  --query "..." \
  --slug temporal-treemap \
  --date 2026-06-19 \
  --overwrite-derived
```

Raw source exports (`sources/eg/raw/`) are always treated as immutable records of what was retrieved.

## Project-Level Library

`library/` accumulates across all search runs:

```
library/
  bibtex/candidates.bib   consolidated BibTeX for all known candidates
  pdfs/                   validly obtained PDFs, named <candidate-id>.pdf
  manifests/pdfs.csv      manifest mapping candidate IDs to PDF files
```

These files are the durable record of your survey. Do not store them only inside a dated run directory.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```
