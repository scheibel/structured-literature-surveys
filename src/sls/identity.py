from __future__ import annotations

import hashlib
import re
import unicodedata


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "based",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "towards",
    "using",
    "with",
}


def ascii_slug(value: str, *, max_len: int | None = None) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    if max_len is not None:
        value = value[:max_len].strip("-")
    return value or "unknown"


def normalize_doi(doi: str) -> str:
    doi = doi.strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.I)
    return doi.lower()


def title_fingerprint(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", ascii_slug(title))


def dedupe_key(record: dict[str, str]) -> str:
    doi = normalize_doi(record.get("doi", ""))
    if doi:
        return f"doi:{doi}"
    canonical_url = record.get("canonical_url", "").strip().lower()
    if canonical_url:
        return f"url:{canonical_url}"
    return f"title-year:{title_fingerprint(record.get('title', ''))}:{record.get('year', '').strip()}"


def first_author_slug(authors: str) -> str:
    first = authors.split(";")[0].strip()
    if "," in first:
        first = first.split(",", 1)[0]
    else:
        first = first.split()[-1] if first.split() else first
    return ascii_slug(first, max_len=32)


def short_title_slug(title: str, words: int = 3) -> str:
    parts = [p for p in ascii_slug(title).split("-") if p and p not in STOPWORDS]
    if not parts:
        parts = [p for p in ascii_slug(title).split("-") if p]
    return "".join(parts[:words]) or "untitled"


def fallback_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]


def candidate_id_base(record: dict[str, str]) -> str:
    authors = record.get("authors", "")
    year = re.sub(r"\D", "", record.get("year", ""))[:4]
    title = record.get("title", "")
    if authors and year and title:
        return f"{first_author_slug(authors)}{year}{short_title_slug(title)}"

    doi = normalize_doi(record.get("doi", ""))
    if doi:
        return f"doi{ascii_slug(doi, max_len=48).replace('-', '')}"

    url = record.get("canonical_url", "")
    if url:
        return f"url{fallback_hash(url)}"

    source_id = record.get("source_record_id", "") or repr(sorted(record.items()))
    return f"record{fallback_hash(source_id)}"


def assign_candidate_ids(records: list[dict[str, str]]) -> None:
    counts: dict[str, int] = {}
    for record in records:
        base = candidate_id_base(record)
        count = counts.get(base, 0)
        counts[base] = count + 1
        candidate_id = base if count == 0 else f"{base}{count + 1}"
        record["candidate_id"] = candidate_id
        record["bibtex_key"] = candidate_id

