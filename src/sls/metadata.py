"""Shared bibliographic CSV schemas for search and snowballing."""

SOURCE_FIELDNAMES = [
    "source",
    "source_record_id",
    "title",
    "authors",
    "year",
    "doi",
    "canonical_url",
    "venue",
    "publisher",
    "pages",
    "volume",
    "number",
    "source_api_url",
]

CANDIDATE_FIELDNAMES = [
    "candidate_id",
    "bibtex_key",
    "title",
    "authors",
    "year",
    "doi",
    "canonical_url",
    "venue",
    "publisher",
    "pages",
    "volume",
    "number",
    "source_count",
    "sources",
    "source_record_ids",
    "dedupe_key",
    "dedupe_confidence",
    "has_local_pdf",
    "pdf_path",
    "has_bibtex",
    "manual_review",
]

ACTION_FIELDNAMES = [
    "candidate_id",
    "action",
    "reason",
    "title",
    "authors",
    "year",
    "doi",
    "canonical_url",
]


