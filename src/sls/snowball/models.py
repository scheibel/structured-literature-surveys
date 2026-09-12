from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os
import tempfile

from ..metadata import SOURCE_FIELDNAMES, CANDIDATE_FIELDNAMES

POLICY_VERSION = "snowball-1"
RECORD_FIELDS = SOURCE_FIELDNAMES + ["record_id", "request_id", "raw_path", "locator", "retrieved_at", "identifiers"]
CANDIDATE_FIELDS = CANDIDATE_FIELDNAMES + ["already_known", "is_seed", "field_provenance", "metadata_conflicts"]
OBSERVATION_FIELDS = ["observation_id", "provider", "seed_id", "direction", "round", "citing_record_id", "cited_record_id", "provider_citation_id", "request_id", "raw_path", "locator", "retrieved_at", "citing_candidate_id", "cited_candidate_id", "edge_id"]
EDGE_FIELDS = ["edge_id", "citing_candidate_id", "cited_candidate_id", "observation_count", "providers"]


def digest(value) -> str:
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=True).encode()
    return hashlib.sha256(value).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@dataclass
class Page:
    data: object
    request_id: str
    raw_path: str
    retrieved_at: str
    url: str = ""


@dataclass
class Result:
    seed: dict
    direction: str
    records: list = field(default_factory=list)
    observations: list = field(default_factory=list)
    status: str = "ok"
    reported_count: int | None = None
    complete: bool = False
    reason: str = ""
    resolution: dict = field(default_factory=dict)


def source_record(provider: str, source_id: str, page: Page, locator: str, **metadata) -> dict:
    return {**{k: "" for k in RECORD_FIELDS}, **metadata,
            "source": provider, "source_record_id": source_id,
            "record_id": digest([provider, page.request_id, locator])[:24],
            "request_id": page.request_id, "raw_path": page.raw_path,
            "locator": locator, "retrieved_at": page.retrieved_at,
            "source_api_url": page.url}


def observe(result: Result, provider: str, seed_record: dict, other: dict,
            page: Page, locator: str, citation_id="") -> None:
    citing, cited = (seed_record, other) if result.direction == "backward" else (other, seed_record)
    result.records.append(other)
    result.observations.append({
        "observation_id": digest([provider, result.seed["record_id"], result.direction, page.request_id, locator])[:24],
        "provider": provider, "seed_id": result.seed["record_id"], "direction": result.direction,
        "round": "1", "citing_record_id": citing["record_id"], "cited_record_id": cited["record_id"],
        "provider_citation_id": citation_id, "request_id": page.request_id, "raw_path": page.raw_path,
        "locator": locator, "retrieved_at": page.retrieved_at,
    })
