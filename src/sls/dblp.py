from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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
from .files import write_csv, write_json, write_text
from .identity import assign_candidate_ids, normalize_doi
from .pdfs import reconcile_candidate_pdfs
from .query import QueryTranslation, translate_for_dblp


DBLP_PUBLICATION_API_URL = "https://dblp.org/search/publ/api"
DBLP_UI_SEARCH_URL = "https://dblp.org/search"
DBLP_MAX_RESULTS = 1000
USER_AGENT = "structured-literature-surveys-spike/0.1 (+https://dblp.org/search/publ/api metadata workflow)"


@dataclass(frozen=True)
class DblpFetchResult:
    pages: list[dict[str, object]]
    request_urls: list[str]
    source_reported_count: int
    capped_at: int


def run_dblp_search(
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
    if page_size > DBLP_MAX_RESULTS:
        raise ValueError(f"page_size must be {DBLP_MAX_RESULTS} or less for DBLP")
    if max_results is not None and max_results <= 0:
        raise ValueError("max_results must be positive when provided")

    translation = translate_for_dblp(generic_query)
    today = run_date or date.today().isoformat()
    run_slug = slugify(slug or generic_query, max_len=64)
    run_dir = Path("searches") / f"{today}_{run_slug}"
    dblp_dir = run_dir / "sources" / "dblp"
    raw_dir = dblp_dir / "raw"
    derived_paths = [
        dblp_dir / "dblp_results.csv",
        dblp_dir / "source_manifest.json",
        dblp_dir / "query_semantics.md",
        run_dir / "merged_candidates.csv",
        run_dir / "validation_report.md",
        run_dir / "manual_action_queue.csv",
        run_dir / "run_config.json",
        run_dir / "bibtex_update.bib",
        run_dir / "query.md",
    ]

    prepare_directories(run_dir, raw_dir, overwrite_derived, derived_paths)
    ensure_library_dirs()
    write_dblp_query_files(run_dir, translation, page_size)
    write_json(
        run_dir / "run_config.json",
        build_dblp_run_config(
            translation=translation,
            page_size=page_size,
            max_results=max_results,
            dry_run=dry_run,
        ),
    )

    if dry_run:
        write_dblp_source_manifest(
            dblp_dir / "source_manifest.json",
            translation,
            status="dry_run",
            request_urls=[],
            source_reported_count=0,
            imported_count=0,
            page_size=page_size,
            max_results=max_results,
            capped_at=DBLP_MAX_RESULTS,
        )
        write_text(run_dir / "validation_report.md", "# Validation Report\n\nDry run only; DBLP was not contacted.\n")
        return SearchResult(run_dir, "dry_run", 0, 0, 0)

    fetched = fetch_dblp_pages(
        translation.translated_query,
        raw_dir,
        page_size=page_size,
        max_results=max_results,
    )
    source_records = normalize_pages(fetched.pages)
    if max_results is not None:
        source_records = source_records[:max_results]
    imported_count = len(source_records)
    write_csv(dblp_dir / "dblp_results.csv", source_records, SOURCE_FIELDNAMES)

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
    append_dblp_validation_notes(
        run_dir / "validation_report.md",
        fetched.source_reported_count,
        imported_count,
        max_results,
    )
    write_dblp_source_manifest(
        dblp_dir / "source_manifest.json",
        translation,
        status="ok",
        request_urls=fetched.request_urls,
        source_reported_count=fetched.source_reported_count,
        imported_count=imported_count,
        page_size=page_size,
        max_results=max_results,
        capped_at=fetched.capped_at,
    )

    return SearchResult(run_dir, "ok", fetched.source_reported_count, imported_count, len(candidates))


def write_dblp_query_files(run_dir: Path, translation: QueryTranslation, page_size: int) -> None:
    api_url = dblp_request_url(translation.translated_query, first=0, page_size=page_size)
    manual_url = f"{DBLP_UI_SEARCH_URL}?{urlencode({'q': translation.translated_query})}"
    query_md = f"""# Search Query

## Generic Query

```text
{translation.generic_query}
```

## DBLP Translated Query

```text
{translation.translated_query}
```

## DBLP Publication API URL

{api_url}

## Manual DBLP URL

{manual_url}
"""
    write_text(run_dir / "query.md", query_md)

    semantics = ["# Query Semantics Report", ""]
    semantics.append(f"- Source: `{translation.source}`")
    semantics.append(f"- Generic query: `{translation.generic_query}`")
    semantics.append(f"- Translated query: `{translation.translated_query}`")
    semantics.append("")
    semantics.append("## Notes")
    semantics.extend(f"- {note}" for note in translation.semantics_notes)
    write_text(run_dir / "sources" / "dblp" / "query_semantics.md", "\n".join(semantics) + "\n")


def build_dblp_run_config(
    *,
    translation: QueryTranslation,
    page_size: int,
    max_results: int | None,
    dry_run: bool,
) -> dict[str, object]:
    return {
        "created_at": now_iso(),
        "source": "dblp",
        "enabled_sources": ["dblp"],
        "disabled_sources": [],
        "generic_query": translation.generic_query,
        "translated_query": translation.translated_query,
        "endpoint_url": DBLP_PUBLICATION_API_URL,
        "page_size": page_size,
        "max_results": max_results,
        "source_result_cap": DBLP_MAX_RESULTS,
        "dry_run": dry_run,
        "code_version": git_revision(),
        "tool": "scripts/sls dblp-search",
    }


def fetch_dblp_pages(
    query: str,
    raw_dir: Path,
    *,
    page_size: int,
    max_results: int | None,
) -> DblpFetchResult:
    pages: list[dict[str, object]] = []
    request_urls: list[str] = []
    source_reported_count = 0
    imported_so_far = 0
    first = 0
    requested_total = min(max_results or DBLP_MAX_RESULTS, DBLP_MAX_RESULTS)

    while imported_so_far < requested_total:
        current_page_size = min(page_size, requested_total - imported_so_far)
        url = dblp_request_url(query, first=first, page_size=current_page_size)
        data = fetch_dblp_json(url)
        request_urls.append(url)
        write_json(raw_dir / f"page_{len(pages):04d}.json", data)
        pages.append(data)

        hits = extract_hits(data)
        current_size = len(hits)
        source_reported_count = max(source_reported_count, source_reported_total(data))
        imported_so_far += current_size

        if current_size == 0:
            break
        if imported_so_far >= requested_total:
            break
        if source_reported_count and first + current_size >= min(source_reported_count, DBLP_MAX_RESULTS):
            break
        first += current_size

    return DblpFetchResult(
        pages=pages,
        request_urls=request_urls,
        source_reported_count=source_reported_count,
        capped_at=DBLP_MAX_RESULTS,
    )


def dblp_request_url(query: str, *, first: int, page_size: int) -> str:
    params = {
        "q": query,
        "format": "json",
        "h": page_size,
        "f": first,
        "c": 0,
    }
    return f"{DBLP_PUBLICATION_API_URL}?{urlencode(params)}"


def fetch_dblp_json(url: str) -> dict[str, object]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_pages(pages: list[dict[str, object]]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for page in pages:
        for hit in extract_hits(page):
            record = normalize_hit(hit)
            if record:
                records.append(record)
    return records


def normalize_hit(hit: dict[str, object]) -> dict[str, str]:
    info = hit.get("info", {})
    if not isinstance(info, dict):
        return {}

    doi = normalize_doi(str(info.get("doi") or ""))
    canonical_url = canonical_url_for(info, doi)
    dblp_key = str(info.get("key") or "")
    dblp_url = str(info.get("url") or "")
    source_record_id = dblp_key or str(hit.get("@id") or "") or doi or canonical_url

    return {
        "source": "dblp",
        "source_record_id": source_record_id,
        "title": clean_text(str(info.get("title") or "")),
        "authors": "; ".join(authors_for(info)),
        "year": year_from(str(info.get("year") or "")),
        "doi": doi,
        "canonical_url": canonical_url,
        "venue": clean_text(str(info.get("venue") or "")),
        "publisher": clean_text(str(info.get("publisher") or "")),
        "pages": clean_text(str(info.get("pages") or "")),
        "volume": clean_text(str(info.get("volume") or "")),
        "number": clean_text(str(info.get("number") or "")),
        "source_api_url": dblp_url,
    }


def extract_hits(page: dict[str, object]) -> list[dict[str, object]]:
    result = page.get("result", {})
    if not isinstance(result, dict):
        return []
    hits = result.get("hits", {})
    if not isinstance(hits, dict):
        return []
    raw = hits.get("hit", [])
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [hit for hit in raw if isinstance(hit, dict)]
    return []


def source_reported_total(page: dict[str, object]) -> int:
    result = page.get("result", {})
    if not isinstance(result, dict):
        return 0
    hits = result.get("hits", {})
    if not isinstance(hits, dict):
        return 0
    try:
        return int(str(hits.get("@total") or "0"))
    except ValueError:
        return 0


def authors_for(info: dict[str, object]) -> list[str]:
    authors = info.get("authors", {})
    if not isinstance(authors, dict):
        return []
    raw = authors.get("author", [])
    if isinstance(raw, str):
        return [clean_text(raw)]
    if isinstance(raw, dict):
        return [clean_text(str(raw.get("text") or ""))]
    if isinstance(raw, list):
        names: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                names.append(clean_text(str(item.get("text") or "")))
            elif isinstance(item, str):
                names.append(clean_text(item))
        return [name for name in names if name]
    return []


def canonical_url_for(info: dict[str, object], doi: str) -> str:
    ee = first_electronic_edition(info.get("ee"))
    if ee:
        return ee
    if doi:
        return f"https://doi.org/{doi}"
    return str(info.get("url") or "")


def first_electronic_edition(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("text") or "")
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item:
                return item
            if isinstance(item, dict) and item.get("text"):
                return str(item["text"])
    return ""


def year_from(value: str) -> str:
    match = re.search(r"(\d{4})", value or "")
    return match.group(1) if match else ""


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def write_dblp_source_manifest(
    path: Path,
    translation: QueryTranslation,
    *,
    status: str,
    request_urls: list[str],
    source_reported_count: int,
    imported_count: int,
    page_size: int,
    max_results: int | None,
    capped_at: int,
) -> None:
    write_json(
        path,
        {
            "source": "dblp",
            "status": status,
            "retrieval_timestamp": now_iso(),
            "generic_query": translation.generic_query,
            "translated_query": translation.translated_query,
            "api_base_url": DBLP_PUBLICATION_API_URL,
            "request_urls": request_urls,
            "source_reported_result_count": source_reported_count,
            "imported_record_count": imported_count,
            "page_size": page_size,
            "max_results": max_results,
            "source_result_cap": capped_at,
            "query_semantics_notes": translation.semantics_notes,
        },
    )


def append_dblp_validation_notes(
    path: Path,
    source_reported_count: int,
    imported_count: int,
    max_results: int | None,
) -> None:
    lines = []
    if source_reported_count >= DBLP_MAX_RESULTS and max_results is None:
        lines.extend(
            [
                "",
                "## DBLP Notes",
                "",
                f"- DBLP documents a maximum of {DBLP_MAX_RESULTS} search results. If this query reaches that cap, partition the search and record each partition.",
            ]
        )
    if max_results is not None:
        lines.extend(
            [
                "",
                "## DBLP Notes",
                "",
                f"- This run intentionally imported at most {max_results} DBLP records.",
            ]
        )
    if lines:
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
