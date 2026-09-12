from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
import fcntl
import json
from pathlib import Path
import re
import subprocess

from ..bibtex import bibtex_fields_to_record, iter_bibtex_entries, parse_bibtex_fields, record_to_bibtex
from ..files import read_csv, write_csv
from ..identity import normalize_doi
from ..metadata import ACTION_FIELDNAMES
from ..pdfs import sha256_file
from .identity import reconcile
from .models import (CANDIDATE_FIELDS, RECORD_FIELDS, EDGE_FIELDS, OBSERVATION_FIELDS,
                     POLICY_VERSION, atomic_json, atomic_text, digest, now)
from .providers import PROVIDERS
from .providers.base import Client, ENV_NAMES


@contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(f"Another process is using {path}") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def known_records(project):
    records = []
    registry = project / "library/manifests/snowball_identity.json"
    if registry.exists():
        records.extend(json.loads(registry.read_text()))
    for path in sorted((project / "searches").glob("*/merged_candidates.csv")):
        for row in read_csv(path):
            if row.get("candidate_id"):
                records.append({**row, "source": "library", "source_record_id": row["candidate_id"], "source_api_url": str(path.relative_to(project))})
    bib = project / "library/bibtex/candidates.bib"
    if bib.exists():
        for entry in iter_bibtex_entries(bib.read_text()):
            row = bibtex_fields_to_record(entry["key"], parse_bibtex_fields(entry["body"]), source="library")
            records.append({**row, "candidate_id": entry["key"], "bibtex_key": entry["key"]})
    result = {}
    for row in records:
        row = dict(row)
        row["record_id"] = row.get("record_id") or "known" + digest(row)[:20]
        row["already_known"] = "yes"
        row["is_seed"] = "no"
        result[row["record_id"]] = row
    return sorted(result.values(), key=lambda r: r["record_id"])


def read_seeds(path, candidate_ids, known):
    rows = []
    if path:
        path = Path(path).expanduser()
        if path.suffix.lower() == ".bib":
            for entry in iter_bibtex_entries(path.read_text()):
                rows.append(bibtex_fields_to_record(entry["key"], parse_bibtex_fields(entry["body"]), source="seed"))
        else:
            rows = read_csv(path)
    rows.extend({"candidate_id": cid} for cid in candidate_ids)
    if not rows:
        raise ValueError("Provide a nonempty seeds CSV/BibTeX or --candidate-id")
    for row in rows:
        if row.get("candidate_id"):
            matches = [k for k in known if k.get("candidate_id") == row["candidate_id"]]
            if matches:
                base = {k: v for r in matches for k, v in r.items() if v}
                base.update({k: v for k, v in row.items() if v})
                row.update(base)
        row["doi"] = normalize_doi(row.get("doi", ""))
        if not (row.get("doi") or row.get("title") or any(row.get(p + "_id") for p in PROVIDERS)):
            raise ValueError("Seed lacks metadata or identifiers; unknown candidate ID")
        row.update(source="seed", source_record_id=row.get("doi") or row.get("candidate_id", ""), is_seed="yes")
        row["record_id"] = "seed" + digest(row)[:20]
    candidates, mapping, decisions, unresolved = reconcile(known + rows)
    if any(d["decision"] == "review" and any(r["record_id"] in (d["left"], d["right"]) for r in rows) for d in decisions):
        raise ValueError("Ambiguous seed identity; resolve conflicting seed metadata before preparing")
    # Provider-only seeds may have no bibliographic metadata until resolution.
    for r in rows:
        if mapping[r["record_id"]].startswith("unresolved"):
            cid = r.get("candidate_id") or "record" + digest({p: r.get(p + "_id") for p in PROVIDERS})[:16]
            candidates.append({**r, "candidate_id": cid, "bibtex_key": cid})
            mapping[r["record_id"]] = cid
    by_cid = {c["candidate_id"]: c for c in candidates}
    seeds = {}
    for row in rows:
        cid = mapping[row["record_id"]]
        seed = seeds.setdefault(cid, {**by_cid[cid], "record_id": "seed" + digest(cid)[:20], "source": "seed", "source_record_id": cid, "is_seed": "yes"})
        for p in PROVIDERS:
            if row.get(p + "_id"):
                if seed.get(p + "_id") and seed[p + "_id"] != row[p + "_id"]:
                    raise ValueError("Conflicting explicit provider IDs for one seed")
                seed[p + "_id"] = row[p + "_id"]
    return sorted(seeds.values(), key=lambda r: r["record_id"])


