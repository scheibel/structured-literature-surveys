from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
import copy
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .bibtex import parse_bibtex_records
from .eg import (
    CANDIDATE_FIELDNAMES,
    SOURCE_FIELDNAMES,
    SearchResult,
    deduplicate,
    ensure_library_dirs,
    git_revision,
    now_iso,
    prepare_directories,
    slugify,
    update_bibtex,
    write_manual_action_queue,
    write_validation_report,
)
from .files import read_csv, write_csv, write_json, write_text
from .identity import assign_candidate_ids, normalize_doi
from .pdfs import reconcile_candidate_pdfs
from .query import QueryTranslation, translate_for_springer


SPRINGER_ENDPOINTS = {
    "metadata": "https://api.springernature.com/metadata/json",
    "meta-v2": "https://api.springernature.com/meta/v2/json",
}
SPRINGER_LINK_SEARCH_URL = "https://link.springer.com/search"
USER_AGENT = "structured-literature-surveys-spike/0.1 (+https://api.springernature.com metadata workflow)"


@dataclass(frozen=True)
class SpringerFetchResult:
    pages: list[dict[str, object]]
    request_urls: list[str]
    source_reported_count: int


@dataclass(frozen=True)
class SpringerImportResult:
    run_dir: Path
    status: str
    source_reported_count: int
    imported_count: int
    deduplicated_count: int
    raw_export_path: Path


def run_springer_search(
    *,
    generic_query: str,
    slug: str | None = None,
    run_date: str | None = None,
    max_results: int | None = None,
    page_size: int = 100,
    overwrite_derived: bool = False,
    dry_run: bool = False,
    api_key_env: str = "SPRINGER_API_KEY",
    endpoint: str = "metadata",
) -> SearchResult:
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    if max_results is not None and max_results <= 0:
        raise ValueError("max_results must be positive when provided")
    if endpoint not in SPRINGER_ENDPOINTS:
        raise ValueError(f"unknown Springer endpoint {endpoint!r}")

    translation = translate_for_springer(generic_query)
    today = run_date or date.today().isoformat()
    run_slug = slugify(slug or generic_query, max_len=64)
    run_dir = Path("searches") / f"{today}_{run_slug}"
    springer_dir = run_dir / "sources" / "springer"
    raw_dir = springer_dir / "raw"
    derived_paths = [
        springer_dir / "springer_results.csv",
        springer_dir / "source_manifest.json",
        springer_dir / "query_semantics.md",
        run_dir / "merged_candidates.csv",
        run_dir / "validation_report.md",
        run_dir / "manual_action_queue.csv",
        run_dir / "run_config.json",
        run_dir / "bibtex_update.bib",
    ]

    prepare_directories(run_dir, raw_dir, overwrite_derived, derived_paths)
    ensure_library_dirs()
    write_springer_query_files(run_dir, translation, endpoint)
    write_json(
        run_dir / "run_config.json",
        build_springer_run_config(
            translation=translation,
            page_size=page_size,
            max_results=max_results,
            dry_run=dry_run,
            api_key_env=api_key_env,
            endpoint=endpoint,
        ),
    )

    if dry_run:
        write_springer_source_manifest(
            springer_dir / "source_manifest.json",
            translation,
            status="dry_run",
            request_urls=[],
            source_reported_count=0,
            imported_count=0,
            page_size=page_size,
            max_results=max_results,
            api_key_env=api_key_env,
            endpoint=endpoint,
        )
        write_text(run_dir / "validation_report.md", "# Validation Report\n\nDry run only; Springer was not contacted.\n")
        return SearchResult(run_dir, "dry_run", 0, 0, 0)

    api_key = os.environ.get(api_key_env, "").strip()
    if not api_key:
        write_springer_source_manifest(
            springer_dir / "source_manifest.json",
            translation,
            status="missing_credentials",
            request_urls=[],
            source_reported_count=0,
            imported_count=0,
            page_size=page_size,
            max_results=max_results,
            api_key_env=api_key_env,
            endpoint=endpoint,
        )
        write_text(
            run_dir / "validation_report.md",
            f"# Validation Report\n\nSpringer was not contacted because `{api_key_env}` is not set.\n",
        )
        return SearchResult(run_dir, "missing_credentials", 0, 0, 0)

    fetched = fetch_springer_pages(
        translation.translated_query,
        raw_dir,
        api_key=api_key,
        endpoint=endpoint,
        page_size=page_size,
        max_results=max_results,
    )
    source_records = normalize_pages(fetched.pages)
    if max_results is not None:
        source_records = source_records[:max_results]
    imported_count = len(source_records)
    write_csv(springer_dir / "springer_results.csv", source_records, SOURCE_FIELDNAMES)

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
        source_reported_count=fetched.source_reported_count,
        imported_count=imported_count,
        deduplicated_count=len(candidates),
        max_results=max_results,
        candidates=candidates,
    )
    write_springer_source_manifest(
        springer_dir / "source_manifest.json",
        translation,
        status="ok",
        request_urls=fetched.request_urls,
        source_reported_count=fetched.source_reported_count,
        imported_count=imported_count,
        page_size=page_size,
        max_results=max_results,
        api_key_env=api_key_env,
        endpoint=endpoint,
    )

    return SearchResult(run_dir, "ok", fetched.source_reported_count, imported_count, len(candidates))


