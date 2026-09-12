from __future__ import annotations

from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

from ...identity import normalize_doi, title_fingerprint
from ..models import Page, Result, atomic_json, atomic_text, digest, now

ENV_NAMES = {"openalex": "OPENALEX_API_KEY", "semantic_scholar": "SEMANTIC_SCHOLAR_API_KEY", "opencitations": "OPENCITATIONS_ACCESS_TOKEN"}
HOSTS = {"openalex": "api.openalex.org", "semantic_scholar": "api.semanticscholar.org", "opencitations": "api.opencitations.net"}


class ProviderError(Exception):
    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("error", "Unexpected API redirect; review the documented endpoint")


class Client:
    """GET-only transport with immutable redacted evidence and offline replay.

    Request budgets count attempts (including retries) across resumes. Cached
    successes are never refreshed; a fresh snapshot requires a new run.
    """
    def __init__(self, run: Path, provider: str, config: dict, *, offline=False):
        self.run, self.provider, self.config, self.offline = run, provider, config, offline
        self.root = run / "sources" / provider
        self.journal = self.root / "requests.json"
        self.entries = json.loads(self.journal.read_text()) if self.journal.exists() else []
        self.last_request = 0.0
        self.secret = os.environ.get(config["credential_env"][provider], "") if not offline else ""
        self.opener = build_opener(NoRedirect())
        source_root = Path(__file__).parents[2]
        self.code_digest = digest({str(p.relative_to(source_root)): digest(p.read_bytes()) for p in sorted(source_root.rglob("*.py"))})

    def get(self, url: str, params: dict | None = None) -> Page:
        if urlsplit(url).scheme != "https" or urlsplit(url).hostname != HOSTS[self.provider]:
            raise ProviderError("error", "Request outside configured API host")
        params = {k: str(v) for k, v in (params or {}).items()}
        public_url = url + (("&" if "?" in url else "?") + urlencode(sorted(params.items())) if params else "")
        request_id = digest(["GET", public_url])[:24]
        matches = [e for e in self.entries if e["request_id"] == request_id]
        successes = [e for e in matches if e["status"] == "ok"]
        if successes:
            e = successes[0]
            path = self.run / e["raw_path"]
            body = path.read_bytes()
            if digest(body) != e["checksum"]:
                raise ValueError(f"Raw artifact checksum mismatch: {path}")
            return Page(json.loads(body), request_id, e["raw_path"], e["retrieved_at"], public_url)
        if self.offline:
            status = matches[-1]["status"] if matches else "budget_exhausted" if len(self.entries) >= self.config["request_budgets"][self.provider] else "not_collected"
            raise ProviderError(status, "No successful recorded response for this request")
        if matches and matches[-1]["status"] == "not_found":
            raise ProviderError("not_found", "Provider did not find the identifier")
        if any(e.get("retry_after_until", 0) > time.time() for e in self.entries):
            raise ProviderError("rate_limited", "Provider Retry-After is still active; resume later")

        headers = {"Accept": "application/json", "User-Agent": "structured-literature-surveys/0.2 (citation metadata research)"}
        wire_url = public_url
        if self.secret:
            if self.provider == "openalex":
                wire_url += ("&" if "?" in wire_url else "?") + urlencode({"api_key": self.secret})
            else:
                headers["x-api-key" if self.provider == "semantic_scholar" else "authorization"] = self.secret
        for attempt in range(self.config["retries"] + 1):
            if len(self.entries) >= self.config["request_budgets"][self.provider]:
                raise ProviderError("budget_exhausted", "Per-provider request budget exhausted; create a new run with a larger budget")
            interval = max(1.0, self.config["request_interval"])
            time.sleep(max(0, interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            entry = {"request_id": request_id, "method": "GET", "url": public_url,
                     "credential_env": self.config["credential_env"][self.provider],
                     "code_digest": self.code_digest,
                     "attempt": len(matches) + attempt + 1, "retrieved_at": now(), "status": "interrupted"}
            self.entries.append(entry)
            atomic_json(self.journal, self.entries)
            delay = min(2 ** attempt, 30)
            try:
                with self.opener.open(Request(wire_url, headers=headers), timeout=self.config["timeout"]) as response:
                    body = response.read().decode("utf-8")
                # Preserve response text except credentials if a provider echoes them.
                if self.secret:
                    for token in {self.secret, quote(self.secret, safe=""), urlencode({"k": self.secret})[2:]}:
                        body = body.replace(token, "[REDACTED]")
                body = re.sub(r'("(?:api_key|apikey|x-api-key|authorization|access_token)"\s*:\s*)"(?:\\.|[^"\\])*"', r'\1"[REDACTED]"', body, flags=re.I)
                data = json.loads(body)
                checksum = digest(body.encode())
                path = self.root / "raw" / f"{request_id}-{checksum}.json"
                if not path.exists():
                    atomic_text(path, body)
                entry.update(status="ok", checksum=checksum, raw_path=str(path.relative_to(self.run)))
                atomic_json(self.journal, self.entries)
                return Page(data, request_id, entry["raw_path"], entry["retrieved_at"], public_url)
            except HTTPError as exc:
                entry["http_status"] = exc.code
                entry["status"] = {401: "missing_credentials", 403: "access_denied", 404: "not_found", 429: "rate_limited"}.get(exc.code, "error")
                retry = exc.code == 429 or exc.code >= 500
                retry_after = exc.headers.get("Retry-After", "")
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        try:
                            delay = max(delay, parsedate_to_datetime(retry_after).timestamp() - time.time())
                        except (ValueError, TypeError):
                            pass
                    entry["retry_after_until"] = time.time() + max(0, delay)
            except (URLError, TimeoutError, OSError, ValueError, ProviderError) as exc:
                entry["status"], retry = "error", False
                entry["error_type"] = type(exc).__name__
                entry["transport_reason_type"] = type(getattr(exc, "reason", None)).__name__
            atomic_json(self.journal, self.entries)
            # Long provider backoffs are reported for a later resume, not ignored.
            if not retry or attempt == self.config["retries"] or delay > 60:
                raise ProviderError(entry["status"], f"API request failed ({entry.get('http_status', 'transport/schema')}); see requests.json")
            time.sleep(delay)
        raise AssertionError("unreachable")


def validate_seed(seed: dict, record: dict) -> str:
    """Strong identity mismatches are errors, weak/absent evidence is reviewable."""
    expected, actual = normalize_doi(seed.get("doi", "")), normalize_doi(record.get("doi", ""))
    if expected and actual:
        if expected != actual:
            raise ProviderError("wrong_seed", "Resolved DOI differs from seed DOI")
        return "doi"
    explicit = seed.get(record["source"] + "_id", "")
    if explicit and explicit in (record.get("source_record_id"), record.get("source_record_id", "").rsplit("/", 1)[-1]):
        return "provider_id"
    if seed.get("title") and seed.get("year") and title_fingerprint(seed["title"]) == title_fingerprint(record.get("title", "")) and str(seed["year"]) == str(record.get("year", "")):
        return "title_year"
    raise ProviderError("ambiguous_seed", "Insufficient evidence to verify resolved seed")


class Provider:
    name = ""

    def __init__(self, client: Client):
        self.client = client

    def run(self, seed: dict, direction: str) -> Result:
        result = Result(seed, direction)
        try:
            record, context = self.resolve(seed)
            method = validate_seed(seed, record)
            result.records.append(record)
            result.resolution = {"seed_id": seed["record_id"], "provider": self.name, "record_id": record["record_id"], "method": method, "status": "ok"}
            self.collect(result, record, context)
            if result.complete and not result.observations:
                result.status = "empty"
        except ProviderError as exc:
            result.status, result.reason = exc.status, str(exc)
        except (TypeError, KeyError, AttributeError, ValueError) as exc:
            result.status, result.reason = "schema_error", f"Unexpected response shape ({type(exc).__name__})"
        return result

    def capped(self, result: Result) -> bool:
        cap = self.client.config["max_relationships"]
        if cap is not None and len(result.observations) >= cap:
            result.status, result.reason = "capped", "Per-seed/direction relationship cap reached"
            return True
        return False
