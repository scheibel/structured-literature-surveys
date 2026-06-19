# Structured Literature Surveys

A repeatable, inspectable pipeline for structured literature search. It creates dated search-run directories, collects bibliographic metadata from digital libraries, deduplicates candidates, generates BibTeX, and reports what still needs manual attention.

## Prerequisites

- Python 3.10 or later (no extra packages required)
- Git (used to stamp the code version in run metadata)
- Run all commands from the project root directory
- Enough disk space for raw API responses, CSV files, BibTeX, and PDFs you manually add

Check the tool is available:

```bash
python3 scripts/sls --help
```

## Concepts

**Search run** — one execution of the pipeline for a given query. Each run gets its own directory under `searches/YYYY-MM-DD_slug/` so nothing is ever overwritten. You can have many runs for the same topic.

**Candidate** — a deduplicated literature record. Multiple source records that refer to the same paper (matched by DOI, then canonical URL, then normalized title + year) collapse into one candidate with a stable `candidate_id`.

**Candidate ID** — a human-readable key in the form `firstauthorYYYYshorttitle`, e.g. `firat2020treemapliteracyclassroom`. This is also used as the BibTeX citation key. It is stable across runs.

**Library** — project-level folders (`library/bibtex/`, `library/pdfs/`, `library/manifests/`) that accumulate across runs. BibTeX entries and PDF records are never stored only inside a dated run directory.

**Raw artifact** — a file preserved as evidence of what was obtained from a source. Do not edit raw files under `sources/*/raw/`.

**Derived artifact** — a file generated from raw artifacts and manifests, such as normalized CSV, merged candidates, BibTeX updates, validation reports, and missing-PDF reports. These may be regenerated.

## Process Checklist

For one literature-search iteration, the intended process is:

1. Define the query.
2. Run a dry run and inspect the translated query.
3. Run the live EG search.
4. Inspect validation and candidate outputs.
5. Work through `manual_action_queue.csv`.
6. For each missing PDF, use the canonical URL to assess access manually.
7. Register validly obtained PDFs one by one.
8. Refresh the run and regenerate the missing-PDF report.
9. Stop when validation is acceptable and the remaining manual actions are understood.

The current spike supports this end to end for the EG Digital Library only.

## End-to-End Workflow

### 1. Write your query

Queries use a small Boolean syntax: `AND`, `OR`, `NOT`, parentheses, bare terms, and quoted phrases. Single or double quotes are both accepted.

```text
('temporal' OR 'dynamic' OR 'animated') AND 'treemap'
```

Choose a short slug for the search, such as `temporal-treemap`. The slug becomes part of the dated run directory name.

### 2. Do a dry run first

A dry run creates the run directory and query files — including the translated query and the URL you would use to verify the search manually — without contacting any digital library.

```bash
python3 scripts/sls eg-search \
  --query "('temporal' OR 'dynamic' OR 'animated') AND 'treemap'" \
  --slug temporal-treemap \
  --dry-run
```

Open `searches/YYYY-MM-DD_temporal-treemap/query.md` to review the translated query and the manual verification URL before committing to a live run.

Also open `sources/eg/query_semantics.md`. For this spike, the query translator preserves Boolean operators and parentheses, and normalizes quoted terms for EG/DSpace search.

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

For a real survey run, omit `--max-results` unless you intentionally want a partial pilot. If `--max-results` is used, the validation report will show a count mismatch because EG may report more results than were imported.

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

Start with `validation_report.md`, then `merged_candidates.csv`, then `manual_action_queue.csv`.

### 5. Check the validation report

Open `validation_report.md`. If `source_reported_count` differs from `imported_count`, the report flags it. A mismatch expected when you used `--max-results`; otherwise it may indicate a pagination issue or an export cap to investigate.

### 6. Work through the manual action queue

Open `manual_action_queue.csv`. Each row is one action needed for one candidate. The `action` column indicates:

| Action | What to do |
|---|---|
| `missing_local_pdf` | Obtain the PDF through valid access and register it (see below) |
| `missing_bibtex` | BibTeX could not be generated — usually means a candidate is missing authors, year, or title |
| `review_duplicate` | Deduplication matched on title + year only (no DOI or URL available); confirm the merge is correct |

### 7. Obtain and register PDFs one by one

The pipeline never downloads PDFs. For each `missing_local_pdf` row:

1. Open the `canonical_url` from the row.
2. Decide manually whether you have valid access.
3. If you obtain the PDF, save it locally.
4. Register it with the matching `candidate_id`.
5. Refresh the run and continue with the next missing PDF.

Register a validly obtained PDF:

```bash
python3 scripts/sls pdfs add \
  --candidate-id firat2020treemapliteracyclassroom \
  --pdf ~/Downloads/paper.pdf
```

The file is copied to `library/pdfs/<candidate-id>.pdf`, checksummed, and recorded in `library/manifests/pdfs.csv`. Use `--no-copy` to reference a PDF in its current location without copying.

To register and immediately refresh the current run:

```bash
python3 scripts/sls pdfs add \
  --candidate-id firat2020treemapliteracyclassroom \
  --pdf ~/Downloads/paper.pdf \
  --refresh-run searches/2026-06-19_temporal-treemap
```

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

After refresh, reopen:

- `merged_candidates.csv` to confirm `has_local_pdf=yes` for the registered candidate.
- `missing_pdfs.csv` to confirm the candidate disappeared from the missing list.
- `library/manifests/pdfs.csv` to confirm checksum and file path were recorded.

### 8. Decide whether the run is ready for downstream analysis

A run is ready as input to downstream analysis when:

- `validation_report.md` has no unexplained count mismatch.
- `merged_candidates.csv` contains the expected candidate set.
- `manual_action_queue.csv` contains no unresolved issues you consider blocking.
- `library/bibtex/candidates.bib` contains the expected BibTeX entries.
- Missing local PDFs are either resolved or intentionally deferred.

It is acceptable for some PDFs to remain missing if access is unavailable or not yet assessed. The important part is that the missing state is explicit and traceable.

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

For the current spike, another researcher should use only the EG automated workflow for reproducible execution.

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

Generated `searches/` and `library/` files are ignored by Git in this spike. Share them deliberately when they are part of a study package; do not assume they are committed automatically.

## Troubleshooting

**The run directory already exists.**
Use a new slug/date, or pass `--overwrite-derived` when you intentionally want to regenerate derived files for the same run.

**`source_reported_count` and `imported_count` differ.**
This is expected if you used `--max-results`. Otherwise, inspect `sources/eg/source_manifest.json` and the raw pages under `sources/eg/raw/`.

**A candidate still appears in `missing_pdfs.csv` after registering a PDF.**
Run `python3 scripts/sls pdfs refresh <run-dir>`. If it still appears, check that `library/manifests/pdfs.csv` contains the same `candidate_id` as `merged_candidates.csv`.

**A PDF was registered by mistake.**
Delete the corresponding row from `library/manifests/pdfs.csv`, remove the PDF from `library/pdfs/` if it was copied there, and run `python3 scripts/sls pdfs refresh <run-dir>`.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```
