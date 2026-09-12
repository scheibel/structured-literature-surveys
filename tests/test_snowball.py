import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit, parse_qs, unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sls.files import read_csv, write_csv
from sls.snowball.identity import reconcile, canonical_url
from sls.snowball.models import digest, atomic_json
from sls.snowball.providers.base import Client, ProviderError
from sls.snowball.runner import prepare, collect, verify
from sls.snowball.quality import evaluate
from sls.snowball.providers import PROVIDERS


class FixtureOpener:
    def __init__(self, provider):
        self.provider = provider
        self.fixture = json.loads((ROOT / "tests/fixtures/snowballing" / (provider + ".json")).read_text())
        self.calls = []

    def open(self, request, timeout):
        self.calls.append(request.full_url)
        parsed = urlsplit(request.full_url)
        path, query = unquote(parsed.path), parse_qs(parsed.query)
        works, edges = self.fixture["works"], self.fixture["edges"]
        by_key = dict(zip("abc", works))
        if self.provider == "openalex":
            if path == "/works":
                value = query["filter"][0]
                if value.startswith("openalex:"):
                    ids = value[len("openalex:"):].split("|")
                    data = {"results": [w for w in works if w["id"].rsplit("/", 1)[-1] in ids]}
                else:
                    target = "abc"[int(value[-1]) - 1]
                    found = [by_key[a] for a, b in edges if b == target]
                    cursor = query["cursor"][0]
                    offset = 0 if cursor == "*" else int(cursor)
                    size = int(query["per_page"][0])
                    data = {"results": found[offset:offset + size], "meta": {"count": len(found), "next_cursor": str(offset + size) if offset + size < len(found) else None}}
            else:
                key = path[-1]
                data = by_key[key] if key in by_key else works[int(key) - 1]
        elif self.provider == "semantic_scholar":
            if path.endswith(("/references", "/citations")):
                key, direction = path.split("/")[-2:]
                backward = direction == "references"
                found = [by_key[b if backward else a] for a, b in edges if (a if backward else b) == key]
                offset, size = int(query["offset"][0]), int(query["limit"][0])
                data = {"data": [{"citedPaper" if backward else "citingPaper": w} for w in found[offset:offset + size]]}
                if offset + size < len(found):
                    data["next"] = offset + size
            else:
                data = by_key[path[-1]]
        else:
            if "/metadata/" in path:
                key = path[-1]
                data = [by_key[key]] if key in by_key else [works[int(key) - 1]]
            else:
                key, backward = path[-1], "/references/" in path
                data = [{"oci": a + "-" + b, "citing": by_key[a]["id"], "cited": by_key[b]["id"]} for a, b in edges if (a if backward else b) == key]
        return io.BytesIO(json.dumps(data).encode())


