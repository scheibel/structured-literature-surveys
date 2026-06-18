# Structured Literature Surveys

Spike implementation for a structured literature-search workflow.

The current spike targets only the EG Digital Library / Eurographics Digital Library. It creates dated search-run directories, preserves raw EG API pages, normalizes metadata to CSV, deduplicates candidates, generates BibTeX, updates project-level literature assets, and reports follow-up actions.

## Usage

```bash
python3 scripts/sls eg-search \
  --query "('temporal' OR 'dynamic' OR 'animated') AND 'treemap'" \
  --slug temporal-treemap \
  --max-results 25
```

Outputs are written to:

- `searches/YYYY-MM-DD_slug/` for run-specific artifacts.
- `library/bibtex/candidates.bib` for the durable consolidated BibTeX database.
- `library/pdfs/` for PDFs that the researcher obtains and places manually.
- `library/manifests/pdfs.csv` for PDF-to-candidate mappings.

The script does not download PDFs.

## PDF Follow-Up Workflow

After a search run, list candidates that still need a local PDF:

```bash
python3 scripts/sls pdfs missing \
  --run-dir searches/2026-06-19_temporal-treemap \
  --output searches/2026-06-19_temporal-treemap/missing_pdfs.csv
```

The report includes candidate metadata and the canonical publisher URL. The researcher can use that URL to assess access and obtain the PDF through valid routes.

After placing or downloading a PDF through valid access, register it one candidate at a time:

```bash
python3 scripts/sls pdfs add \
  --candidate-id firat2020treemapliteracyclassroom \
  --pdf ~/Downloads/paper.pdf \
  --refresh-run searches/2026-06-19_temporal-treemap
```

By default, the file is copied to `library/pdfs/<candidate-id>.pdf`, checksummed, and recorded in `library/manifests/pdfs.csv`. Use `--no-copy` to reference a PDF in place.

To refresh an existing run after manually placing files in `library/pdfs/` with candidate-id filenames:

```bash
python3 scripts/sls pdfs refresh searches/2026-06-19_temporal-treemap
```

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```
