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

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```