class FixtureClient(Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.opener = FixtureOpener(self.provider)


def record(rid, doi="", title="Study", year="2020", authors="A Author", **kwargs):
    return {"record_id": rid, "source": "fixture", "source_record_id": rid, "title": title, "authors": authors, "year": year, "doi": doi, **kwargs}


class IdentityTests(unittest.TestCase):
    def test_multilevel_matching_and_order_independent_keys(self):
        rows = [record("a", "10.1234/a", canonical_url="https://publisher/X"), record("b", canonical_url="https://publisher/X")]
        first = reconcile(rows)
        second = reconcile(rows[::-1])
        self.assertEqual(len(first[0]), 1)
        self.assertEqual(first[1], second[1])

    def test_weak_matches_cannot_bridge_conflicting_dois(self):
        rows = [record("a", "10.1234/a"), record("b"), record("c", "10.1234/c")]
        candidates, mapping, decisions, _ = reconcile(rows)
        self.assertEqual(len(candidates), 3)
        self.assertTrue(any(d["decision"] == "review" for d in decisions))

    def test_empty_records_stay_unresolved(self):
        result = reconcile([record("a", title="", year="", authors=""), record("b", title="", year="", authors="")])
        self.assertEqual(len(result[0]), 0)
        self.assertEqual(len(result[3]), 2)
        self.assertNotEqual(result[1]["a"], result[1]["b"])

    def test_collision_suffixes_stable_and_existing_key_preserved(self):
        rows = [record("a", "10.1234/a"), record("b", "10.1234/b")]
        self.assertEqual(reconcile(rows)[1], reconcile(rows[::-1])[1])
        rows[0].update(candidate_id="customKey", bibtex_key="separateBibKey", already_known="yes")
        candidate = next(c for c in reconcile(rows)[0] if c["candidate_id"] == "customKey")
        self.assertEqual(candidate["bibtex_key"], "separateBibKey")

    def test_url_path_case_is_preserved(self):
        self.assertEqual(canonical_url("https://PUBLISHER.org/Article?X=Y#fragment"), "https://publisher.org/Article?X=Y")
        self.assertNotEqual(canonical_url("https://p.org/A"), canonical_url("https://p.org/a"))

    def test_manual_separation_survives_strong_match(self):
        rows = [record("a", "10.1234/a"), record("b", "10.1234/a")]
        override = {"left": "a", "right": "b", "decision": "separate", "actor": "tester", "timestamp": "2026-09-11", "reason": "Incorrect source DOI"}
        self.assertEqual(len(reconcile(rows, [override])[0]), 2)

    def test_conflicting_existing_id_cannot_silently_overwrite(self):
        with self.assertRaisesRegex(ValueError, "conflicting identities"):
            reconcile([record("a", "10.1234/a", candidate_id="pinned"), record("b", "10.1234/b", candidate_id="pinned")])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.sleep = patch("sls.snowball.providers.base.time.sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)
        self.seeds = self.project / "seeds.csv"
        write_csv(self.seeds, [{"doi": "10.1234/a"}, {"doi": "10.1234/b"}], ["doi"])

    def prepare(self, **kwargs):
        return prepare(project=self.project, seeds=self.seeds, slug="test", page_size=1, retries=0, **kwargs)

    def collect(self, run, **kwargs):
        return collect(run, project=self.project, client_class=FixtureClient, **kwargs)

    def test_overlap_three_providers_and_both_directions(self):
        run = self.prepare()
        summary = self.collect(run)
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["observations"], 12)
        self.assertEqual(summary["unique_edges"], 3)
        self.assertEqual(summary["candidates"], 3)
        self.assertEqual(summary["new_candidates"], 1)
        edges = read_csv(run / "citation_edges.csv")
        self.assertEqual(sorted(int(e["observation_count"]) for e in edges), [3, 3, 6])
        self.assertTrue(all(len(e["providers"].split(";")) == 3 for e in edges))
        self.assertEqual(len(read_csv(run / "unresolved_references.csv")), 0)

    def test_prepare_is_offline_and_resume_and_rebuild_preserve_raw(self):
        run = self.prepare()
        self.assertFalse((self.project / "library").exists())
        self.assertFalse(list(run.glob("sources/*/requests.json")))
        self.collect(run)
        files = {str(p.relative_to(run)): digest(p.read_bytes()) for p in run.glob("sources/*/raw/*.json")}
        candidates = read_csv(run / "merged_candidates.csv")
        with patch("sls.snowball.providers.base.build_opener") as opener, patch.object(FixtureOpener, "open", side_effect=AssertionError("resume must reuse cached successes")):
            self.collect(run, resume=True)
            collect(run, project=self.project, offline=True)
            opener.return_value.open.assert_not_called()
        self.assertEqual(files, {str(p.relative_to(run)): digest(p.read_bytes()) for p in run.glob("sources/*/raw/*.json")})
        self.assertEqual(candidates, read_csv(run / "merged_candidates.csv"))
        with self.assertRaises(ValueError):
            self.collect(run)

    def test_tampering_detected(self):
        run = self.prepare()
        self.collect(run)
        raw = next(run.glob("sources/*/raw/*.json"))
        raw.write_text("{}")
        with self.assertRaisesRegex(ValueError, "Raw artifact changed"):
            verify(run)

    def test_config_and_seed_tampering_detected(self):
        run = self.prepare()
        (run / "seed_snapshot.json").write_text("[]")
        with self.assertRaisesRegex(ValueError, "Immutable input changed"):
            verify(run)

    def test_caps_are_partial_and_counts_before_deduplication(self):
        run = self.prepare(max_relationships=1)
        summary = self.collect(run)
        self.assertEqual(summary["status"], "partial")
        self.assertTrue(any(op["count_mismatch"] for op in summary["operations"]))
        self.assertTrue(any(op["status"] == "capped" for op in summary["operations"]))

    def test_known_candidate_id_survives_next_run(self):
        run = self.prepare()
        self.collect(run)
        first = {c["doi"]: c["candidate_id"] for c in read_csv(run / "merged_candidates.csv")}
        next_run = prepare(project=self.project, seeds=self.seeds, slug="next", page_size=1)
        self.collect(next_run)
        second = {c["doi"]: c["candidate_id"] for c in read_csv(next_run / "merged_candidates.csv")}
        self.assertEqual(first, second)
        self.assertTrue(all(c["already_known"] == "yes" for c in read_csv(next_run / "merged_candidates.csv")))

    def test_smoke_is_isolated_and_unreviewed_quality_is_inconclusive(self):
        marker = self.project / "library/manifests/untouched.txt"
        marker.parent.mkdir(parents=True)
        marker.write_text("unchanged")
        run = self.prepare(smoke=True)
        self.collect(run)
        report = evaluate(run)
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(marker.read_text(), "unchanged")
        self.assertEqual(list((self.project / "library").rglob("*")), [marker.parent, marker])
        self.assertTrue((run / "library/bibtex/candidates.bib").exists())

    def test_reviewed_fixture_quality_passes_then_wrong_review_fails(self):
        benchmark = self.project / "benchmark"
        benchmark.mkdir()
        atomic_json(benchmark / "benchmark.json", {"version": "synthetic-test", "minimum_known_edge_recovery": 1.0, "minimum_sample_correctness": 1.0})
        proof = {"verified": "yes", "reviewer": "fixture author", "reviewed_at": "2026-09-11", "evidence": "synthetic test graph"}
        write_csv(benchmark / "expected_edges.csv", [{**proof, "citing_doi": "10.1234/" + a, "cited_doi": "10.1234/" + b} for a, b in [("a", "c"), ("b", "c"), ("b", "a")]], ["citing_doi", "cited_doi", *proof])
        write_csv(benchmark / "expected_metadata.csv", [{**proof, "doi": "10.1234/a", "title": "Alpha seed study", "authors": "Alice Author", "year": "2020"}], ["doi", "title", "authors", "year", *proof])
        run = self.prepare(smoke=True, benchmark=benchmark)
        self.collect(run)
        self.assertEqual(evaluate(run)["status"], "inconclusive")
        review = read_csv(run / "reference_review.csv")
        for row in review:
            row.update(verdict="correct", reviewer="fixture author", reviewed_at="2026-09-11", evidence="synthetic graph")
        write_csv(run / "reference_review.csv", review, list(review[0]))
        self.assertEqual(evaluate(run)["status"], "pass")
        review[0]["verdict"] = "incorrect"
        write_csv(run / "reference_review.csv", review, list(review[0]))
        self.assertEqual(evaluate(run)["status"], "fail")

    def test_budget_exhaustion_persists_partial_work(self):
        run = self.prepare(request_budget=2)
        summary = self.collect(run)
        self.assertEqual(summary["status"], "partial")
        self.assertTrue(any(op["status"] in ("budget_exhausted", "partial_metadata") for op in summary["operations"]))
        for path in run.glob("sources/*/requests.json"):
            self.assertLessEqual(len(json.loads(path.read_text())), 2)

    def test_duplicate_seeds_expand_once(self):
        write_csv(self.seeds, [{"doi": "10.1234/a"}, {"doi": "https://doi.org/10.1234/A"}], ["doi"])
        run = self.prepare()
        self.assertEqual(len(json.loads((run / "seed_snapshot.json").read_text())), 1)
        self.assertEqual(len(self.collect(run)["operations"]), 6)

    def test_publisher_url_seed_matches_bare_doi_and_preserves_input(self):
        write_csv(self.seeds, [{"doi": "10.1234/a"}, {"doi": "https://publisher.example/doi/10.1234/A?tracking=1#references"}], ["doi"])
        original = self.seeds.read_bytes()
        run = self.prepare()
        seeds = json.loads((run / "seed_snapshot.json").read_text())
        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0]["doi"], "10.1234/a")
        self.assertEqual((run / "seed_input.csv").read_bytes(), original)
        self.assertEqual(self.collect(run)["status"], "ok")

    def test_wrong_seed_doi_is_rejected_by_every_provider(self):
        run = self.prepare()
        config = verify(run)
        seed = {"record_id": "seed", "doi": "10.1234/wrong", "openalex_id": "W1", "semantic_scholar_id": "a", "opencitations_id": "doi:10.1234/a"}
        for name, adapter in PROVIDERS.items():
            with self.subTest(provider=name):
                result = adapter(FixtureClient(run, name, config)).run(seed, "backward")
                self.assertEqual(result.status, "wrong_seed")
                self.assertEqual(result.observations, [])

    def test_null_semantic_scholar_endpoint_is_retained(self):
        run = self.prepare(providers=["semantic_scholar"])
        config = verify(run)
        client = FixtureClient(run, "semantic_scholar", config)
        original = client.opener.open
        def response(request, timeout):
            if request.full_url.split("?")[0].endswith("/references"):
                return io.BytesIO(b'{"data":[{"citedPaper":null}]}')
            return original(request, timeout)
        client.opener.open = response
        seed = json.loads((run / "seed_snapshot.json").read_text())[0]
        result = PROVIDERS["semantic_scholar"](client).run(seed, "backward")
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(result.records[-1]["title"], "")
        self.assertEqual(len(reconcile(result.records)[3]), 1)

    def test_opencitations_metadata_failure_keeps_edge_and_identifier(self):
        run = self.prepare(providers=["opencitations"])
        client = FixtureClient(run, "opencitations", verify(run))
        original = client.opener.open
        def response(request, timeout):
            if "/metadata/doi:10.1234/c" in request.full_url:
                raise HTTPError(request.full_url, 404, "missing", {}, None)
            return original(request, timeout)
        client.opener.open = response
        seed = {"record_id": "seed", "doi": "10.1234/a"}
        result = PROVIDERS["opencitations"](client).run(seed, "backward")
        self.assertEqual(result.status, "partial_metadata")
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(result.records[-1]["doi"], "10.1234/c")
        self.assertTrue(result.complete)

    def test_openalex_missing_hydration_retains_reference(self):
        run = self.prepare(providers=["openalex"])
        client = FixtureClient(run, "openalex", verify(run))
        original = client.opener.open
        def response(request, timeout):
            if "filter=openalex" in request.full_url:
                return io.BytesIO(b'{"results":[]}')
            return original(request, timeout)
        client.opener.open = response
        result = PROVIDERS["openalex"](client).run({"record_id": "seed", "doi": "10.1234/a"}, "backward")
        self.assertEqual(result.status, "partial_metadata")
        self.assertEqual(len(result.observations), 1)
        self.assertEqual(result.records[-1]["source_record_id"], "https://openalex.org/W3")

    def test_opencitations_reversed_edge_is_rejected(self):
        run = self.prepare(providers=["opencitations"])
        client = FixtureClient(run, "opencitations", verify(run))
        original = client.opener.open
        def response(request, timeout):
            if "/references/" in request.full_url:
                return io.BytesIO(b'[{"citing":"doi:10.1234/c","cited":"doi:10.1234/a","oci":"wrong"}]')
            return original(request, timeout)
        client.opener.open = response
        result = PROVIDERS["opencitations"](client).run({"record_id": "seed", "doi": "10.1234/a"}, "backward")
        self.assertEqual(result.status, "wrong_direction")
        self.assertFalse(result.observations)

    def test_repeated_pagination_cursor_stops_and_preserves_results(self):
        run = self.prepare(providers=["openalex"])
        client = FixtureClient(run, "openalex", verify(run))
        original = client.opener.open
        def response(request, timeout):
            response = original(request, timeout)
            data = json.loads(response.read())
            if "filter=cites" in request.full_url:
                data["meta"]["next_cursor"] = "*"
            return io.BytesIO(json.dumps(data).encode())
        client.opener.open = response
        result = PROVIDERS["openalex"](client).run({"record_id": "seed", "doi": "10.1234/a"}, "forward")
        self.assertEqual(result.status, "pagination_error")
        self.assertEqual(len(result.observations), 1)

    def test_bibtex_intake_and_candidate_id_intake(self):
        bib = self.project / "seeds.bib"
        bib.write_text('@article{custom,title={Alpha seed study},author={Alice Author},year={2020},doi={10.1234/a}}')
        run = prepare(project=self.project, seeds=bib, slug="bib", providers=["openalex"])
        self.collect(run)
        candidate = read_csv(run / "merged_candidates.csv")[0]
        known = prepare(project=self.project, candidate_ids=[candidate["candidate_id"]], slug="known", providers=["openalex"])
        self.assertEqual(len(json.loads((known / "seed_snapshot.json").read_text())), 1)

    def test_interruption_resumes_without_repeating_completed_requests(self):
        run = self.prepare(providers=["openalex"])
        original = FixtureOpener.open
        count = 0
        def interrupt(opener, request, timeout):
            nonlocal count
            count += 1
            if count == 3:
                raise KeyboardInterrupt()
            return original(opener, request, timeout)
        with patch.object(FixtureOpener, "open", interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.collect(run)
        raw_before = {p.name: digest(p.read_bytes()) for p in run.glob("sources/*/raw/*.json")}
        self.assertEqual(len(raw_before), 2)
        summary = self.collect(run, resume=True)
        self.assertEqual(summary["status"], "ok")
        journal = json.loads((run / "sources/openalex/requests.json").read_text())
        successful = [r for r in journal if r["status"] == "ok"]
        self.assertEqual(len({r["request_id"] for r in successful}), len(successful))
        for name, checksum in raw_before.items():
            self.assertEqual(digest((run / "sources/openalex/raw" / name).read_bytes()), checksum)

    def test_partial_live_and_offline_statuses_agree(self):
        run = self.prepare(request_budget=2)
        live = self.collect(run)
        offline = collect(run, project=self.project, offline=True)
        self.assertEqual([(o["status"], o["imported_count"]) for o in live["operations"]], [(o["status"], o["imported_count"]) for o in offline["operations"]])


class TransportTests(unittest.TestCase):
    def test_redaction_and_offline_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            seeds = project / "seeds.csv"
            seeds.write_text("doi\n10.1234/a\n")
            run = prepare(project=project, seeds=seeds, slug="test", providers=["openalex"], retries=0)
            config = verify(run)
            with patch.dict("os.environ", {"OPENALEX_API_KEY": "private-secret"}):
                client = Client(run, "openalex", config)
                with patch.object(client.opener, "open", return_value=io.BytesIO(b'{"echo":"private-secret","api_key":"other-sensitive-value"}')):
                    client.get("https://api.openalex.org/works/W1")
            for path in run.rglob("*"):
                if path.is_file():
                    self.assertNotIn("private-secret", path.read_text())
                    self.assertNotIn("other-sensitive-value", path.read_text())
            page = Client(run, "openalex", config, offline=True).get("https://api.openalex.org/works/W1")
            self.assertEqual(page.data["echo"], "[REDACTED]")

    def test_long_retry_after_does_not_sleep_or_bypass_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            seeds = project / "seeds.csv"
            seeds.write_text("doi\n10.1234/a\n")
            run = prepare(project=project, seeds=seeds, slug="test", providers=["openalex"])
            client = Client(run, "openalex", verify(run))
            error = HTTPError("https://api.openalex.org/works/W1", 429, "rate limited", {"Retry-After": "120"}, None)
            with patch.object(client.opener, "open", side_effect=error), patch("sls.snowball.providers.base.time.sleep") as sleep:
                with self.assertRaises(ProviderError) as caught:
                    client.get("https://api.openalex.org/works/W1")
                self.assertEqual(caught.exception.status, "rate_limited")
                self.assertTrue(all(call.args[0] < 60 for call in sleep.call_args_list))


if __name__ == "__main__":
    unittest.main()
