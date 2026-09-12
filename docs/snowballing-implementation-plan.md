# Snowballing implementation plan

Status: implementation authorized 2026-09-11. The original design below records the proposed sequence; see [the implementation guide](snowballing.md) for the delivered commands, defaults, and validation limits.

Scope revision, 2026-09-12: citation-source quality is an accepted, reported limitation for this iteration. Independent benchmarks, citation adjudication, accuracy thresholds, and upstream data correction are no longer delivery requirements. Implementation correctness and explicit reporting of retrieval failures remain required.

The requested component supports backward and forward discovery through OpenAlex, Semantic Scholar, and OpenCitations, reference-quality smoke testing, and duplicate filtering across seeds and providers. Defaults below are proposals, not settled project requirements.

## 1. Scope and execution contract

- Backward means seed cites discovered paper; forward means discovered paper cites seed. Store every edge as citing paper → cited paper.
- Start with explicitly selected candidate IDs or a seed CSV containing DOI and available bibliographic metadata. Preserve the input and its checksum. BibTeX intake can reuse the existing parser after CSV/ID intake works.
- Proposed default: one round, both directions, all three providers independently configurable. Later rounds consume a researcher-selected seed file and link to the parent run. No automatic recursive expansion in the first release.
- Proposed normal-run location: `searches/YYYY-MM-DD_slug-snowball/`, with `run_type=snowball` in configuration. Existing directories require explicit resume or offline rebuild; a new collection snapshot requires a new run.
- Parameters: seeds, providers, direction, slug/date, parent run, per-provider request budget, per-seed relationship cap, timeout, retries, page/batch size where supported, and credential environment-variable names. Record stopping reasons and partial results. Avoid default date/type filters; add them only with explicit semantics and exclusion reports.
- Citation relationships and provider counts used for completeness are the narrow scope extension. Abstract analysis, citation contexts, topical screening, PDF retrieval, and access assessment remain excluded.

Acceptance: a dry run validates inputs and writes planned work and configuration without network requests or durable library updates.

## 2. Shared schema and repository integration

Introduce `src/sls/snowball/` with `models.py`, `runner.py`, `quality.py`, and `providers/{base,openalex,semantic_scholar,opencitations}.py`. Register the CLI in `src/sls/cli.py`; keep `scripts/sls` as the entry point. Use the standard library initially.

Extract shared metadata columns and candidate processing from `eg.py` into source-neutral modules, preserving existing connector behavior with regression tests. Reuse `files.py`, `bibtex.py`, and `pdfs.py` after identity reconciliation.

Keep three separate entities:

| Entity | Minimum persisted fields |
|---|---|
| Source record | provider, provider record ID, bibliographic fields, request ID, raw artifact path and record locator, retrieval time |
| Citation observation | observation ID, provider, citing/cited source IDs, provider citation ID when available, seed, direction, round, request/raw locator |
| Canonical edge | edge ID, citing candidate ID, cited candidate ID; linked observations retain all discovery paths |

Unresolved endpoints retain local unresolved IDs and evidence in a separate table; they are never converted silently into complete candidates. Provider record URLs are provenance, not automatically publisher URLs. Record field-level origins and conflicts when combining metadata.

## 3. Implement all three provider adapters

Each adapter exposes seed resolution, backward retrieval, forward retrieval, and metadata hydration. Its result includes records, observations, reported totals where available, continuation/checkpoint information, and explicit status. Validate identity during seed resolution; ambiguous title matches require review.