def prepare_springer_manual_search(
    *,
    generic_query: str,
    slug: str | None = None,
    run_date: str | None = None,
    overwrite_derived: bool = False,
    endpoint: str = "metadata",
) -> SearchResult:
    if endpoint not in SPRINGER_ENDPOINTS:
        raise ValueError(f"unknown Springer endpoint {endpoint!r}")

    translation = translate_for_springer(generic_query)
    today = run_date or date.today().isoformat()
    run_slug = slugify(slug or generic_query, max_len=64)
    run_dir = Path("searches") / f"{today}_{run_slug}"
    springer_dir = run_dir / "sources" / "springer"
    raw_dir = springer_dir / "raw"
    derived_paths = [
        springer_dir / "springer_results.csv",
        springer_dir / "source_manifest.json",
        springer_dir / "query_semantics.md",
        run_dir / "merged_candidates.csv",
        run_dir / "validation_report.md",
        run_dir / "manual_action_queue.csv",
        run_dir / "run_config.json",
        run_dir / "bibtex_update.bib",
        run_dir / "query.md",
    ]

    prepare_directories(run_dir, raw_dir, overwrite_derived, derived_paths)
    ensure_library_dirs()
    write_springer_query_files(run_dir, translation, endpoint)
    run_config = build_springer_run_config(
        translation=translation,
        page_size=100,
        max_results=None,
        dry_run=False,
        api_key_env="",
        endpoint=endpoint,
    )
    run_config["tool"] = "scripts/sls springer-prepare"
    run_config["manual_export"] = True
    write_json(run_dir / "run_config.json", run_config)
    write_springer_source_manifest(
        springer_dir / "source_manifest.json",
        translation,
        status="manual_export_required",
        request_urls=[],
        source_reported_count=0,
        imported_count=0,
        page_size=100,
        max_results=None,
        api_key_env="",
        endpoint=endpoint,
    )
    write_text(
        run_dir / "validation_report.md",
        "# Validation Report\n\nSpringer manual export is pending; no records have been imported yet.\n",
    )
    return SearchResult(run_dir, "manual_export_required", 0, 0, 0)


