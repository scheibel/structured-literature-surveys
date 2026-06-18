from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import shutil

from .files import read_csv, write_csv


PDF_MANIFEST = Path("library/manifests/pdfs.csv")
PDF_DIR = Path("library/pdfs")

PDF_MANIFEST_FIELDNAMES = [
    "candidate_id",
    "pdf_path",
    "checksum",
    "file_size",
    "match_method",
    "note",
]

MISSING_PDF_FIELDNAMES = [
    "candidate_id",
    "title",
    "authors",
    "year",
    "doi",
    "canonical_url",
    "source_runs",
]


@dataclass(frozen=True)
class PdfRegistration:
    candidate_id: str
    pdf_path: Path
    checksum: str
    file_size: int


def ensure_pdf_store() -> None:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    PDF_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    if not PDF_MANIFEST.exists():
        write_csv(PDF_MANIFEST, [], PDF_MANIFEST_FIELDNAMES)


def read_pdf_manifest() -> list[dict[str, str]]:
    ensure_pdf_store()
    return read_csv(PDF_MANIFEST)


def write_pdf_manifest(rows: list[dict[str, str]]) -> None:
    write_csv(PDF_MANIFEST, rows, PDF_MANIFEST_FIELDNAMES)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_pdf(
    *,
    candidate_id: str,
    source_pdf: Path,
    copy: bool = True,
    note: str = "",
) -> PdfRegistration:
    ensure_pdf_store()
    source_pdf = source_pdf.expanduser().resolve()
    if not source_pdf.exists():
        raise FileNotFoundError(source_pdf)
    if source_pdf.suffix.lower() != ".pdf":
        raise ValueError(f"expected a PDF file, got {source_pdf}")

    target = PDF_DIR / f"{candidate_id}.pdf"
    if copy:
        if source_pdf != target.resolve():
            shutil.copy2(source_pdf, target)
    else:
        target = source_pdf

    checksum = sha256_file(target)
    file_size = target.stat().st_size

    rows = [row for row in read_pdf_manifest() if row.get("candidate_id") != candidate_id]
    rows.append(
        {
            "candidate_id": candidate_id,
            "pdf_path": str(target),
            "checksum": checksum,
            "file_size": str(file_size),
            "match_method": "registered",
            "note": note,
        }
    )
    rows.sort(key=lambda row: row.get("candidate_id", ""))
    write_pdf_manifest(rows)

    return PdfRegistration(candidate_id, target, checksum, file_size)


def reconcile_candidate_pdfs(candidates: list[dict[str, str]]) -> bool:
    """Update candidate PDF status from the manifest and candidate-id filenames.

    Returns True when the manifest changed.
    """

    ensure_pdf_store()
    manifest_rows = prune_missing_manifest_rows(read_pdf_manifest())
    by_candidate = {row.get("candidate_id", ""): row for row in manifest_rows}
    pdf_files = {path.stem: path for path in PDF_DIR.glob("*.pdf")}

    changed = False
    for candidate in candidates:
        cid = candidate["candidate_id"]
        candidate["has_local_pdf"] = "no"
        candidate["pdf_path"] = ""

        row = by_candidate.get(cid)
        if row and row.get("pdf_path") and Path(row["pdf_path"]).exists():
            candidate["has_local_pdf"] = "yes"
            candidate["pdf_path"] = row["pdf_path"]
            continue

        if cid in pdf_files:
            path = pdf_files[cid]
            by_candidate[cid] = {
                "candidate_id": cid,
                "pdf_path": str(path),
                "checksum": sha256_file(path),
                "file_size": str(path.stat().st_size),
                "match_method": "filename_candidate_id",
                "note": "",
            }
            candidate["has_local_pdf"] = "yes"
            candidate["pdf_path"] = str(path)
            changed = True

    if changed:
        rows = sorted(by_candidate.values(), key=lambda row: row.get("candidate_id", ""))
        write_pdf_manifest(rows)
    return changed


def prune_missing_manifest_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    kept = [row for row in rows if row.get("pdf_path") and Path(row["pdf_path"]).exists()]
    if len(kept) != len(rows):
        write_pdf_manifest(sorted(kept, key=lambda row: row.get("candidate_id", "")))
    return kept


def collect_candidates(run_dir: Path | None = None) -> list[dict[str, str]]:
    paths: list[Path]
    if run_dir is not None:
        paths = [run_dir / "merged_candidates.csv"]
    else:
        paths = sorted(Path("searches").glob("*/merged_candidates.csv"))

    candidates: dict[str, dict[str, str]] = {}
    for path in paths:
        for row in read_csv(path):
            cid = row.get("candidate_id", "")
            if not cid:
                continue
            existing = candidates.get(cid)
            if existing is None:
                row["source_runs"] = str(path.parent)
                candidates[cid] = row
            else:
                runs = set(existing.get("source_runs", "").split(";"))
                runs.add(str(path.parent))
                existing["source_runs"] = ";".join(sorted(r for r in runs if r))
    return sorted(candidates.values(), key=lambda row: row.get("candidate_id", ""))


def missing_pdf_rows(run_dir: Path | None = None) -> list[dict[str, str]]:
    candidates = collect_candidates(run_dir)
    reconcile_candidate_pdfs(candidates)
    return [
        {
            "candidate_id": row.get("candidate_id", ""),
            "title": row.get("title", ""),
            "authors": row.get("authors", ""),
            "year": row.get("year", ""),
            "doi": row.get("doi", ""),
            "canonical_url": row.get("canonical_url", ""),
            "source_runs": row.get("source_runs", ""),
        }
        for row in candidates
        if row.get("has_local_pdf") != "yes"
    ]


def write_missing_pdf_report(path: Path, run_dir: Path | None = None) -> list[dict[str, str]]:
    rows = missing_pdf_rows(run_dir)
    write_csv(path, rows, MISSING_PDF_FIELDNAMES)
    return rows


def refresh_run_pdf_status(run_dir: Path) -> int:
    candidates_path = run_dir / "merged_candidates.csv"
    candidates = read_csv(candidates_path)
    if not candidates:
        raise FileNotFoundError(f"no candidates found at {candidates_path}")
    reconcile_candidate_pdfs(candidates)
    fieldnames = list(candidates[0].keys())
    write_csv(candidates_path, candidates, fieldnames)
    write_missing_pdf_report(run_dir / "missing_pdfs.csv", run_dir)
    return sum(1 for row in candidates if row.get("has_local_pdf") == "yes")