def prepare(*, project, seeds=None, candidate_ids=(), providers=None, direction="both", slug="snowball",
            run_date=None, request_budget=1000, budgets=None, max_relationships=None, timeout=20,
            retries=2, page_size=100, request_interval=1.0, credential_env=None, parent_run=None,
            overrides=None, smoke=False, benchmark=None):
    project = Path(project).resolve()
    providers = list(dict.fromkeys(providers or PROVIDERS))
    if not providers or any(p not in PROVIDERS for p in providers):
        raise ValueError("Unknown or empty provider selection")
    if direction not in ("both", "backward", "forward"):
        raise ValueError("Invalid direction")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", slug):
        raise ValueError("Slug must contain only letters, digits, hyphens, or underscores")
    run_date = run_date or date.today().isoformat()
    date.fromisoformat(run_date)
    request_budgets = {p: (budgets or {}).get(p, request_budget) for p in providers}
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in (credential_env or {}).values()):
        raise ValueError("Credential configuration must contain environment variable names")
    if min(request_budgets.values()) < 1 or timeout <= 0 or retries < 0 or page_size < 1 or request_interval < 1 or (max_relationships is not None and max_relationships < 1):
        raise ValueError("Budgets, timeouts, page sizes and caps must be positive; interval >= 1s, retries >= 0")
    known = [] if smoke else known_records(project)
    selected = read_seeds(seeds, candidate_ids, known)
    run = project / ("smoke-tests" if smoke else "searches") / f"{run_date}_{slug}-snowball"
    if run.exists():
        raise FileExistsError(f"Run exists: {run}; resume/rebuild it or choose another slug")
    override_rows = read_csv(Path(overrides)) if overrides else []
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unknown"
    config = {"run_type": "snowball", "policy_version": POLICY_VERSION, "created_at": now(), "code_revision": revision,
              "code_digest": digest({str(p.relative_to(Path(__file__).parents[1])): digest(p.read_bytes()) for p in sorted(Path(__file__).parents[1].rglob("*.py"))}),
              "providers": providers, "directions": ["backward", "forward"] if direction == "both" else [direction],
              "round": 1, "parent_run": str(parent_run or ""), "request_budgets": request_budgets,
              "max_relationships": max_relationships, "timeout": timeout, "retries": retries,
              "page_size": page_size, "request_interval": request_interval,
              "credential_env": {**ENV_NAMES, **(credential_env or {})}, "smoke": smoke, "rerun_policy": "resume_cached_requests_or_offline_rebuild"}
    run.mkdir(parents=True)
    inputs = {"seed_snapshot.json": selected, "known_candidates.json": known, "overrides.json": override_rows}
    if benchmark:
        benchmark = Path(benchmark)
        for name in ("benchmark.json", "expected_edges.csv", "expected_metadata.csv"):
            path = benchmark / name
            if path.exists():
                atomic_text(run / "benchmark" / name, path.read_text())
                config.setdefault("benchmark_checksums", {})[name] = digest((run / "benchmark" / name).read_bytes())
    for name, value in inputs.items():
        atomic_json(run / name, value)
    if seeds:
        source = Path(seeds).expanduser()
        raw = source.read_bytes()
        (run / ("seed_input" + source.suffix)).write_bytes(raw)
        config["original_seed_input"] = {"file": "seed_input" + source.suffix, "checksum": digest(raw)}
    config["input_checksums"] = {name: digest((run / name).read_bytes()) for name in inputs}
    capabilities = Path(__file__).with_name("capabilities.json")
    atomic_text(run / "provider_capabilities.json", capabilities.read_text())
    config["input_checksums"]["provider_capabilities.json"] = digest(capabilities.read_bytes())
    atomic_json(run / "run_config.json", config)
    atomic_text(run / "config.sha256", digest((run / "run_config.json").read_bytes()) + "\n")
    write_csv(run / "seeds.csv", selected, list(dict.fromkeys(RECORD_FIELDS + ["candidate_id", "bibtex_key"] + [p + "_id" for p in PROVIDERS])))
    atomic_text(run / "query.md", "# Snowballing run\n\nSeeds: seeds.csv. Directions: " + ", ".join(config["directions"]) + ".\nProviders: " + ", ".join(providers) + ".\nOne round; no topical query filtering. Inspect seed_resolution.csv after collection.\n")
    atomic_text(run / "validation_report.md", "# Validation\n\nPrepared offline; collection pending.\n")
    return run