def import_springer_export(
    *,
    run_dir: Path,
    export_path: Path,
    export_format: str = "auto",
    reported_count: int | None = None,
    source_url: str = "",
    notes: str = "",
) -> SpringerImportResult:
    run_dir = run_dir.expanduser()
    export_path = export_path.expanduser()
    if not run_dir.exists():
        raise FileNotFoundError(run_dir)
    if not export_path.exists():
        raise FileNotFoundError(export_path)

    ensure_library_dirs()
    springer_dir = run_dir / "sources" / "springer"
    raw_dir = springer_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_export = copy_raw_export(export_path, raw_dir)

    parsed_format = detect_export_format(raw_export, export_format)
    records = parse_springer_export(raw_export, parsed_format)
    write_csv(springer_dir / "springer_results.csv", records, SOURCE_FIELDNAMES)

    source_records = collect_run_source_records(run_dir)
    candidates = deduplicate(source_records)
    assign_candidate_ids(candidates)
    reconcile_candidate_pdfs(candidates)
    bibtex_entries = update_bibtex(candidates, run_dir)
    for candidate in candidates:
        candidate["has_bibtex"] = "yes" if candidate.get("bibtex_key") in bibtex_entries else "no"

    write_csv(run_dir / "merged_candidates.csv", candidates, CANDIDATE_FIELDNAMES)
    write_manual_action_queue(run_dir / "manual_action_queue.csv", candidates)
    source_reported_count = reported_count if reported_count is not None else len(records)
    write_validation_report(
        run_dir / "validation_report.md",
        source_reported_count=source_reported_count,
        imported_count=len(records),
        deduplicated_count=len(candidates),
        max_results=None,
        candidates=candidates,
    )

    translation, endpoint = translation_from_run_config(run_dir)
    write_springer_source_manifest(
        springer_dir / "source_manifest.json",
        translation,
        status="ok",
        request_urls=[source_url] if source_url else [],
        source_reported_count=source_reported_count,
        imported_count=len(records),
        page_size=0,
        max_results=None,
        api_key_env="",
        endpoint=endpoint,
    )
    append_manual_import_notes(springer_dir / "source_manifest.json", parsed_format, str(raw_export), source_url, notes)

    return SpringerImportResult(
        run_dir=run_dir,
        status="ok",
        source_reported_count=source_reported_count,
        imported_count=len(records),
        deduplicated_count=len(candidates),
        raw_export_path=raw_export,
    )


def write_springer_query_files(run_dir: Path, translation: QueryTranslation, endpoint: str) -> None:
    api_url = springer_request_url(
        translation.translated_query,
        api_key="<redacted>",
        endpoint=endpoint,
        start=1,
        page_size=100,
    )
    manual_url = f"{SPRINGER_LINK_SEARCH_URL}?{urlencode({'query': translation.generic_query})}"
    query_md = f"""# Search Query

## Generic Query

```text
{translation.generic_query}
```

## Springer Translated Query

```text
{translation.translated_query}
```

## Redacted Springer API URL

{api_url}

## Manual SpringerLink URL

{manual_url}

## Manual Execution Notes

1. Open the SpringerLink URL in a normal browser session.
2. Verify the query and filters in the browser UI.
3. Record the visible SpringerLink result count.
4. Export citation/search results as CSV, BibTeX, or RIS if SpringerLink offers that for the result set.
5. Import the raw export with `python3 scripts/sls springer-import --run-dir {run_dir} --export <file> --reported-count <count>`.
"""
    write_text(run_dir / "query.md", query_md)

    semantics = ["# Query Semantics Report", ""]
    semantics.append(f"- Source: `{translation.source}`")
    semantics.append(f"- Generic query: `{translation.generic_query}`")
    semantics.append(f"- Translated query: `{translation.translated_query}`")
    semantics.append("")
    semantics.append("## Notes")
    semantics.extend(f"- {note}" for note in translation.semantics_notes)
    write_text(run_dir / "sources" / "springer" / "query_semantics.md", "\n".join(semantics) + "\n")


def build_springer_run_config(
    *,
    translation: QueryTranslation,
    page_size: int,
    max_results: int | None,
    dry_run: bool,
    api_key_env: str,
    endpoint: str,
) -> dict[str, object]:
    return {
        "created_at": now_iso(),
        "source": "springer",
        "enabled_sources": ["springer"],
        "disabled_sources": [],
        "generic_query": translation.generic_query,
        "translated_query": translation.translated_query,
        "endpoint": endpoint,
        "endpoint_url": SPRINGER_ENDPOINTS[endpoint],
        "api_key_env": api_key_env,
        "page_size": page_size,
        "max_results": max_results,
        "dry_run": dry_run,
        "code_version": git_revision(),
        "tool": "scripts/sls springer-search",
    }


def fetch_springer_pages(
    query: str,
    raw_dir: Path,
    *,
    api_key: str,
    endpoint: str,
    page_size: int,
    max_results: int | None,
) -> SpringerFetchResult:
    pages: list[dict[str, object]] = []
    request_urls: list[str] = []
    total = 0
    start = 1
    imported_so_far = 0

    while True:
        url = springer_request_url(query, api_key=api_key, endpoint=endpoint, start=start, page_size=page_size)
        redacted_url = springer_request_url(
            query,
            api_key="<redacted>",
            endpoint=endpoint,
            start=start,
            page_size=page_size,
        )
        data = sanitize_springer_response(fetch_springer_json(url))
        request_urls.append(redacted_url)
        write_json(raw_dir / f"page_{len(pages):04d}.json", data)
        pages.append(data)

        records = data.get("records", [])
        current_size = len(records) if isinstance(records, list) else 0
        total = source_reported_count(data)
        imported_so_far += current_size

        if current_size == 0:
            break
        if max_results is not None and imported_so_far >= max_results:
            break
        if total and start + current_size > total:
            break
        start += current_size

    return SpringerFetchResult(pages, request_urls, total)


