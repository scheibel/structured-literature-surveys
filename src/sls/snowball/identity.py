"""Conservative evidence reconciliation; never use missing values as keys."""
from collections import defaultdict
import json
from urllib.parse import urlsplit, urlunsplit

from ..identity import ascii_slug, candidate_id_base, normalize_doi, title_fingerprint
from ..metadata import SOURCE_FIELDNAMES
from .models import digest, POLICY_VERSION


def canonical_url(value):
    parsed = urlsplit((value or "").strip())
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        return ""
    # Hostnames are case insensitive; paths and query strings are not.
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))


def keys(record):
    result = {}
    doi = normalize_doi(record.get("doi", ""))
    url = canonical_url(record.get("canonical_url", ""))
    if doi:
        result["doi"] = doi
    if url:
        result["url"] = url
    if record.get("title") and record.get("year"):
        result["title_year"] = title_fingerprint(record["title"]) + ":" + str(record["year"])
    if record.get("source") and record.get("source_record_id"):
        result["provider_id"] = record["source"] + ":" + record["source_record_id"]
    if record.get("candidate_id"):
        result["candidate_id"] = record["candidate_id"]
    return result


def reconcile(records, overrides=()):
    records = sorted({r["record_id"]: dict(r) for r in records}.values(), key=lambda r: r["record_id"])
    by_id = {r["record_id"]: i for i, r in enumerate(records)}
    parent = list(range(len(records)))
    members = {i: {i} for i in parent}
    decisions, blocked = [], set()

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def values(group, field):
        return {records[i].get(field, "") for i in members[group]} - {""}

    def join(a, b, method, manual=False):
        a, b = root(a), root(b)
        if a == b:
            return
        left, right = records[min(members[a])]["record_id"], records[min(members[b])]["record_id"]
        conflict = len({normalize_doi(v) for v in values(a, "doi") | values(b, "doi")}) > 1
        id_conflict = len(values(a, "candidate_id") | values(b, "candidate_id")) > 1
        separated = any(frozenset((i, j)) in blocked for i in members[a] for j in members[b])
        decision = "merge"
        reason = method
        if separated or id_conflict or (conflict and not manual):
            decision = "review"
            reason = "manual_separation" if separated else "existing_id_conflict" if id_conflict else "conflicting_doi"
        decisions.append({"left": left, "right": right, "decision": decision, "evidence": reason, "policy_version": POLICY_VERSION})
        if decision == "merge":
            a, b = sorted((a, b))
            parent[b] = a
            members[a] |= members.pop(b)

    for o in overrides:
        if not all(o.get(k) for k in ("left", "right", "decision", "actor", "timestamp", "reason")):
            raise ValueError("Overrides require left/right record IDs, decision, actor, timestamp, reason")
        if o["left"] not in by_id or o["right"] not in by_id:
            raise ValueError("Override refers to an unknown record ID")
        if o["decision"] == "separate":
            blocked.add(frozenset((by_id[o["left"]], by_id[o["right"]])))
        elif o["decision"] != "merge":
            raise ValueError("Override decision must be merge or separate")
    for o in overrides:
        if o["decision"] == "merge":
            join(by_id[o["left"]], by_id[o["right"]], "manual:" + o["reason"], True)

    for method in ("candidate_id", "doi", "provider_id", "url", "title_year"):
        index = defaultdict(list)
        for i, r in enumerate(records):
            value = keys(r).get(method)
            if value:
                index[value].append(i)
        for value, ids in sorted(index.items()):
            groups = sorted({root(i) for i in ids})
            # Weak evidence must not bridge multiple incompatible strong identities.
            dois = {normalize_doi(d) for g in groups for d in values(g, "doi")}
            ambiguous = method in ("url", "title_year") and len(dois) > 1
            if method == "title_year" and len(groups) > 1:
                author_sets = [{ascii_slug(r.get("authors", "").split(";")[0]) for i in members[g] if (r := records[i]).get("authors")} for g in groups]
                ambiguous |= not all(author_sets) or not set.intersection(*author_sets)
            if ambiguous:
                for i in ids[1:]:
                    decisions.append({"left": records[ids[0]]["record_id"], "right": records[i]["record_id"], "decision": "review", "evidence": "ambiguous_" + method, "policy_version": POLICY_VERSION})
                continue
            for i in ids[1:]:
                join(ids[0], i, method)

    candidates, mapping, unresolved = [], {}, []
    used = {r.get("candidate_id") for r in records if r.get("candidate_id")}
    planned = []
    for group in members.values():
        rows = sorted((records[i] for i in group), key=lambda r: (not bool(r.get("candidate_id")), not bool(r.get("doi")), r["record_id"]))
        origins, conflicts, candidate = {}, {}, {}
        for field in SOURCE_FIELDNAMES:
            choices = [(r[field], r["record_id"]) for r in rows if r.get(field)]
            candidate[field] = choices[0][0] if choices else ""
            origins[field] = [rid for v, rid in choices if v == candidate[field]]
            if len({v for v, _ in choices}) > 1:
                conflicts[field] = choices
        ids = [r["record_id"] for r in rows]
        if not candidate.get("doi") and not candidate.get("title") and not candidate.get("canonical_url"):
            uid = "unresolved" + digest(ids)[:16]
            for r in rows:
                mapping[r["record_id"]] = uid
                unresolved.append({**r, "unresolved_id": uid, "reason": "No resolvable bibliographic metadata"})
            continue
        candidate["doi"] = normalize_doi(candidate.get("doi", ""))
        pinned = next((r.get("candidate_id") for r in rows if r.get("candidate_id")), "")
        evidence = sorted({f"{k}:{v}" for r in rows for k, v in keys(r).items()})
        base = pinned or candidate_id_base(candidate)
        planned.append((base, digest(evidence)[:12], pinned, candidate, rows, origins, conflicts))
    counts = defaultdict(int)
    for base, *_ in planned:
        counts[base] += 1
    for base, suffix, pinned, candidate, rows, origins, conflicts in planned:
        if pinned and counts[base] > 1:
            raise ValueError("Existing candidate ID has conflicting identities; resolve the library manifest first")
        cid = pinned or (base if counts[base] == 1 and base not in used else base + suffix)
        if not pinned and cid in used:
            raise ValueError("Candidate ID collision requires a recorded override")
        used.add(cid)
        member_ids = {r["record_id"] for r in rows}
        candidate.update(candidate_id=cid, bibtex_key=next((r.get("bibtex_key") for r in rows if r.get("bibtex_key")), cid),
                         already_known="yes" if any(r.get("already_known") == "yes" for r in rows) else "no",
                         is_seed="yes" if any(r.get("is_seed") == "yes" for r in rows) else "no",
                         sources=";".join(sorted({r.get("source", "") for r in rows} - {""})),
                         source_count=str(len(rows)), source_record_ids=";".join(sorted({r.get("source_record_id", "") for r in rows} - {""})),
                         manual_review="yes" if any(d["decision"] == "review" and member_ids.intersection((d["left"], d["right"])) for d in decisions) else "no",
                         field_provenance=json.dumps(origins, sort_keys=True), metadata_conflicts=json.dumps(conflicts, sort_keys=True),
                         dedupe_key=next((k + ":" + keys(candidate)[k] for k in ("doi", "url", "title_year") if k in keys(candidate)), ""), dedupe_confidence="recorded_evidence",
                         has_local_pdf="no", has_bibtex="no", pdf_path="")
        candidates.append(candidate)
        mapping.update({r["record_id"]: cid for r in rows})
    return sorted(candidates, key=lambda c: c["candidate_id"]), mapping, decisions, unresolved
