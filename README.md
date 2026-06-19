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
3. Run the live EG search, or prepare and import a manual ACM export.
4. Inspect validation and candidate outputs.
5. Work through `manual_action_queue.csv`.
6. For each missing PDF, use the canonical URL to assess access manually.
7. Register validly obtained PDFs one by one.
8. Refresh the run and regenerate the missing-PDF report.
9. Stop when validation is acceptable and the remaining manual actions are understood.

The current spike supports this end to end for the EG Digital Library, supports Springer Nature through its key-required metadata API, and supports ACM Digital Library through a human-executed BibTeX/RIS export.

## End-to-End Workflow

The EG workflow is fully automated for public metadata. The ACM workflow is intentionally human-assisted: the script prepares the query and run folder, the researcher executes the search/export in the ACM browser UI, and the script imports the resulting BibTeX or RIS file.

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

## ACM Manual Export Workflow

Use this workflow when the next source is ACM Digital Library. The script does not scrape ACM or automate browser interaction.

### 1. Prepare the ACM run

```bash
python3 scripts/sls acm-prepare \
  --query "('temporal' OR 'dynamic' OR 'animated') AND 'treemap'" \
  --slug temporal-treemap-acm
```

The command creates `searches/YYYY-MM-DD_temporal-treemap-acm/` and writes:

| File | What it contains |
|---|---|
| `query.md` | Generic query, ACM translated query, ACM browser URL, and manual execution notes |
| `run_config.json` | Query, prepared ACM URL, code version, and tool metadata |
| `sources/acm/query_semantics.md` | Translation notes and manual verification reminders |
| `sources/acm/source_manifest.json` | Status `manual_export_required` until an export is imported |
| `validation_report.md` | A pending-export placeholder |

### 2. Execute the search manually

Open the ACM URL from `query.md` in a normal browser session. Confirm the query and filters, record the visible result count, and export the result set as BibTeX or RIS using ACM's own export controls.

Keep the exported file unchanged. The import step will copy it into `sources/acm/raw/` as the immutable raw artifact.

### 3. Import the ACM export

```bash
python3 scripts/sls acm-import \
  --run-dir searches/YYYY-MM-DD_temporal-treemap-acm \
  --export ~/Downloads/acm-export.bib \
  --format auto \
  --reported-count 42 \
  --source-url "https://dl.acm.org/action/doSearch?..."
```

The import step parses BibTeX or RIS, writes `sources/acm/acm_results.csv`, regenerates `merged_candidates.csv`, updates `library/bibtex/candidates.bib`, reconciles known PDFs, and refreshes `manual_action_queue.csv` and `validation_report.md`.

If ACM's visible result count differs from the imported record count, the validation report flags the mismatch. This usually means an export cap, page-only export, filter mismatch, or a manual export mistake that should be documented or corrected.

## Springer Nature API Workflow

Use this workflow for SpringerLink/Springer Nature metadata discovery. The connector uses the Springer Nature metadata API and does not scrape SpringerLink HTML.

### 1. Configure the API key

Store the API key outside project files:

```bash
export SPRINGER_API_KEY="..."
```

The key is read from the environment, used only for the request, and redacted from run manifests and raw JSON artifacts.

### 2. Run or prepare the Springer search

```bash
python3 scripts/sls springer-search \
  --query "('temporal' OR 'dynamic' OR 'animated') AND 'treemap'" \
  --slug temporal-treemap-springer
```

The query translator maps unscoped generic terms to conservative Springer keyword constraints:

```text
(keyword:"temporal" OR keyword:"dynamic" OR keyword:"animated") AND keyword:"treemap"
```

If `SPRINGER_API_KEY` is not set, the command still creates a dated run directory and records source status `missing_credentials` in `sources/springer/source_manifest.json`. This makes the blocked state explicit without falling back to HTML parsing.

Useful options:

```bash
python3 scripts/sls springer-search \
  --query "..." \
  --slug temporal-treemap-springer \
  --max-results 25 \
  --page-size 25 \
  --api-key-env SPRINGER_API_KEY \
  --endpoint metadata
```

### 3. Review Springer outputs

When credentials are available, the command writes:

| File | What it contains |
|---|---|
| `query.md` | Generic query, Springer translated query, redacted API URL, and manual SpringerLink URL |
| `run_config.json` | Endpoint, query, page size, env-var name, code version, and tool metadata |
| `sources/springer/query_semantics.md` | Translation notes and API-key handling notes |
| `sources/springer/source_manifest.json` | Redacted API URLs, result counts, credential status, and connector status |
| `sources/springer/raw/page_NNNN.json` | Raw API responses with API-key fields redacted |
| `sources/springer/springer_results.csv` | Normalized Springer records before deduplication |
| `merged_candidates.csv` | Deduplicated candidate list |
| `validation_report.md` | Count comparison and missing-field summary |

Do not paste API keys into `query.md`, `run_config.json`, or command history that will be shared.

### 4. Manual SpringerLink export without an API key

If you do not have a Springer API key, use the human-executed workflow:

```bash
python3 scripts/sls springer-prepare \
  --query "('temporal' OR 'dynamic' OR 'animated') AND 'treemap'" \
  --slug temporal-treemap-springer
```

Open the SpringerLink URL in `query.md`, verify the query and filters, record the visible result count, and export citations/search results as CSV, BibTeX, or RIS if SpringerLink offers that for the result set.

Then import the unchanged export:

```bash
python3 scripts/sls springer-import \
  --run-dir searches/YYYY-MM-DD_temporal-treemap-springer \
  --export ~/Downloads/springer-export.csv \
  --format auto \
  --reported-count 42 \
  --source-url "https://link.springer.com/search?..."
```

The import step writes `sources/springer/springer_results.csv`, copies the raw export to `sources/springer/raw/`, regenerates `merged_candidates.csv`, updates `library/bibtex/candidates.bib`, and refreshes `manual_action_queue.csv` and `validation_report.md`.

Springer CSV exports are mapped from `Item Title`, `Publication Title`, `Book Series Title`, `Journal Volume`, `Journal Issue`, `Item DOI`, `Authors`, `Publication Year`, and `URL`. Springer may concatenate authors without separators; the importer applies a conservative splitting heuristic and preserves the resulting names for review.

SpringerLink CSV exports may be capped. If `validation_report.md` shows a source-reported count above the imported count, and exactly `1000` records were imported, treat the run as partial. Prefer the Springer API for complete pagination, or partition the browser search into smaller ranges such as publication years, content type, or other documented filters, then import each partition as a separate run.

## Currently Supported Sources

| Source | Mode | What is required |
|---|---|---|
| EG Digital Library (Eurographics) | Automated via DSpace REST API | Nothing — public API |
| ACM Digital Library | Manual BibTeX/RIS export ingest | Open in browser, search, export citation file, then run `acm-import` |
| Springer Nature / SpringerLink | API via Springer metadata endpoint, or manual CSV/BibTeX/RIS export ingest | Springer API key in `SPRINGER_API_KEY`, or browser export |
| IEEE Xplore | API (not yet implemented) | API key |
| ScienceDirect | API (not yet implemented) | Elsevier API key |
| Scopus | API (not yet implemented) | Elsevier API key |
| Web of Science | API (not yet implemented) | Clarivate API key and license |
| Google Scholar | Not automated | Manual export only; no official bulk API |

For sources that require a manual export: prepare the run first to get the translated query and source instructions, execute the search in your browser, export using the library's own export controls, and import the raw export with the matching source command when available.

For the current spike, another researcher can execute EG automatically, Springer automatically with a configured key, Springer through the manual-export workflow, or ACM through the manual-export workflow. Other sources remain documented requirements only.

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

For ACM imports, raw export files under `sources/acm/raw/` are also immutable. Re-importing the same run with a different raw export requires a new filename, so the run preserves what was actually supplied.

For Springer API runs, raw JSON pages under `sources/springer/raw/` are derived from the request and have API-key fields redacted before persistence. Use `--overwrite-derived` only when you intentionally want to regenerate derived artifacts for the same run.

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

**An ACM import reports a count mismatch.**
Compare `sources/acm/source_manifest.json`, the browser result count you recorded, and the exported file. Common causes are exporting only selected records, exporting only the current page, or changing filters between search and export.

**An ACM raw export already exists.**
Raw exports are immutable. Use a new export filename or create a new run directory rather than overwriting `sources/acm/raw/<filename>`.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```