def springer_request_url(
    query: str,
    *,
    api_key: str,
    endpoint: str,
    start: int,
    page_size: int,
) -> str:
    return f"{SPRINGER_ENDPOINTS[endpoint]}?{urlencode({'api_key': api_key, 'q': query, 's': start, 'p': page_size})}"


def fetch_springer_json(url: str) -> dict[str, object]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def copy_raw_export(export_path: Path, raw_dir: Path) -> Path:
    target = raw_dir / export_path.name
    if target.exists():
        if export_path.resolve() == target.resolve():
            return target
        raise FileExistsError(
            f"raw export {target} already exists; keep raw exports immutable or use a new filename"
        )
    shutil.copy2(export_path, target)
    return target


def detect_export_format(path: Path, export_format: str) -> str:
    if export_format != "auto":
        normalized = export_format.lower()
    else:
        suffix = path.suffix.lower()
        if suffix in {".bib", ".bibtex"}:
            normalized = "bibtex"
        elif suffix == ".ris":
            normalized = "ris"
        elif suffix == ".csv":
            normalized = "csv"
        else:
            head = path.read_text(encoding="utf-8", errors="replace")[:200].lstrip()
            if head.startswith("@"):
                normalized = "bibtex"
            elif "Item Title" in head and "Item DOI" in head:
                normalized = "csv"
            else:
                normalized = "ris"
    if normalized not in {"bibtex", "ris", "csv"}:
        raise ValueError("export format must be bibtex, ris, csv, or auto")
    return normalized


def parse_springer_export(path: Path, export_format: str) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if export_format == "bibtex":
        return normalize_manual_records(parse_bibtex_records(text, source="springer"))
    if export_format == "ris":
        return parse_ris_records(text)
    if export_format == "csv":
        return parse_csv_records(text)
    raise ValueError(f"unsupported Springer export format: {export_format}")


def parse_csv_records(text: str) -> list[dict[str, str]]:
    rows = csv.DictReader(text.splitlines())
    records: list[dict[str, str]] = []
    for row in rows:
        clean = {str(key or "").strip().lstrip("\ufeff"): str(value or "").strip() for key, value in row.items()}
        doi = normalize_doi(clean.get("Item DOI", ""))
        title = clean.get("Item Title", "")
        canonical_url = clean.get("URL", "") or (f"https://doi.org/{doi}" if doi else "")
        venue = clean.get("Publication Title", "") or clean.get("Book Series Title", "")
        record = {
            "source": "springer",
            "source_record_id": doi or canonical_url or title,
            "title": title,
            "authors": "; ".join(split_springer_csv_authors(clean.get("Authors", ""))),
            "year": year_from(clean.get("Publication Year", "")),
            "doi": doi,
            "canonical_url": canonical_url,
            "venue": venue,
            "publisher": "",
            "pages": "",
            "volume": clean.get("Journal Volume", ""),
            "number": clean.get("Journal Issue", ""),
            "source_api_url": "",
        }
        if record["title"] or record["doi"] or record["canonical_url"]:
            records.append(record)
    return records


def split_springer_csv_authors(value: str) -> list[str]:
    value = " ".join(value.split())
    if not value:
        return []
    if ";" in value:
        return [part.strip() for part in value.split(";") if part.strip()]
    if "|" in value:
        return [part.strip() for part in value.split("|") if part.strip()]

    names: list[str] = []
    start = 0
    for index in range(1, len(value)):
        prev = value[index - 1]
        current = value[index]
        if current.isupper() and (prev.islower() or prev in ")’"):
            candidate = value[start:index].strip()
            if " " in candidate:
                names.append(candidate)
                start = index
    names.append(value[start:].strip())
    return [name for name in names if name]