| Provider | Backward collection | Forward collection | Integration details |
|---|---|---|---|
| OpenAlex | Read `referenced_works`; batch-fetch metadata for referenced IDs | Query works with `filter=cites:<work_id>` and follow cursors | Separate edge retrieval from metadata hydration failures. See [official recipes](https://help.openalex.org/how-to/api-recipes/). |
| Semantic Scholar | Paper `/references` endpoint | Paper `/citations` endpoint | Request only required metadata, follow documented pagination, retain unresolved endpoints. See [official API examples](https://api.semanticscholar.org/api-docs/snippets) and [API reference](https://api.semanticscholar.org/api-docs/graph). |
| OpenCitations | Index v2 `/references/{id}` | Index v2 `/citations/{id}` | Preserve OCI and DOI/PMID/OMID identifiers. Hydrate bibliographic metadata through a reviewed OpenCitations Meta adapter or the other configured providers; keep edge provenance separate from metadata provenance. See [Index v2 documentation](https://api.opencitations.net/index/v2) and [API directory](https://api.opencitations.net/). |

Before implementing each adapter, record its current authentication, terms, attribution, quotas, pagination, and field behavior in a provider capability manifest. Do not assume a common pagination scheme: OpenCitations list retrieval must follow its actual contract, not invented page parameters. Configure `OPENALEX_API_KEY`, `SEMANTIC_SCHOLAR_API_KEY`, and `OPENCITATIONS_ACCESS_TOKEN` as proposed environment names. Persist redacted request descriptors; never credentials.

Use timeouts, bounded retries, provider throttling, and Retry-After where available. Cache metadata resolution within the run. A failed provider must not discard successful results from another; mark the aggregate run partial. Separate not-found, ambiguous seed, missing credentials, rate-limited, capped, empty, and error states.

Acceptance: fixtures for each adapter cover both directions, metadata mapping, missing IDs, empty responses, provider errors, and pagination/batching where applicable.

## 4. Duplicate filtering and stable identity

The current `eg.deduplicate()` groups by one `identity.dedupe_key()`. This misses matches across different levels of metadata completeness. `assign_candidate_ids()` also resolves collisions by encounter order. Address these limitations before publishing snowballing results into the library.

1. Normalize DOI, conservatively normalize publisher URLs, and compute title/year evidence. Do not lowercase case-sensitive URL paths or merge empty title/year values.
2. Build multiple evidence indexes across all provider records, all seeds, and known library candidates. Match DOI first, then canonical publisher URL, then title/year subject to ambiguity checks.
3. Prevent conflicting nonempty DOIs from being merged by weaker evidence. Keep preprints, conference versions, and journal extensions distinct unless a recorded researcher override establishes identity. Do not let transitive weak matches bridge incompatible clusters.
4. Keep ambiguous title/year matches separate and create review actions. Persist merge/non-merge decisions, evidence, policy version, and manual overrides.
5. Reuse existing project IDs and BibTeX keys through a durable identity/alias manifest. Allocate new readable IDs with deterministic evidence-derived collision suffixes, independent of provider or input order. Reconcile existing assignments without silently renaming assets.
6. Deduplicate edges by the ordered pair of resolved candidate IDs. Keep distinct edges to different seeds; aggregate only repeated observations of the same edge. Preserve direction and seed discovery paths in observations.
7. Track expansion separately by seed/provider/direction/snapshot. Deduplicate seeds and avoid repeated expansion; newly discovered papers already in the library remain in the relationship graph but are marked `already_known`.

Acceptance example: A cites C and B cites C, and all three services return both relationships. Output one C candidate, two canonical edges, and six observations. A forward observation of an already recorded edge adds evidence, not another edge. Reordering records must preserve candidate and edge IDs. Resume must not add duplicate observations for the same raw record.

## 5. Smoke testing and source-quality limitations

Provide a bounded live smoke mode and a deterministic offline replay mode. Neither writes the production library. Use a separate workspace under `smoke-tests/`, with its own run files and temporary library. Live tests are opt-in and excluded from ordinary unit tests.

Use representative seeds to exercise retrieval. Do not require a researcher-reviewed answer set or treat a publication bibliography or provider union as definitive ground truth. Report source-quality uncertainty without trying to resolve it in this iteration.

Evaluate each provider independently and then the deduplicated union:

| Measure | Interpretation |
|---|---|
| Seed resolution correctness | Was the intended paper resolved, rather than a similarly titled paper? |
| Citation correctness and omissions | Unknown unless specific supporting evidence exists; do not claim global precision or recall. No mandatory adjudication. |
| Metadata quality | Required-field availability and observable conflicts; completeness does not establish accuracy. |
| Duplicate behavior | Residual duplicate candidates, incorrect merges, repeated edges, and retained observation counts. |
| Provider contribution | Pairwise overlap and unique additions for comparable resolved seeds; disagreement triggers review, not automatic rejection. |
| Retrieval completeness | Reported count versus imported observations before deduplication, unresolved endpoints, pagination completion, and caps. |

Preserve suspicious relationships and their provenance. Provider agreement or disagreement alone does not determine correctness. A citation need not be topically relevant; topical screening remains separate. The existing optional sample-review functionality may remain available, but completing reviews is not required.

Test gates: offline fixtures require exact expected identities, directions, merges, and observation preservation. Report live structural failures, resolution problems, unavailable credentials, and truncated retrieval explicitly. Missing reviewed expectations and uncalibrated quality thresholds do not block component acceptance. Existing evaluator `inconclusive` results must be interpreted with their stated reasons, separately from retrieval status. Do not pin live total citation counts.

Outputs: `quality_summary.md`, `quality_metrics.csv`, `reference_review.csv`, `provider_overlap.csv`, and normal provenance artifacts. Compare later live runs against a dated baseline to identify added/missing edges without treating all graph changes as regressions.

## 6. CLI, artifacts, and safe persistence

Proposed commands (not yet implemented):

```bash
python3 scripts/sls snowball prepare --seeds seeds.csv --providers openalex semantic_scholar opencitations --direction both --slug pilot
python3 scripts/sls snowball collect --run-dir searches/YYYY-MM-DD_pilot-snowball
python3 scripts/sls snowball collect --run-dir searches/YYYY-MM-DD_pilot-snowball --resume
python3 scripts/sls snowball rebuild --run-dir searches/YYYY-MM-DD_pilot-snowball
python3 scripts/sls snowball smoke --benchmark tests/benchmarks/snowballing --live
python3 scripts/sls snowball smoke --benchmark tests/benchmarks/snowballing --replay smoke-tests/BASELINE
```

`prepare` is offline. `collect` fetches and then runs normalization, deduplication, validation, and normal-run library reconciliation. `rebuild` regenerates derived results offline from the frozen inputs, raw records, and recorded overrides. Resume checks the configuration hash and completes unfinished requests without overwriting raw artifacts. Write checkpoints atomically and preserve a raw-artifact checksum manifest. A changed configuration requires a new run.

Persist `seeds.csv`, `run_config.json`, `seed_resolution.csv`, source manifests/raw files/normalized CSVs, `citation_observations.csv`, `citation_edges.csv`, `merged_candidates.csv`, `unresolved_references.csv`, `deduplication_decisions.csv`, validation reports, manual action queue, and library reconciliation reports. Counts distinguish raw observations, unique edges, discovered candidates, new candidates, and already-known candidates. Provider counts must never be compared directly with post-deduplication candidate counts.

## 7. Delivery order and completion criteria

1. Review defaults, record the scope extension in AGENTS.md, and finalize schema and benchmark format.
2. Implement shared identity/edge processing and offline fixtures, including regression tests for existing search workflows.
3. Implement runner, immutable persistence, budgets, resume, offline rebuild, and isolated smoke workspace.
4. Implement OpenAlex, then Semantic Scholar, then OpenCitations including metadata hydration. Run adapter fixtures after each addition.
5. Run a bounded live pilot through all three providers; preserve retrieval findings and report source-data limitations. No mandatory citation adjudication or threshold calibration.
6. Integrate normal-run BibTeX/PDF reconciliation, document CLI examples and limitations, and run the full offline suite.

Completion requires both directions through all three providers; correct overlapping-candidate and edge deduplication; stable cross-run IDs; traceable unresolved data; successful offline rebuild/resume tests; no production-library mutation from smoke tests; and an inspectable live report including source-quality limitations. If a provider cannot be exercised, explicitly record that validation gap rather than declaring it verified. Establishing citation accuracy or correcting upstream data is not a completion criterion.

Seed formats, run locations, and the one-round default are implemented as documented in the guide; provider budgets remain configurable. Quality benchmark construction and acceptance thresholds are outside this iteration's required work. Manual citation-export adapters and automatic multi-round expansion are follow-up scope unless requested.
