from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .bibtex import parse_bibtex_keys, record_to_bibtex
from .files import read_csv, write_csv, write_json, write_text
from .identity import assign_candidate_ids, dedupe_key, normalize_doi
from .pdfs import ensure_pdf_store, reconcile_candidate_pdfs
from .query import QueryTranslation, translate_for_eg


EG_SEARCH_URL = "https://diglib.eg.org/server/api/discover/search/objects"
EG_UI_SEARCH_URL = "https://diglib.eg.org/discover"
USER_AGENT = "structured-literature-surveys-spike/0.1 (+https://diglib.eg.org metadata workflow)"


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


@dataclass(frozen=True)
class SearchResult:
    run_dir: Path
    status: str
    source_reported_count: int
    imported_count: int
    deduplicated_count: int


def run_eg_search(
    *,
    generic_query: str,
    slug: str | None = None,
    run_date: str | None = None,
    max_results: int | None = None,
    page_size: int = 100,
    overwrite_derived: bool = False,
    dry_run: bool = False,
) -> SearchResult:
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    if max_results is not None and max_results <= 0:
        raise ValueError("max_results must be positive when provided")

    translation = translate_for_eg(generic_query)
    today = run_date or date.today().isoformat()
    run_slug = slugify(slug or generic_query, max_len=64)
    run_dir = Path("searches") / f"{today}_{run_slug}"
    eg_dir = run_dir / "sources" / "eg"
    raw_dir = eg_dir / "raw"
    derived_paths = [
        eg_dir / "eg_results.csv",
        eg_dir / "source_manifest.json",
        eg_dir / "query_semantics.md",
        run_dir / "merged_candidates.csv",
        run_dir / "validation_report.md",
        run_dir / "manual_action_queue.csv",
        run_dir / "run_config.json",
        run_dir / "bibtex_update.bib",
    ]

    prepare_directories(run_dir, raw_dir, overwrite_derived, derived_paths)
    ensure_library_dirs()
    write_query_files(run_dir, translation)

    run_config = build_run_config(
        translation=translation,
        page_size=page_size,
        max_results=max_results,
        dry_run=dry_run,
    )
    write_json(run_dir / "run_config.json", run_config)

    if dry_run:
        write_source_manifest(
            eg_dir / "source_manifest.json",
            translation,
            status="dry_run",
            request_urls=[],
            source_reported_count=0,
            imported_count=0,
            page_size=page_size,
            max_results=max_results,
        )
        write_text(run_dir / "validation_report.md", "# Validation Report\n\nDry run only; EG was not contacted.\n")
        return SearchResult(run_dir, "dry_run", 0, 0, 0)

    pages, request_urls, source_reported_count = fetch_eg_pages(
        translation.translated_query,
        raw_dir,
        page_size=page_size,
        max_results=max_results,
    )
    source_records = normalize_pages(pages)
    if max_results is not None:
        source_records = source_records[:max_results]
    imported_count = len(source_records)
    write_csv(eg_dir / "eg_results.csv", source_records, SOURCE_FIELDNAMES)

    candidates = deduplicate(source_records)
    assign_candidate_ids(candidates)
    reconcile_candidate_pdfs(candidates)
    bibtex_entries = update_bibtex(candidates, run_dir)
    for candidate in candidates:
        candidate["has_bibtex"] = "yes" if candidate.get("bibtex_key") in bibtex_entries else "no"

    write_csv(run_dir / "merged_candidates.csv", candidates, CANDIDATE_FIELDNAMES)
    write_manual_action_queue(run_dir / "manual_action_queue.csv", candidates)
    write_validation_report(
        run_dir / "validation_report.md",
        source_reported_count=source_reported_count,
        imported_count=imported_count,
        deduplicated_count=len(candidates),
        max_results=max_results,
        candidates=candidates,
    )
    write_source_manifest(
        eg_dir / "source_manifest.json",
        translation,
        status="ok",
        request_urls=request_urls,
        source_reported_count=source_reported_count,
        imported_count=imported_count,
        page_size=page_size,
        max_results=max_results,
    )

    return SearchResult(run_dir, "ok", source_reported_count, imported_count, len(candidates))


