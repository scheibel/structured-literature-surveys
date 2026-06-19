from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import json
import re
import shutil
from urllib.parse import urlencode

from .bibtex import acm_canonical_url, parse_bibtex_records
from .eg import (
    CANDIDATE_FIELDNAMES,
    SOURCE_FIELDNAMES,
    SearchResult,
    deduplicate,
    ensure_library_dirs,
    git_revision,
    now_iso,
    slugify,
    update_bibtex,
    write_manual_action_queue,
    write_validation_report,
)
from .files import read_csv, write_csv, write_json, write_text
from .identity import assign_candidate_ids, normalize_doi
from .pdfs import reconcile_candidate_pdfs
from .query import QueryTranslation, translate_for_acm


ACM_SEARCH_URL = "https://dl.acm.org/action/doSearch"


@dataclass(frozen=True)
class AcmImportResult:
    run_dir: Path
    status: str
    source_reported_count: int
    imported_count: int
    deduplicated_count: int
    raw_export_path: Path


def prepare_acm_search(
    *,
    generic_query: str,
    slug: str | None = None,
    run_date: str | None = None,
    overwrite_derived: bool = False,
) -> SearchResult:
    translation = translate_for_acm(generic_query)
    today = run_date or date.today().isoformat()
    run_slug = slugify(slug or generic_query, max_len=64)
    run_dir = Path("searches") / f"{today}_{run_slug}"
    acm_dir = run_dir / "sources" / "acm"
    raw_dir = acm_dir / "raw"
    derived_paths = [
        acm_dir / "source_manifest.json",
        acm_dir / "query_semantics.md",
        run_dir / "query.md",
        run_dir / "run_config.json",
        run_dir / "validation_report.md",
    ]

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

    ensure_library_dirs()
    write_acm_query_files(run_dir, translation)
    write_json(run_dir / "run_config.json", build_acm_run_config(translation))
    write_acm_source_manifest(
        acm_dir / "source_manifest.json",
        translation,
        status="manual_export_required",
        source_reported_count=0,
        imported_count=0,
        export_format="",
        raw_export_path="",
        source_url=acm_search_url(translation),
        notes="Run the ACM search manually in a browser, export BibTeX or RIS, then import the raw export.",
    )
    write_text(
        run_dir / "validation_report.md",
        "# Validation Report\n\nACM manual export is pending; no records have been imported yet.\n",
    )
    return SearchResult(run_dir, "manual_export_required", 0, 0, 0)


def import_acm_export(
    *,
    run_dir: Path,
    export_path: Path,
    export_format: str = "auto",
    reported_count: int | None = None,
    source_url: str = "",
    notes: str = "",
) -> AcmImportResult:
    run_dir = run_dir.expanduser()
    export_path = export_path.expanduser()
    if not run_dir.exists():
        raise FileNotFoundError(run_dir)
    if not export_path.exists():
        raise FileNotFoundError(export_path)

    ensure_library_dirs()
    acm_dir = run_dir / "sources" / "acm"
    raw_dir = acm_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_export = copy_raw_export(export_path, raw_dir)

    parsed_format = detect_export_format(raw_export, export_format)
    records = parse_acm_export(raw_export, parsed_format)
    write_csv(acm_dir / "acm_results.csv", records, SOURCE_FIELDNAMES)

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

    translation = translation_from_run_config(run_dir)
    write_acm_source_manifest(
        acm_dir / "source_manifest.json",
        translation,
        status="ok",
        source_reported_count=source_reported_count,
        imported_count=len(records),
        export_format=parsed_format,
        raw_export_path=str(raw_export),
        source_url=source_url or acm_search_url(translation),
        notes=notes,
    )

    return AcmImportResult(
        run_dir=run_dir,
        status="ok",
        source_reported_count=source_reported_count,
        imported_count=len(records),
        deduplicated_count=len(candidates),
        raw_export_path=raw_export,
    )