def normalize_manual_records(records: list[dict[str, str]]) -> list[dict[str, str]]:
    for record in records:
        doi = normalize_doi(record.get("doi", ""))
        record["source"] = "springer"
        record["doi"] = doi
        if not record.get("canonical_url") and doi:
            record["canonical_url"] = f"https://doi.org/{doi}"
        record["source_record_id"] = doi or record.get("source_record_id", "")
    return records


def parse_ris_records(text: str) -> list[dict[str, str]]:
    records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    last_tag = ""
    for line in text.splitlines():
        match = re.match(r"^([A-Z0-9]{2})\s+-\s?(.*)$", line)
        if match:
            tag = match.group(1)
            value = match.group(2).strip()
            if tag == "ER":
                if current:
                    records.append(current)
                current = {}
                last_tag = ""
                continue
            current.setdefault(tag, []).append(value)
            last_tag = tag
        elif last_tag and line.strip():
            current[last_tag][-1] = f"{current[last_tag][-1]} {line.strip()}"
    if current:
        records.append(current)

    return [ris_fields_to_record(fields) for fields in records]


def ris_fields_to_record(fields: dict[str, list[str]]) -> dict[str, str]:
    doi = normalize_doi(first_ris(fields, "DO"))
    url = first_ris(fields, "UR")
    title = first_ris(fields, "TI", "T1", "CT")
    return {
        "source": "springer",
        "source_record_id": doi or url or title,
        "title": title,
        "authors": "; ".join(fields.get("AU", []) + fields.get("A1", [])),
        "year": year_from(first_ris(fields, "PY", "Y1", "DA")),
        "doi": doi,
        "canonical_url": url or (f"https://doi.org/{doi}" if doi else ""),
        "venue": first_ris(fields, "JF", "JO", "T2", "BT"),
        "publisher": first_ris(fields, "PB"),
        "pages": pages_from_ris(fields),
        "volume": first_ris(fields, "VL"),
        "number": first_ris(fields, "IS"),
        "source_api_url": "",
    }


def first_ris(fields: dict[str, list[str]], *tags: str) -> str:
    for tag in tags:
        values = [value for value in fields.get(tag, []) if value]
        if values:
            return values[0]
    return ""


def pages_from_ris(fields: dict[str, list[str]]) -> str:
    start = first_ris(fields, "SP")
    end = first_ris(fields, "EP")
    if start and end:
        return f"{start}-{end}"
    return start or first_ris(fields, "PG")


