"""Quality evaluation distinguishes reviewed correctness from provider overlap."""
from collections import defaultdict
from itertools import combinations
import json
from pathlib import Path

from ..files import read_csv, write_csv
from ..identity import normalize_doi, title_fingerprint
from .models import atomic_json, atomic_text, digest
from .runner import verify

REVIEW_FIELDS = ["observation_id", "edge_id", "provider", "direction", "overlap", "citing_doi", "cited_doi", "citing_title", "cited_title", "verdict", "reviewer", "reviewed_at", "evidence", "notes"]


def evaluate(run, *, baseline=None):
    run = Path(run)
    config = verify(run)
    if not config["smoke"]:
        raise ValueError("Quality smoke evaluation requires an isolated smoke run")
    bench_path = run / "benchmark/benchmark.json"
    benchmark = json.loads(bench_path.read_text()) if bench_path.exists() else {}
    validation = json.loads((run / "validation.json").read_text())
    candidates = {r["candidate_id"]: r for r in read_csv(run / "merged_candidates.csv")}
    records = {r["record_id"]: r for r in read_csv(run / "source_records.csv")}
    observations = read_csv(run / "citation_observations.csv")
    edges = read_csv(run / "citation_edges.csv")
    seeds = json.loads((run / "seed_snapshot.json").read_text())
    seed_dois = {s["record_id"]: normalize_doi(s.get("doi", "")) for s in seeds}
    expected = [e for e in read_csv(run / "benchmark/expected_edges.csv") if e.get("verified") == "yes" and all(e.get(k) for k in ("reviewer", "reviewed_at", "evidence", "citing_doi", "cited_doi"))]
    metadata = [r for r in read_csv(run / "benchmark/expected_metadata.csv") if r.get("verified") == "yes" and all(r.get(k) for k in ("reviewer", "reviewed_at", "evidence", "doi"))]
    expected_pairs = {(normalize_doi(e["citing_doi"]), normalize_doi(e["cited_doi"])) for e in expected}

    def pair(o):
        return tuple(normalize_doi(candidates.get(o[k + "_candidate_id"], {}).get("doi", "")) for k in ("citing", "cited"))

    edge_providers = defaultdict(set)
    for o in observations:
        if o["edge_id"]:
            edge_providers[o["edge_id"]].add(o["provider"])
    strata = defaultdict(list)
    for o in observations:
        overlap = "shared" if len(edge_providers[o["edge_id"]]) > 1 else "unique"
        strata[(o["provider"], o["direction"], overlap)].append(o)
    selected = []
    sample_seed, size = benchmark.get("sampling_seed", 42), benchmark.get("sample_per_stratum", 5)
    if not isinstance(size, int) or size < 1:
        raise ValueError("sample_per_stratum must be a positive integer")
    sample_path = run / "review_sample.json"
    frozen = set(json.loads(sample_path.read_text())["observation_ids"]) if sample_path.exists() else None
    for (_, _, overlap), rows in sorted(strata.items()):
        # Sample distinct edges in each stratum, retaining unresolved observations.
        distinct = {}
        for o in sorted(rows, key=lambda o: o["observation_id"]):
            distinct.setdefault(o["edge_id"] or o["observation_id"], o)
        sampled_rows = [o for o in rows if o["observation_id"] in frozen] if frozen is not None else sorted(distinct.values(), key=lambda o: digest([sample_seed, o["observation_id"]]))[:size]
        for o in sampled_rows:
            a, b = pair(o)
            selected.append({**{k: o[k] for k in ("observation_id", "edge_id", "provider", "direction")},
                             "overlap": overlap, "citing_doi": a, "cited_doi": b,
                             "citing_title": candidates.get(o["citing_candidate_id"], {}).get("title", ""),
                             "cited_title": candidates.get(o["cited_candidate_id"], {}).get("title", ""), "verdict": "unverified"})
    if frozen is not None and frozen != {r["observation_id"] for r in selected}:
        raise ValueError("Frozen sample refers to observations missing from this snapshot")
    review_path = run / "reference_review.csv"
    review = read_csv(review_path)
    if review:
        if {r["observation_id"] for r in review} != {r["observation_id"] for r in selected} or len(review) != len(selected):
            raise ValueError("Frozen review sample changed; retain its observation IDs")
        # Keep evidence and verdicts, but use canonical identifiers from the frozen sample.
        existing = {r["observation_id"]: r for r in review}
        review = [{**r, **{k: existing[r["observation_id"]].get(k, "") for k in ("verdict", "reviewer", "reviewed_at", "evidence", "notes")}} for r in selected]
    else:
        review = selected
    write_csv(review_path, review, REVIEW_FIELDS)
    if not sample_path.exists() and selected:
        atomic_json(sample_path, {"sampling_seed": sample_seed, "sample_per_stratum": size, "observation_ids": sorted(r["observation_id"] for r in selected)})
    metrics, failures, pending = [], [], []

    def metric(provider, direction, name, numerator, denominator, notes=""):
        metrics.append({"provider": provider, "direction": direction, "metric": name,
                        "numerator": numerator, "denominator": denominator,
                        "value": numerator / denominator if denominator else "", "notes": notes})

    threshold = benchmark.get("minimum_known_edge_recovery")
    precision_threshold = benchmark.get("minimum_sample_correctness")
    for value in (threshold, precision_threshold):
        if value is not None and not 0 <= value <= 1:
            raise ValueError("Quality thresholds must be between 0 and 1")
    if not expected or not metadata:
        pending.append("Independently reviewed expected edges and metadata are required")
    if threshold is None or precision_threshold is None:
        pending.append("Quality thresholds are not calibrated in benchmark.json")
    if validation["status"] != "ok":
        pending.append("At least one required provider/direction was incomplete or failed")
    if any(op["status"] in ("wrong_seed", "wrong_direction") for op in validation["operations"]):
        failures.append("Known wrong seed or citation direction")
    if len({e["edge_id"] for e in edges}) != len(edges):
        failures.append("Duplicate canonical edges")
    if len({o["observation_id"] for o in observations}) != len(observations):
        failures.append("Duplicate observations")
    if any(e[k] not in candidates for e in edges for k in ("citing_candidate_id", "cited_candidate_id")):
        failures.append("Dangling canonical citation endpoint")
    if any(e["citing_candidate_id"] == e["cited_candidate_id"] for e in edges):
        pending.append("A paper cites itself as the same candidate; inspect identity and version matching")
    if any(a["action"] in ("review_duplicate", "unresolved_reference", "incomplete_metadata") for a in read_csv(run / "manual_action_queue.csv")):
        pending.append("Identity or metadata actions require review")

    for provider in config["providers"] + ["union"]:
        ops = [op for op in validation["operations"] if provider == "union" or op["provider"] == provider]
        metric(provider, "both", "resolved_seed_operations", sum(bool(op["resolution"]) for op in ops), len(ops))
        for direction in config["directions"]:
            obs = [o for o in observations if o["direction"] == direction and (provider == "union" or o["provider"] == provider)]
            expected_here = {p for p in expected_pairs if p[0 if direction == "backward" else 1] in set(seed_dois.values()) - {""}}
            returned = {pair(o) for o in obs}
            recovered = len(expected_here & returned)
            metric(provider, direction, "known_edge_recovery", recovered, len(expected_here), "Benchmark recovery, not global recall; see operation completeness")
            if not expected_here:
                pending.append(f"No reviewed expected edges for {provider}/{direction}")
            elif threshold is not None and validation["status"] == "ok" and recovered / len(expected_here) < threshold:
                failures.append(f"Known-edge recovery below threshold: {provider}/{direction}")
            sampled = [r for r in review if r["direction"] == direction and (provider == "union" or r["provider"] == provider)]
            judged = [r for r in sampled if r.get("verdict") in ("correct", "incorrect") and all(r.get(k) for k in ("reviewer", "reviewed_at", "evidence"))]
            correct = sum(r["verdict"] == "correct" for r in judged)
            metric(provider, direction, "sample_correctness", correct, len(judged), "Reviewed observations only")
            metric(provider, direction, "sample_unverified", len(sampled) - len(judged), len(sampled))
            if len(judged) < len(sampled) or not judged:
                pending.append(f"Reference sample review incomplete: {provider}/{direction}")
            if judged and precision_threshold is not None and correct / len(judged) < precision_threshold:
                failures.append(f"Sample correctness below threshold: {provider}/{direction}")
            endpoint_ids = {o[k] for o in obs for k in ("citing_record_id", "cited_record_id")}
            source_rows = [records[rid] for rid in endpoint_ids if rid in records]
            for field in ("title", "authors", "year", "doi", "canonical_url"):
                metric(provider, direction, field + "_available", sum(bool(r.get(field)) for r in source_rows), len(source_rows))
            verified = {normalize_doi(r["doi"]): r for r in metadata}
            for field in ("title", "authors", "year"):
                comparisons = [(r, verified[normalize_doi(r["doi"])]) for r in source_rows if normalize_doi(r.get("doi", "")) in verified and verified[normalize_doi(r["doi"])].get(field)]
                normalize = title_fingerprint if field == "title" else lambda s: " ".join(s.lower().split())
                correct_values = sum(normalize(r.get(field, "")) == normalize(v[field]) for r, v in comparisons)
                metric(provider, direction, field + "_agreement", correct_values, len(comparisons))
                if correct_values != len(comparisons):
                    pending.append(f"Reviewed metadata disagrees for {provider}/{direction}/{field}")
    overlap_rows = []
    for a, b in combinations(config["providers"], 2):
        for direction in config["directions"]:
            resolved = {p: {op["seed_id"] for op in validation["operations"] if op["provider"] == p and op["direction"] == direction and op["resolution"]} for p in (a, b)}
            comparable = resolved[a] & resolved[b]
            sets = {p: {o["edge_id"] for o in observations if o["provider"] == p and o["direction"] == direction and o["seed_id"] in comparable and o["edge_id"]} for p in (a, b)}
            overlap_rows.append({"provider_a": a, "provider_b": b, "direction": direction, "comparable_seeds": len(comparable),
                                 "shared": len(sets[a] & sets[b]), "only_a": len(sets[a] - sets[b]), "only_b": len(sets[b] - sets[a]),
                                 "union": len(sets[a] | sets[b]), "note": "Provider disagreement is not evidence of an incorrect citation; inspect completeness"})
    metric("union", "both", "collapsed_repeated_edges", validation["duplicate_observations_collapsed"], len(observations))
    metric("union", "both", "unresolved_endpoints", sum(not o["edge_id"] for o in observations), len(observations))
    duplicate_dois = defaultdict(list)
    for c in candidates.values():
        if c.get("doi"):
            duplicate_dois[c["doi"]].append(c["candidate_id"])
    residual = sum(len(ids) - 1 for ids in duplicate_dois.values())
    metric("union", "both", "residual_duplicate_dois", residual, len(candidates))
    if residual:
        pending.append("Unresolved duplicate DOI identities require review")
    if baseline:
        previous = read_csv(Path(baseline) / "citation_edges.csv")
        before = {e["edge_id"] for e in previous}
        after = {e["edge_id"] for e in edges}
        write_csv(run / "baseline_changes.csv", [{"edge_id": e, "change": "added"} for e in sorted(after - before)] + [{"edge_id": e, "change": "missing"} for e in sorted(before - after)], ["edge_id", "change"])
    write_csv(run / "quality_metrics.csv", metrics, ["provider", "direction", "metric", "numerator", "denominator", "value", "notes"])
    write_csv(run / "provider_overlap.csv", overlap_rows, ["provider_a", "provider_b", "direction", "comparable_seeds", "shared", "only_a", "only_b", "union", "note"])
    status = "fail" if failures else "inconclusive" if pending else "pass"
    report = {"status": status, "failures": sorted(set(failures)), "pending": sorted(set(pending)), "benchmark_version": benchmark.get("version", "unreviewed"), "observations": len(observations), "review_sample": len(review)}
    atomic_json(run / "quality_summary.json", report)
    atomic_text(run / "quality_summary.md", "# Reference quality smoke test\n\nStatus: **" + status + "**\n\n" + "\n".join("- " + s for s in report["failures"] + report["pending"]) + "\n\nInspect quality_metrics.csv, provider_overlap.csv, and reference_review.csv. Correctness requires independent evidence; coverage is limited to the dated provider snapshots.\n")
    return report
