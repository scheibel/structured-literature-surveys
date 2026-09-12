from urllib.parse import quote

from ...identity import normalize_doi
from ..models import source_record, observe
from .base import Provider, ProviderError

BASE = "https://api.openalex.org/works"
FIELDS = "id,doi,title,publication_year,authorships,primary_location,biblio,referenced_works,cited_by_count"


def normalize(work, page, locator):
    doi = normalize_doi(work.get("doi") or "")
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    biblio = work.get("biblio") or {}
    return source_record("openalex", work.get("id") or "", page, locator,
                         doi=doi, canonical_url="https://doi.org/" + doi if doi else (location.get("landing_page_url") or "") if source.get("type") in ("journal", "conference") else "",
                         title=work.get("title") or "", year=str(work.get("publication_year") or ""),
                         authors="; ".join((a.get("author") or {}).get("display_name", "") for a in work.get("authorships", [])),
                         venue=source.get("display_name") or "", volume=biblio.get("volume") or "", number=biblio.get("issue") or "",
                         pages="-".join(str(biblio[k]) for k in ("first_page", "last_page") if biblio.get(k)))


class OpenAlex(Provider):
    name = "openalex"

    def resolve(self, seed):
        identity = seed.get("openalex_id") or ("https://doi.org/" + normalize_doi(seed["doi"]) if seed.get("doi") else "")
        if not identity:
            raise ProviderError("unresolved_seed", "OpenAlex resolution needs a DOI or explicit OpenAlex ID")
        if identity.startswith("https://openalex.org/"):
            identity = identity.rsplit("/", 1)[-1]
        page = self.client.get(BASE + "/" + quote(identity, safe=":/"), {"select": FIELDS})
        return normalize(page.data, page, "$"), page

    def collect(self, result, seed, seed_page):
        if result.direction == "backward":
            refs = seed_page.data["referenced_works"]
            result.reported_count = len(refs)
            cap = self.client.config["max_relationships"]
            refs_to_get = refs if cap is None else refs[:cap]
            size = min(self.client.config["page_size"], 100)
            failures = []
            for offset in range(0, len(refs_to_get), size):
                batch = refs_to_get[offset:offset + size]
                try:
                    page = self.client.get(BASE, {"filter": "openalex:" + "|".join(i.rsplit("/", 1)[-1] for i in batch), "select": FIELDS, "per_page": len(batch)})
                    hydrated = {w["id"]: normalize(w, page, f"results[{i}]") for i, w in enumerate(page.data["results"])}
                except ProviderError as exc:
                    hydrated = {}
                    failures.append(exc.status)
                for index, identity in enumerate(batch, offset):
                    record = hydrated.get(identity) or source_record(self.name, identity, seed_page, f"referenced_works[{index}]")
                    if not record.get("title"):
                        failures.append("unresolved_metadata")
                    observe(result, self.name, seed, record, seed_page, f"referenced_works[{index}]")
            result.complete = len(refs_to_get) == len(refs)
            if not result.complete:
                result.status, result.reason = "capped", "Per-seed/direction relationship cap reached"
            elif failures:
                result.status, result.reason = "partial_metadata", ", ".join(sorted(set(failures)))
        else:
            cursor, seen = "*", set()
            while cursor:
                if cursor in seen:
                    raise ProviderError("pagination_error", "Repeated OpenAlex cursor")
                seen.add(cursor)
                page = self.client.get(BASE, {"filter": "cites:" + seed["source_record_id"].rsplit("/", 1)[-1], "select": FIELDS, "per_page": min(self.client.config["page_size"], 100), "cursor": cursor})
                result.reported_count = page.data["meta"].get("count")
                for i, work in enumerate(page.data["results"]):
                    if self.capped(result):
                        return
                    observe(result, self.name, seed, normalize(work, page, f"results[{i}]"), page, f"results[{i}]")
                cursor = page.data["meta"].get("next_cursor")
                if not page.data["results"]:
                    cursor = None
                if cursor and self.capped(result):
                    return
            result.complete = True