def verify(run):
    if digest((run / "run_config.json").read_bytes()) != (run / "config.sha256").read_text().strip():
        raise ValueError("Run configuration changed; create a new run")
    config = json.loads((run / "run_config.json").read_text())
    for name, checksum in config["input_checksums"].items():
        if digest((run / name).read_bytes()) != checksum:
            raise ValueError(f"Immutable input changed: {name}")
    for name, checksum in config.get("benchmark_checksums", {}).items():
        if digest((run / "benchmark" / name).read_bytes()) != checksum:
            raise ValueError("Benchmark snapshot changed")
    original = config.get("original_seed_input")
    if original and digest((run / original["file"]).read_bytes()) != original["checksum"]:
        raise ValueError("Original seed input changed")
    for path in run.glob("sources/*/requests.json"):
        for entry in json.loads(path.read_text()):
            if entry.get("raw_path") and digest((run / entry["raw_path"]).read_bytes()) != entry["checksum"]:
                raise ValueError(f"Raw artifact changed: {entry['raw_path']}")
    return config


def collect(run, *, project, resume=False, offline=False, client_class=Client):
    run, project = Path(run).resolve(), Path(project).resolve()
    config = verify(run)
    if run.parent != project / ("smoke-tests" if config["smoke"] else "searches"):
        raise ValueError("Run from the project root containing this run directory")
    if not offline and not resume and any(run.glob("sources/*/requests.json")):
        raise ValueError("Collection already started; use --resume or rebuild")
    with lock(run / ".run.lock"):
        seeds = json.loads((run / "seed_snapshot.json").read_text())
        results = []
        for provider in config["providers"]:
            client = client_class(run, provider, config, offline=offline)
            adapter = PROVIDERS[provider](client)
            for seed in seeds:
                for direction in config["directions"]:
                    result = adapter.run(seed, direction)
                    if result.resolution:
                        for record in result.records:
                            if record["record_id"] == result.resolution["record_id"]:
                                record.update(candidate_id=seed["candidate_id"], bibtex_key=seed["bibtex_key"], is_seed="yes")
                    results.append(result)
                    atomic_json(run / "checkpoint.json", {"config_hash": digest(config), "completed": [asdict(r) for r in results]})
            # The manifest keeps statuses per seed/direction, never just the last request.
            source_results = results[-len(seeds) * len(config["directions"]):]
            atomic_json(run / "sources" / provider / "source_manifest.json", {
                "provider": provider, "attribution": "Semantic Scholar" if provider == "semantic_scholar" else provider,
                "operations": [operation(r, provider) for r in source_results]})
        verify(run)
        return derive(run, project, config, seeds, results, publish=not offline)


def operation(result, provider):
    imported = len(result.observations)
    mismatch = result.reported_count is not None and imported != result.reported_count
    return {"provider": provider, "seed_id": result.seed["record_id"], "direction": result.direction,
            "status": result.status, "reason": result.reason, "source_reported_count": result.reported_count,
            "imported_count": imported, "complete": result.complete, "count_mismatch": mismatch,
            "resolution": result.resolution}