def prepare_directories(
    run_dir: Path,
    raw_dir: Path,
    overwrite_derived: bool,
    derived_paths: list[Path],
) -> None:
    if run_dir.exists() and not overwrite_derived:
        raise FileExistsError(
            f"{run_dir} already exists; pass --overwrite-derived to regenerate derived artifacts"
        )
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    if overwrite_derived:
        for path in derived_paths:
            if path.exists():
                path.unlink()
        # Raw exports are immutable by default, but EG API pages are derived from
        # the request in this spike. Keep the latest request pages together.
        if raw_dir.exists():
            for item in raw_dir.glob("page_*.json"):
                item.unlink()


def ensure_library_dirs() -> None:
    Path("library/bibtex").mkdir(parents=True, exist_ok=True)
    ensure_pdf_store()


def write_query_files(run_dir: Path, translation: QueryTranslation) -> None:
    query_md = f"""# Search Query

## Generic Query

```text
{translation.generic_query}
```

## EG Translated Query

```text
{translation.translated_query}
```

## Manual EG URL

{EG_UI_SEARCH_URL}?{urlencode({"query": translation.translated_query})}
"""
    write_text(run_dir / "query.md", query_md)

    semantics = ["# Query Semantics Report", ""]
    semantics.append(f"- Source: `{translation.source}`")
    semantics.append(f"- Generic query: `{translation.generic_query}`")
    semantics.append(f"- Translated query: `{translation.translated_query}`")
    semantics.append("")
    semantics.append("## Notes")
    semantics.extend(f"- {note}" for note in translation.semantics_notes)
    write_text(run_dir / "sources" / "eg" / "query_semantics.md", "\n".join(semantics) + "\n")


def build_run_config(
    *,
    translation: QueryTranslation,
    page_size: int,
    max_results: int | None,
    dry_run: bool,
) -> dict[str, object]:
    return {
        "created_at": now_iso(),
        "source": "eg",
        "enabled_sources": ["eg"],
        "disabled_sources": [],
        "generic_query": translation.generic_query,
        "translated_query": translation.translated_query,
        "page_size": page_size,
        "max_results": max_results,
        "dry_run": dry_run,
        "code_version": git_revision(),
        "tool": "scripts/sls eg-search",
    }


def fetch_eg_pages(
    query: str,
    raw_dir: Path,
    *,
    page_size: int,
    max_results: int | None,
) -> tuple[list[dict[str, object]], list[str], int]:
    pages: list[dict[str, object]] = []
    request_urls: list[str] = []
    total_elements = 0
    page = 0
    imported_so_far = 0

    while True:
        params = {"query": query, "page": page, "size": page_size}
        url = f"{EG_SEARCH_URL}?{urlencode(params)}"
        data = fetch_json(url)
        request_urls.append(url)
        write_json(raw_dir / f"page_{page:04d}.json", data)
        pages.append(data)

        page_info = (
            data.get("_embedded", {})
            .get("searchResult", {})
            .get("page", {})
        )
        total_elements = int(page_info.get("totalElements") or 0)
        current_size = len(extract_objects(data))
        imported_so_far += current_size

        if current_size == 0:
            break
        if max_results is not None and imported_so_far >= max_results:
            break
        if page >= int(page_info.get("totalPages") or 1) - 1:
            break
        page += 1

    return pages, request_urls, total_elements


def fetch_json(url: str) -> dict[str, object]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_objects(page: dict[str, object]) -> list[dict[str, object]]:
    return (
        page.get("_embedded", {})
        .get("searchResult", {})
        .get("_embedded", {})
        .get("objects", [])
    )