def write_acm_query_files(run_dir: Path, translation: QueryTranslation) -> None:
    url = acm_search_url(translation)
    query_md = f"""# Search Query

## Generic Query

```text
{translation.generic_query}
```

## ACM Translated Query

```text
{translation.translated_query}
```

## Manual ACM URL

{url}

## Manual Execution Notes

1. Open the ACM URL in a normal browser session.
2. Verify that the search box contains the translated query above.
3. Apply only filters that you record explicitly in `sources/acm/source_manifest.json`.
4. Record the visible ACM result count.
5. Export the result set as BibTeX or RIS using ACM's own export controls.
6. Import the raw export with `python3 scripts/sls acm-import --run-dir {run_dir} --export <file> --reported-count <count>`.
"""
    write_text(run_dir / "query.md", query_md)

    semantics = ["# Query Semantics Report", ""]
    semantics.append(f"- Source: `{translation.source}`")
    semantics.append(f"- Generic query: `{translation.generic_query}`")
    semantics.append(f"- Translated query: `{translation.translated_query}`")
    semantics.append("")
    semantics.append("## Notes")
    semantics.extend(f"- {note}" for note in translation.semantics_notes)
    write_text(run_dir / "sources" / "acm" / "query_semantics.md", "\n".join(semantics) + "\n")


def acm_search_url(translation: QueryTranslation) -> str:
    return f"{ACM_SEARCH_URL}?{urlencode({'AllField': translation.translated_query})}"


def build_acm_run_config(translation: QueryTranslation) -> dict[str, object]:
    return {
        "created_at": now_iso(),
        "source": "acm",
        "enabled_sources": ["acm"],
        "disabled_sources": [],
        "generic_query": translation.generic_query,
        "translated_query": translation.translated_query,
        "manual_search_url": acm_search_url(translation),
        "code_version": git_revision(),
        "tool": "scripts/sls acm-prepare",
    }


def write_acm_source_manifest(
    path: Path,
    translation: QueryTranslation,
    *,
    status: str,
    source_reported_count: int,
    imported_count: int,
    export_format: str,
    raw_export_path: str,
    source_url: str,
    notes: str,
) -> None:
    write_json(
        path,
        {
            "source": "acm",
            "status": status,
            "retrieval_timestamp": now_iso(),
            "generic_query": translation.generic_query,
            "translated_query": translation.translated_query,
            "manual_search_url": acm_search_url(translation),
            "source_url": source_url,
            "source_reported_result_count": source_reported_count,
            "imported_record_count": imported_count,
            "export_format": export_format,
            "raw_export_path": raw_export_path,
            "query_semantics_notes": translation.semantics_notes,
            "notes": notes,
        },
    )


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
        else:
            head = path.read_text(encoding="utf-8", errors="replace")[:200].lstrip()
            normalized = "bibtex" if head.startswith("@") else "ris"
    if normalized not in {"bibtex", "ris"}:
        raise ValueError("export format must be bibtex, ris, or auto")
    return normalized


def parse_acm_export(path: Path, export_format: str) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if export_format == "bibtex":
        return parse_bibtex_records(text, source="acm")
    if export_format == "ris":
        return parse_ris_records(text, source="acm")
    raise ValueError(f"unsupported ACM export format: {export_format}")


def parse_ris_records(text: str, *, source: str) -> list[dict[str, str]]:
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

    return [ris_fields_to_record(fields, source=source) for fields in records]


def ris_fields_to_record(fields: dict[str, list[str]], *, source: str) -> dict[str, str]:
    doi = normalize_doi(first_ris(fields, "DO"))
    url = first_ris(fields, "UR")
    canonical_url = acm_canonical_url(doi, url) if source == "acm" else url
    title = first_ris(fields, "TI", "T1", "CT")
    return {
        "source": source,
        "source_record_id": doi or url or title,
        "title": title,
        "authors": "; ".join(fields.get("AU", []) + fields.get("A1", [])),
        "year": year_from_ris(fields),
        "doi": doi,
        "canonical_url": canonical_url,
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


def year_from_ris(fields: dict[str, list[str]]) -> str:
    for tag in ("PY", "Y1", "DA"):
        match = re.search(r"(\d{4})", first_ris(fields, tag))
        if match:
            return match.group(1)
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


def translation_from_run_config(run_dir: Path) -> QueryTranslation:
    config_path = run_dir / "run_config.json"
    if not config_path.exists():
        return QueryTranslation(
            generic_query="",
            source="acm",
            translated_query="",
            semantics_notes=["No run_config.json was available during manual export import."],
        )

    config = json.loads(config_path.read_text(encoding="utf-8"))
    generic_query = str(config.get("generic_query") or "")
    translated_query = str(config.get("translated_query") or generic_query)
    notes = [
        "Loaded ACM translation from run_config.json during manual export import.",
        "The researcher is responsible for verifying browser-side query semantics.",
    ]
    return QueryTranslation(
        generic_query=generic_query,
        source="acm",
        translated_query=translated_query,
        semantics_notes=notes,
    )