def derive(run, project, config, seeds, results, *, publish):
    atomic_json(run / "derivation_manifest.json", {
        "policy_version": POLICY_VERSION, "configuration_hash": digest(config),
        "code_digest": digest({str(p.relative_to(Path(__file__).parents[1])): digest(p.read_bytes()) for p in sorted(Path(__file__).parents[1].rglob("*.py"))}),
        "mode": "collect" if publish else "offline_rebuild", "derived_at": now()})
    known = json.loads((run / "known_candidates.json").read_text())
    records = {r["record_id"]: r for r in known + seeds}
    observations, operations = {}, []
    for index, result in enumerate(results):
        provider = config["providers"][index // (len(seeds) * len(config["directions"]))]
        operations.append(operation(result, provider))
        records.update({r["record_id"]: r for r in result.records})
        observations.update({o["observation_id"]: dict(o) for o in result.observations})
    overrides = json.loads((run / "overrides.json").read_text())
    candidates, mapping, decisions, unresolved = reconcile(list(records.values()), overrides)
    participating = {r["record_id"] for r in seeds} | {rid for o in observations.values() for rid in (o["citing_record_id"], o["cited_record_id"])}
    wanted = {mapping[rid] for rid in participating}
    candidates = [c for c in candidates if c["candidate_id"] in wanted]
    edges = {}
    for o in observations.values():
        a, b = mapping[o["citing_record_id"]], mapping[o["cited_record_id"]]
        eid = digest([a, b])[:24] if not a.startswith("unresolved") and not b.startswith("unresolved") else ""
        o.update(citing_candidate_id=a, cited_candidate_id=b, edge_id=eid)
        if eid:
            edge = edges.setdefault(eid, {"edge_id": eid, "citing_candidate_id": a, "cited_candidate_id": b, "observation_count": 0, "providers": set()})
            edge["observation_count"] += 1
            edge["providers"].add(o["provider"])
    for e in edges.values():
        e["providers"] = ";".join(sorted(e["providers"]))
    actions = []
    for c in candidates:
        for action, reason in (("incomplete_metadata", "Missing authors/title/year"), ("review_duplicate", "Ambiguous identity evidence")):
            if (action == "incomplete_metadata" and not all(c.get(k) for k in ("title", "authors", "year"))) or (action == "review_duplicate" and c["manual_review"] == "yes"):
                actions.append({**c, "action": action, "reason": reason})
    for u in unresolved:
        actions.append({**u, "candidate_id": u["unresolved_id"], "action": "unresolved_reference"})
    for op in operations:
        if op["status"] not in ("ok", "empty") or op["count_mismatch"]:
            actions.append({"candidate_id": mapping[op["seed_id"]], "action": "source_action", "reason": json.dumps(op, sort_keys=True)})
    if publish:
        library = run / "library" if config["smoke"] else project / "library"
        publish_library(library, project, run, candidates, records, mapping, actions)
    else:
        # Rebuild does not mutate the library; refresh reports against current assets.
        library = run / "library" if config["smoke"] else project / "library"
        inspect_library(library, project, candidates, actions)
    for provider in config["providers"]:
        write_csv(run / "sources" / provider / "normalized_records.csv", [r for r in records.values() if r.get("source") == provider], RECORD_FIELDS)
    write_csv(run / "source_records.csv", sorted(records.values(), key=lambda r: r["record_id"]), list(dict.fromkeys(RECORD_FIELDS + ["candidate_id", "bibtex_key"])))
    write_csv(run / "citation_observations.csv", sorted(observations.values(), key=lambda o: o["observation_id"]), OBSERVATION_FIELDS)
    write_csv(run / "citation_edges.csv", sorted(edges.values(), key=lambda e: e["edge_id"]), EDGE_FIELDS)
    write_csv(run / "merged_candidates.csv", candidates, CANDIDATE_FIELDS)
    write_csv(run / "unresolved_references.csv", unresolved, RECORD_FIELDS + ["unresolved_id", "reason"])
    write_csv(run / "deduplication_decisions.csv", decisions, ["left", "right", "decision", "evidence", "policy_version"])
    write_csv(run / "identity_mapping.csv", [{"record_id": rid, "candidate_id": cid} for rid, cid in sorted(mapping.items())], ["record_id", "candidate_id"])
    write_csv(run / "seed_resolution.csv", [{**op.get("resolution", {}), "seed_id": op["seed_id"], "provider": op["provider"], "direction": op["direction"], "status": op["status"] if not op["resolution"] else "ok"} for op in operations], ["seed_id", "provider", "direction", "record_id", "method", "status"])
    write_csv(run / "manual_action_queue.csv", actions, ACTION_FIELDNAMES)
    write_csv(run / "missing_pdfs.csv", [c for c in candidates if c["has_local_pdf"] == "no"], ["candidate_id", "title", "authors", "year", "doi", "canonical_url"])
    atomic_text(run / "bibtex_update.bib", "\n\n".join(record_to_bibtex(c) for c in candidates if all(c.get(k) for k in ("title", "authors", "year"))) + "\n")
    status = "ok" if all(op["status"] in ("ok", "empty") and op["complete"] and not op["count_mismatch"] for op in operations) else "partial"
    summary = {"status": status, "operations": operations, "observations": len(observations), "unique_edges": len(edges),
               "candidates": len(candidates), "new_candidates": sum(c["already_known"] == "no" and c["is_seed"] == "no" for c in candidates),
               "already_known": sum(c["already_known"] == "yes" for c in candidates), "unresolved_records": len(unresolved),
               "duplicate_observations_collapsed": sum(e["observation_count"] - 1 for e in edges.values()),
               "asset_reconciliation": "current local library (may change independently of raw citation snapshot)"}
    atomic_json(run / "validation.json", summary)
    lines = ["# Snowballing validation", "", f"Status: {status}", "", f"{len(observations)} observations; {len(edges)} unique edges; {len(candidates)} candidates.", "", "Provider totals are compared with observations before candidate/edge deduplication.", "Unknown totals do not establish completeness of the real-world citation graph.", "", "| Provider | Seed | Direction | Status | Reported | Imported | Mismatch |", "|---|---|---|---|---|---|---|"]
    lines.extend(f"| {op['provider']} | {op['seed_id']} | {op['direction']} | {op['status']} | {op['source_reported_count']} | {op['imported_count']} | {op['count_mismatch']} |" for op in operations)
    lines += ["", "Attribution: OpenAlex, Semantic Scholar, and OpenCitations for enabled provider contributions; see provider_capabilities.json for terms.", ""]
    atomic_text(run / "validation_report.md", "\n".join(lines))
    atomic_text(run / "summary.md", "\n".join(lines))
    return summary


def inspect_library(library, project, candidates, actions):
    bib = library / "bibtex/candidates.bib"
    entries = {e["key"]: parse_bibtex_fields(e["body"]) for e in iter_bibtex_entries(bib.read_text())} if bib.exists() else {}
    pdfs = {r["candidate_id"]: r for r in read_csv(library / "manifests/pdfs.csv")}
    for c in candidates:
        pdf = pdfs.get(c["candidate_id"], {}).get("pdf_path", "")
        path = Path(pdf) if pdf else library / "pdfs" / (c["candidate_id"] + ".pdf")
        if not path.is_absolute():
            path = project / path
        exists = path.is_file()
        c.update(has_local_pdf="yes" if exists else "no", pdf_path=str(path) if exists else "", has_bibtex="yes" if c["bibtex_key"] in entries else "no")
        if not exists:
            actions.append({**c, "action": "missing_local_pdf", "reason": "No registered or candidate-named local PDF"})
        if not all(entries.get(c["bibtex_key"], {}).get(k) for k in ("title", "author", "year")):
            actions.append({**c, "action": "missing_bibtex", "reason": "Missing or incomplete bibliographic metadata"})


def publish_library(library, project, run, candidates, records, mapping, actions):
    with lock(library / "manifests/.snowball.lock"):
        registry_path = library / "manifests/snowball_identity.json"
        existing = json.loads(registry_path.read_text()) if registry_path.exists() else []
        candidate_by_id = {c["candidate_id"]: c for c in candidates}
        registry = {r["record_id"]: r for r in existing}
        for rid, cid in mapping.items():
            if cid not in candidate_by_id:
                continue
            if rid in registry and registry[rid]["candidate_id"] != cid:
                raise ValueError("Library identity changed since preparation; create a new run")
            r = records[rid]
            registry[rid] = {**r, "candidate_id": cid, "bibtex_key": candidate_by_id[cid]["bibtex_key"]}
        # Do not overwrite a citation key now occupied by another DOI.
        bib = library / "bibtex/candidates.bib"
        text = bib.read_text() if bib.exists() else ""
        entries = {e["key"]: parse_bibtex_fields(e["body"]) for e in iter_bibtex_entries(text)}
        for c in candidates:
            old = entries.get(c["bibtex_key"])
            if old and old.get("doi") and c.get("doi") and normalize_doi(old["doi"]) != c["doi"]:
                raise ValueError("BibTeX key now refers to another DOI; resolve identity before publication")
            for key, fields in entries.items():
                if c.get("doi") and normalize_doi(fields.get("doi", "")) == c["doi"] and key != c["bibtex_key"]:
                    raise ValueError("A DOI acquired another library key after preparation; reprepare with current identity state")
        additions = [record_to_bibtex(c) for c in candidates if c["bibtex_key"] not in entries and all(c.get(k) for k in ("title", "authors", "year"))]
        if additions:
            atomic_text(bib, text.rstrip() + ("\n\n" if text.strip() else "") + "\n\n".join(additions) + "\n")
        atomic_json(registry_path, sorted(registry.values(), key=lambda r: r["record_id"]))
        (library / "pdfs").mkdir(parents=True, exist_ok=True)
        inspect_library(library, project, candidates, actions)
        # Record integrity for any local files discovered by candidate filename.
        manifest_path = library / "manifests/pdfs.csv"
        pdfs = {r["candidate_id"]: r for r in read_csv(manifest_path)}
        for c in candidates:
            if c["has_local_pdf"] == "yes":
                path = Path(c["pdf_path"])
                pdfs[c["candidate_id"]] = {**pdfs.get(c["candidate_id"], {}), "candidate_id": c["candidate_id"],
                    "pdf_path": str(path.relative_to(project)) if path.is_relative_to(project) else str(path),
                    "checksum": sha256_file(path), "file_size": str(path.stat().st_size),
                    "match_method": pdfs.get(c["candidate_id"], {}).get("match_method", "filename_candidate_id")}
        write_csv(manifest_path, sorted(pdfs.values(), key=lambda r: r["candidate_id"]), ["candidate_id", "pdf_path", "checksum", "file_size", "match_method", "note"])