def normalize_pages(pages: list[dict[str, object]]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for page in pages:
        for obj in extract_objects(page):
            item = obj.get("_embedded", {}).get("indexableObject", {})
            record = normalize_item(item)
            if record:
                records.append(record)
    return records


def normalize_item(item: dict[str, object]) -> dict[str, str]:
    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        return {}
    uuid = str(item.get("uuid") or item.get("id") or "")
    self_url = item.get("_links", {}).get("self", {}).get("href", "")

    doi = normalize_doi(first_meta(metadata, "dc.identifier.doi"))
    canonical_url = canonical_url_for(metadata, str(item.get("handle") or ""), doi)

    return {
        "source": "eg",
        "source_record_id": uuid,
        "title": first_meta(metadata, "dc.title") or str(item.get("name") or ""),
        "authors": "; ".join(values_meta(metadata, "dc.contributor.author")),
        "year": year_from(first_meta(metadata, "dc.date.issued")),
        "doi": doi,
        "canonical_url": canonical_url,
        "venue": first_meta(metadata, "dc.description.seriesinformation"),
        "publisher": first_meta(metadata, "dc.publisher"),
        "pages": first_meta(metadata, "dc.identifier.pages"),
        "volume": first_meta(metadata, "dc.description.volume"),
        "number": first_meta(metadata, "dc.description.number"),
        "source_api_url": self_url,
    }


def first_meta(metadata: dict[str, object], key: str) -> str:
    values = values_meta(metadata, key)
    return values[0] if values else ""


def values_meta(metadata: dict[str, object], key: str) -> list[str]:
    raw = metadata.get(key) or []
    values = []
    if isinstance(raw, list):
        for entry in sorted(raw, key=lambda item: item.get("place", 0) if isinstance(item, dict) else 0):
            if isinstance(entry, dict) and entry.get("value"):
                values.append(str(entry["value"]).strip())
    return [value for value in values if value]


def canonical_url_for(metadata: dict[str, object], handle: str, doi: str) -> str:
    uris = values_meta(metadata, "dc.identifier.uri")
    for uri in uris:
        if "diglib.eg.org" in uri:
            return uri.replace("https://diglib.eg.org:443/", "https://diglib.eg.org/")
    if handle:
        return f"https://diglib.eg.org/handle/{handle}"
    if doi:
        return f"https://doi.org/{doi}"
    return uris[0] if uris else ""


def year_from(value: str) -> str:
    match = re.search(r"(\d{4})", value or "")
    return match.group(1) if match else ""


def deduplicate(records: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for record in records:
        grouped.setdefault(dedupe_key(record), []).append(record)

    candidates: list[dict[str, str]] = []
    for key in sorted(grouped):
        group = grouped[key]
        primary = choose_primary(group)
        candidate = {name: primary.get(name, "") for name in SOURCE_FIELDNAMES if name in primary}
        candidate.update(
            {
                "source_count": str(len(group)),
                "sources": ";".join(sorted({r["source"] for r in group})),
                "source_record_ids": ";".join(sorted({r["source_record_id"] for r in group if r["source_record_id"]})),
                "dedupe_key": key,
                "dedupe_confidence": confidence_for_key(key),
                "has_local_pdf": "no",
                "pdf_path": "",
                "has_bibtex": "no",
                "manual_review": "yes" if confidence_for_key(key) == "title_year" else "no",
            }
        )
        candidates.append(candidate)
    return candidates


def choose_primary(group: list[dict[str, str]]) -> dict[str, str]:
    return sorted(
        group,
        key=lambda record: (
            0 if record.get("doi") else 1,
            0 if record.get("canonical_url") else 1,
            record.get("title", ""),
        ),
    )[0]


def confidence_for_key(key: str) -> str:
    if key.startswith("doi:"):
        return "doi"
    if key.startswith("url:"):
        return "canonical_url"
    return "title_year"


def update_bibtex(candidates: list[dict[str, str]], run_dir: Path) -> set[str]:
    bib_path = Path("library/bibtex/candidates.bib")
    existing = bib_path.read_text(encoding="utf-8") if bib_path.exists() else ""
    existing_keys = parse_bibtex_keys(existing)

    run_entries = [record_to_bibtex(candidate) for candidate in candidates]
    write_text(run_dir / "bibtex_update.bib", "\n\n".join(run_entries) + "\n")

    new_entries = [
        record_to_bibtex(candidate)
        for candidate in candidates
        if candidate["bibtex_key"] not in existing_keys
    ]
    if new_entries:
        prefix = existing.rstrip() + "\n\n" if existing.strip() else ""
        write_text(bib_path, prefix + "\n\n".join(new_entries) + "\n")

    return parse_bibtex_keys(bib_path.read_text(encoding="utf-8")) if bib_path.exists() else set()


def write_manual_action_queue(path: Path, candidates: list[dict[str, str]]) -> None:
    rows: list[dict[str, str]] = []
    for candidate in candidates:
        if candidate.get("has_local_pdf") != "yes":
            rows.append(action_row(candidate, "missing_local_pdf", "No local PDF is mapped in library/manifests/pdfs.csv."))
        if candidate.get("has_bibtex") != "yes":
            rows.append(action_row(candidate, "missing_bibtex", "No consolidated BibTeX entry is available."))
        if candidate.get("manual_review") == "yes":
            rows.append(action_row(candidate, "review_duplicate", "Deduplication used title/year evidence."))
    write_csv(path, rows, ACTION_FIELDNAMES)


def action_row(candidate: dict[str, str], action: str, reason: str) -> dict[str, str]:
    return {
        "candidate_id": candidate.get("candidate_id", ""),
        "action": action,
        "reason": reason,
        "title": candidate.get("title", ""),
        "authors": candidate.get("authors", ""),
        "year": candidate.get("year", ""),
        "doi": candidate.get("doi", ""),
        "canonical_url": candidate.get("canonical_url", ""),
    }


def write_validation_report(
    path: Path,
    *,
    source_reported_count: int,
    imported_count: int,
    deduplicated_count: int,
    max_results: int | None,
    candidates: list[dict[str, str]],
) -> None:
    lines = [
        "# Validation Report",
        "",
        f"- Source-reported result count: {source_reported_count}",
        f"- Imported record count: {imported_count}",
        f"- Deduplicated candidate count: {deduplicated_count}",
    ]
    if max_results is not None:
        lines.append(f"- Spike max-results cap: {max_results}")
    if source_reported_count != imported_count:
        severity = "expected" if max_results is not None and imported_count <= max_results else "warning"
        lines.append(f"- Count mismatch: {severity}")
    missing = {
        "title": sum(1 for c in candidates if not c.get("title")),
        "authors": sum(1 for c in candidates if not c.get("authors")),
        "year": sum(1 for c in candidates if not c.get("year")),
        "doi": sum(1 for c in candidates if not c.get("doi")),
        "canonical_url": sum(1 for c in candidates if not c.get("canonical_url")),
        "local_pdf": sum(1 for c in candidates if c.get("has_local_pdf") != "yes"),
    }
    lines.append("")
    lines.append("## Missing Field Counts")
    lines.extend(f"- {field}: {count}" for field, count in missing.items())
    write_text(path, "\n".join(lines) + "\n")


def write_source_manifest(
    path: Path,
    translation: QueryTranslation,
    *,
    status: str,
    request_urls: list[str],
    source_reported_count: int,
    imported_count: int,
    page_size: int,
    max_results: int | None,
) -> None:
    write_json(
        path,
        {
            "source": "eg",
            "status": status,
            "retrieval_timestamp": now_iso(),
            "generic_query": translation.generic_query,
            "translated_query": translation.translated_query,
            "request_urls": request_urls,
            "source_reported_result_count": source_reported_count,
            "imported_record_count": imported_count,
            "page_size": page_size,
            "max_results": max_results,
            "query_semantics_notes": translation.semantics_notes,
        },
    )


def slugify(value: str, max_len: int = 64) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    if len(value) > max_len:
        value = value[:max_len].strip("-")
    return value or "search"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"