def collect_run_source_records(run_dir: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for path in sorted(run_dir.glob("sources/*/*_results.csv")):
        records.extend(read_csv(path))
    return records


def translation_from_run_config(run_dir: Path) -> tuple[QueryTranslation, str]:
    config_path = run_dir / "run_config.json"
    if not config_path.exists():
        return (
            QueryTranslation(
                generic_query="",
                source="springer",
                translated_query="",
                semantics_notes=["No run_config.json was available during manual export import."],
            ),
            "metadata",
        )

    config = json.loads(config_path.read_text(encoding="utf-8"))
    generic_query = str(config.get("generic_query") or "")
    translated_query = str(config.get("translated_query") or generic_query)
    endpoint = str(config.get("endpoint") or "metadata")
    if endpoint not in SPRINGER_ENDPOINTS:
        endpoint = "metadata"
    notes = [
        "Loaded Springer translation from run_config.json during manual export import.",
        "The researcher is responsible for verifying browser-side query semantics and exported result counts.",
    ]
    return (
        QueryTranslation(
            generic_query=generic_query,
            source="springer",
            translated_query=translated_query,
            semantics_notes=notes,
        ),
        endpoint,
    )


def append_manual_import_notes(
    manifest_path: Path,
    export_format: str,
    raw_export_path: str,
    source_url: str,
    notes: str,
) -> None:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data.update(
        {
            "manual_export": True,
            "export_format": export_format,
            "raw_export_path": raw_export_path,
            "source_url": source_url,
            "notes": notes,
        }
    )
    write_json(manifest_path, data)


def sanitize_springer_response(data: dict[str, object]) -> dict[str, object]:
    sanitized = copy.deepcopy(data)
    for key in ("apiKey", "api_key"):
        if key in sanitized:
            sanitized[key] = "<redacted>"
    return sanitized


def source_reported_count(page: dict[str, object]) -> int:
    result = page.get("result", [])
    if isinstance(result, list) and result:
        total = result[0].get("total") if isinstance(result[0], dict) else None
        if total is not None:
            try:
                return int(str(total))
            except ValueError:
                return 0
    return 0


def normalize_pages(pages: list[dict[str, object]]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for page in pages:
        raw_records = page.get("records", [])
        if not isinstance(raw_records, list):
            continue
        for raw in raw_records:
            if isinstance(raw, dict):
                record = normalize_record(raw)
                if record:
                    records.append(record)
    return records


def normalize_record(raw: dict[str, object]) -> dict[str, str]:
    doi = normalize_doi(first_value(raw, "doi") or identifier_doi(first_value(raw, "identifier")))
    canonical_url = canonical_url_for(raw, doi)
    title = first_value(raw, "title")
    return {
        "source": "springer",
        "source_record_id": doi or first_value(raw, "identifier") or canonical_url or title,
        "title": title,
        "authors": "; ".join(creators(raw)),
        "year": year_from(first_value(raw, "publicationDate") or first_value(raw, "year") or first_value(raw, "onlineDate")),
        "doi": doi,
        "canonical_url": canonical_url,
        "venue": first_value(raw, "publicationName") or first_value(raw, "journal") or first_value(raw, "bookTitle"),
        "publisher": first_value(raw, "publisher"),
        "pages": pages_from(raw),
        "volume": first_value(raw, "volume"),
        "number": first_value(raw, "number"),
        "source_api_url": "",
    }


def first_value(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, dict):
                for nested_key in ("value", key, "name", "title"):
                    nested = item.get(nested_key)
                    if isinstance(nested, str) and nested.strip():
                        return nested.strip()
    return ""


def identifier_doi(identifier: str) -> str:
    match = re.search(r"doi:\s*(.+)$", identifier or "", flags=re.I)
    return match.group(1).strip() if match else ""


def canonical_url_for(raw: dict[str, object], doi: str) -> str:
    urls = raw.get("url", [])
    if isinstance(urls, list):
        for item in urls:
            if isinstance(item, dict):
                value = str(item.get("value") or "").strip()
                platform = str(item.get("platform") or "").lower()
                fmt = str(item.get("format") or "").lower()
                if value and ("springer" in platform or fmt == "html"):
                    return value
        for item in urls:
            if isinstance(item, dict) and item.get("value"):
                return str(item["value"]).strip()
            if isinstance(item, str) and item.strip():
                return item.strip()
    direct_url = first_value(raw, "url")
    if direct_url:
        return direct_url
    return f"https://doi.org/{doi}" if doi else ""


def creators(raw: dict[str, object]) -> list[str]:
    for key in ("creators", "authors", "creator"):
        value = raw.get(key)
        if isinstance(value, list):
            names = []
            for item in value:
                if isinstance(item, str) and item.strip():
                    names.append(item.strip())
                elif isinstance(item, dict):
                    name = item.get("creator") or item.get("name") or item.get("author")
                    if isinstance(name, str) and name.strip():
                        names.append(name.strip())
            if names:
                return names
        if isinstance(value, str) and value.strip():
            return [value.strip()]
    return []


def year_from(value: str) -> str:
    match = re.search(r"(\d{4})", value or "")
    return match.group(1) if match else ""


def pages_from(raw: dict[str, object]) -> str:
    pages = first_value(raw, "pages")
    if pages:
        return pages
    start = first_value(raw, "startingPage")
    end = first_value(raw, "endingPage")
    if start and end:
        return f"{start}-{end}"
    return start


def write_springer_source_manifest(
    path: Path,
    translation: QueryTranslation,
    *,
    status: str,
    request_urls: list[str],
    source_reported_count: int,
    imported_count: int,
    page_size: int,
    max_results: int | None,
    api_key_env: str,
    endpoint: str,
) -> None:
    write_json(
        path,
        {
            "source": "springer",
            "status": status,
            "retrieval_timestamp": now_iso(),
            "generic_query": translation.generic_query,
            "translated_query": translation.translated_query,
            "endpoint": endpoint,
            "endpoint_url": SPRINGER_ENDPOINTS[endpoint],
            "api_key_env": api_key_env,
            "request_urls": request_urls,
            "source_reported_result_count": source_reported_count,
            "imported_record_count": imported_count,
            "page_size": page_size,
            "max_results": max_results,
            "query_semantics_notes": translation.semantics_notes,
        },
    )
