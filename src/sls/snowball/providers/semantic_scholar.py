from urllib.parse import quote

from ...identity import normalize_doi
from ..models import source_record, observe
from .base import Provider, ProviderError

BASE = "https://api.semanticscholar.org/graph/v1/paper"
FIELDS = "paperId,externalIds,title,year,authors,venue,journal,referenceCount,citationCount"


def normalize(work, page, locator):
    doi = normalize_doi((work.get("externalIds") or {}).get("DOI") or "")
    journal = work.get("journal") or {}
    return source_record("semantic_scholar", work.get("paperId") or "", page, locator,
                         doi=doi, canonical_url="https://doi.org/" + doi if doi else "",
                         title=work.get("title") or "", year=str(work.get("year") or ""),
                         authors="; ".join(a.get("name", "") for a in work.get("authors", [])),
                         venue=work.get("venue") or journal.get("name") or "",
                         volume=journal.get("volume") or "", pages=journal.get("pages") or "")


class SemanticScholar(Provider):
    name = "semantic_scholar"

    def resolve(self, seed):
        identity = seed.get("semantic_scholar_id") or ("DOI:" + normalize_doi(seed["doi"]) if seed.get("doi") else "")
        if not identity:
            raise ProviderError("unresolved_seed", "Semantic Scholar resolution needs a DOI or explicit paper ID")
        page = self.client.get(BASE + "/" + quote(identity, safe=":"), {"fields": FIELDS})
        return normalize(page.data, page, "$"), page

    def collect(self, result, seed, seed_page):
        backward = result.direction == "backward"
        endpoint, field, count = ("references", "citedPaper", "referenceCount") if backward else ("citations", "citingPaper", "citationCount")
        result.reported_count = seed_page.data.get(count)
        offset, seen = 0, set()
        while offset is not None:
            if offset in seen:
                raise ProviderError("pagination_error", "Repeated Semantic Scholar offset")
            seen.add(offset)
            page = self.client.get(BASE + "/" + quote(seed["source_record_id"], safe="") + "/" + endpoint,
                                   {"fields": FIELDS, "limit": min(self.client.config["page_size"], 1000), "offset": offset})
            for i, item in enumerate(page.data["data"]):
                if self.capped(result):
                    return
                locator = f"data[{i}].{field}"
                observe(result, self.name, seed, normalize(item.get(field) or {}, page, locator), page, locator)
            offset = page.data.get("next")
            if offset is not None and self.capped(result):
                return
        result.complete = True
