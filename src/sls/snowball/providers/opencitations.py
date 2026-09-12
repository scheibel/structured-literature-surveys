import re
from urllib.parse import quote

from ...identity import normalize_doi
from ..models import source_record, observe
from .base import Provider, ProviderError

BASE = "https://api.opencitations.net/index/v2"
META = "https://api.opencitations.net/meta/v1/metadata/"


def identifiers(value):
    return re.findall(r"(?:doi|omid|pmid|openalex):[^\s;]+", value or "")


def preferred(value):
    ids = identifiers(value)
    return next((i for scheme in ("doi:", "omid:", "pmid:") for i in ids if i.startswith(scheme)), "")


def normalize(work, page, locator):
    ids = identifiers(work.get("id", ""))
    doi = next((normalize_doi(i[4:]) for i in ids if i.startswith("doi:")), "")
    clean = lambda x: re.sub(r"\s*\[[^\]]*\]", "", x or "").strip()
    return source_record("opencitations", preferred(work.get("id", "")), page, locator,
                         identifiers=";".join(ids), doi=doi, canonical_url="https://doi.org/" + doi if doi else "",
                         title=work.get("title") or "", authors=clean(work.get("author")), year=(work.get("pub_date") or "")[:4],
                         venue=clean(work.get("venue")), publisher=clean(work.get("publisher")),
                         volume=work.get("volume") or "", number=work.get("issue") or "", pages=work.get("page") or "")


class OpenCitations(Provider):
    name = "opencitations"

    def metadata(self, identity):
        page = self.client.get(META + quote(identity, safe=":/"))
        matches = [(i, item) for i, item in enumerate(page.data) if identity in identifiers(item.get("id", ""))]
        if len(matches) != 1:
            raise ProviderError("not_found" if not matches else "ambiguous_seed", "OpenCitations Meta did not return one matching entity")
        i, item = matches[0]
        return normalize(item, page, f"[{i}]"), page

    def resolve(self, seed):
        identity = seed.get("opencitations_id") or ("doi:" + normalize_doi(seed["doi"]) if seed.get("doi") else "")
        if not identity:
            raise ProviderError("unresolved_seed", "OpenCitations resolution needs a DOI or explicit identifier")
        return self.metadata(identity)

    def collect(self, result, seed, seed_page):
        backward = result.direction == "backward"
        endpoint = "references" if backward else "citations"
        page = self.client.get(BASE + "/" + endpoint + "/" + quote(seed["source_record_id"], safe=":/"))
        if not isinstance(page.data, list):
            raise ProviderError("schema_error", "OpenCitations relationships must be a list")
        # Index v2 returns a list, not a cursor-paginated collection. Its list size
        # is not an independently reported total; leave reported_count unknown.
        failures = []
        seed_ids = set(seed.get("identifiers", "").split(";")) | {seed["source_record_id"]}
        for i, item in enumerate(page.data):
            if self.capped(result):
                return
            seed_end = item.get("citing" if backward else "cited", "")
            if not seed_ids.intersection(identifiers(seed_end)):
                raise ProviderError("wrong_direction", "Citation endpoint does not identify the requested seed")
            value = item.get("cited" if backward else "citing", "")
            identity = preferred(value)
            try:
                if not identity:
                    raise ProviderError("unresolved_metadata", "Missing endpoint identifier")
                other, _ = self.metadata(identity)
            except ProviderError as exc:
                failures.append(exc.status)
                doi = normalize_doi(identity[4:]) if identity.startswith("doi:") else ""
                other = source_record(self.name, identity, page, f"[{i}].endpoint", identifiers=";".join(identifiers(value)), doi=doi, canonical_url="https://doi.org/" + doi if doi else "")
            observe(result, self.name, seed, other, page, f"[{i}]", item.get("oci", ""))
        result.complete = True
        if failures:
            result.status, result.reason = "partial_metadata", ", ".join(sorted(set(failures)))
