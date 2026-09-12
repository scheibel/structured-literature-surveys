# Snowballing

The `snowball` commands collect one round of backward references and forward citations from explicitly selected papers. OpenAlex, Semantic Scholar, and OpenCitations can be enabled independently. All commands run from the project root using Python 3.10+ and the standard library.

Decision recorded 2026-09-12: citation-source quality is an accepted limitation for this iteration. We report uncertainty and observable anomalies; establishing ground truth, adjudicating references, estimating false-positive/false-negative rates, and correcting upstream citation data are not required for component acceptance.

Backward: the seed cites another paper. Forward: another paper cites the seed. Edges always point from citing to cited paper. Discovery does not constitute screening or inclusion in a survey.

## Start a normal run

Create `seeds.csv`:

```csv
doi,title,authors,year
10.1111/cgf.15087,Improving Temporal Treemaps by Minimizing Crossings.,Alexander Dobler; Martin Nöllenburg,2024
```

```bash
python3 scripts/sls snowball prepare \
  --seeds seeds.csv \
  --providers openalex semantic_scholar opencitations \
  --direction both --slug temporal-treemap

python3 scripts/sls snowball collect \
  --run-dir searches/YYYY-MM-DD_temporal-treemap-snowball
```

`prepare` is the offline dry run: it validates inputs and creates immutable input snapshots without network access or library updates. Use the actual dated directory printed by the command. `collect` retrieves metadata, deduplicates, validates, and reconciles the normal run with the project library.

Alternatively, pass a BibTeX file through `--seeds`, or select existing library candidates with repeatable `--candidate-id` arguments. A CSV can contain `candidate_id`, `doi`, `canonical_url`, `title`, `authors`, `year`, and explicit `openalex_id`, `semantic_scholar_id`, or `opencitations_id` columns. Duplicate seeds are collapsed before expansion. Conflicting seed identities are rejected during preparation.

The `doi` column also accepts DOI resolver URLs and publisher URLs with `/doi/<doi>` in their path:

```csv
doi
https://onlinelibrary.wiley.com/doi/10.1111/cgf.15087
```

This normalizes to `10.1111/cgf.15087` locally. URL path escapes are decoded, and query parameters and fragments are excluded. The original CSV is retained unchanged for provenance. Other publisher URL formats, such as `/doi/full/<doi>`, are not extracted by this rule; use a DOI resolver URL or the bare DOI instead.

Provider seed resolution currently requires a DOI or explicit provider identifier. Title-only seeds remain reported as unresolved; automatic title search is intentionally not used to silently select a similarly named paper. OpenAlex work IDs may be bare `W...` identifiers or OpenAlex URLs; Semantic Scholar accepts paper IDs; OpenCitations identifiers use prefixes such as `doi:` or `omid:br/...`.

## Credentials and limits

Configure credentials outside project files when available:

| Provider | Environment variable | API behavior |
|---|---|---|
| OpenAlex | `OPENALEX_API_KEY` | Referenced work IDs with metadata batches; incoming works via cursor pagination |
| Semantic Scholar | `SEMANTIC_SCHOLAR_API_KEY` | Paper references and citations with offset pagination |
| OpenCitations | `OPENCITATIONS_ACCESS_TOKEN` | Index v2 citation lists plus Meta v1 bibliographic lookup |

The clients use documented public endpoints without credentials when permitted and report access failures. They never fall back to HTML parsing. Unauthenticated Semantic Scholar requests may be heavily rate limited. API keys and their URL-encoded values are redacted if echoed in responses; persisted request descriptors contain only credential environment-variable names.

Each prepared run records these settings:

- `--request-budget 1000`: maximum request attempts **per provider across all resumes**, including retries. Cached responses cost no requests.
- `--budget opencitations=200`: override one provider's budget; repeatable.
- `--max-relationships N`: cap observations per seed and direction. Normal runs default to no relationship cap; `all` explicitly removes it.
- `--page-size 100`: adapter-specific paging/batching limit.
- `--timeout 20`, `--retries 2`, `--request-interval 1`: seconds, additional retry attempts, and minimum seconds between requests. Intervals below one second are rejected.
- `--credential-env openalex=MY_OPENALEX_KEY`: use another environment-variable name.
- `--parent-run PATH`: link a subsequent researcher-selected round to its predecessor.

Request limits are attempt budgets, not promises about currency charges or response sizes. OpenCitations returns a whole citation list; its relationship cap applies after the response arrives. Provider Retry-After delays are respected; long delays produce a resumable rate-limited state instead of a long blocking sleep. Exhausting a frozen request budget requires a new run with a larger budget.

API capabilities, documentation links, access constraints, and attribution requirements are captured in `provider_capabilities.json` in each run. Attribute Semantic Scholar contributions in published materials as required by its API terms.

## Outputs and duplicate handling

| File | Contents |
|---|---|
| `seeds.csv`, `seed_snapshot.json`, `seed_input.*` | Inspectable seeds, authoritative normalized snapshot, and original input |
| `run_config.json`, `config.sha256` | Frozen settings and integrity hash |
| `known_candidates.json`, `overrides.json` | Frozen identity state and explicit duplicate decisions |
| `sources/<provider>/requests.json` | Exact redacted URLs, attempts, timestamps, statuses, raw paths and checksums |
| `sources/<provider>/raw/*.json` | Immutable response text, except redacted echoed credentials |
| `sources/<provider>/source_manifest.json` | Status and counts for every seed/direction |
| `sources/<provider>/normalized_records.csv` | Bibliographic source records |
| `seed_resolution.csv` | Provider identity evidence and unresolved seed statuses |
| `source_records.csv`, `identity_mapping.csv` | Source observations and their canonical candidate mappings |
| `citation_observations.csv` | All provider/seed/direction discovery paths and raw record locators |
| `citation_edges.csv` | Unique directed candidate pairs with observation counts |
| `merged_candidates.csv` | Participating seeds and discovered candidates; `is_seed` and `already_known` distinguish them |
| `deduplication_decisions.csv` | Merge/review evidence and matching policy version |
| `unresolved_references.csv` | Endpoints lacking enough metadata to form a candidate |
| `validation.json`, `validation_report.md`, `summary.md` | Counts, completeness, caps, and provider failures |
| `manual_action_queue.csv`, `missing_pdfs.csv` | Missing metadata/assets and unresolved source or identity work |
| `bibtex_update.bib` | Bibliographic entries derivable for this run |
| `derivation_manifest.json` | Code fingerprint and mode used for derived outputs |

Matching indexes DOI, conservatively normalized publisher URL, provider identity, and title/year evidence. Weak evidence cannot bridge conflicting DOI identities. Ambiguous title/year matches remain separate with review actions. Existing candidate IDs and BibTeX keys are retained; new readable-key collisions receive deterministic suffixes. Missing bibliographic values never act as shared matching keys.

Paper deduplication and edge deduplication are separate. If A and B both cite C and all three services return both relationships, C appears once, the graph has two edges, and six observations retain provenance. A forward discovery of an already recorded edge adds an observation. Seeds are expanded once per provider/direction/snapshot; there is no automatic recursion.

For reviewed duplicate overrides, prepare a new run with `--overrides decisions.csv`. Columns are `left,right,decision,actor,timestamp,reason`; `left` and `right` identify source records from `source_records.csv`, and `decision` is `merge` or `separate`. Source record IDs are derived from provider/request/locator. Overrides must reference records available in the new run. Conflicting existing candidate IDs cannot be silently renamed; resolve the project identity state first.

Normal collection maintains `library/manifests/snowball_identity.json` and adds complete new entries to `library/bibtex/candidates.bib`. It preserves existing BibTeX entries, reporting incomplete entries for review. Local PDFs are checked and checksummed; no PDFs are retrieved. Overlapping snowball publications use a library lock; do not run older search commands that write the same library concurrently.

## Resume and rebuild

```bash
python3 scripts/sls snowball collect --run-dir searches/RUN --resume
python3 scripts/sls snowball rebuild --run-dir searches/RUN
```

Resume reuses successful cached requests and attempts unfinished work within the original budget. The checkpoint records progress, but raw responses and input snapshots are authoritative: rebuilding replays the provider adapters from recorded requests. Raw checksum or frozen-input changes stop execution.

Rebuild makes no network requests and does not publish into the library. Bibliographic and citation identities derive from the frozen snapshots. Asset-status reports inspect the current local library and can change when the researcher adds PDFs or BibTeX; this is recorded separately from the citation snapshot. A fresh citation update requires a newly dated or newly named run.

## Reference-quality smoke tests

```bash
python3 scripts/sls snowball smoke \
  --benchmark tests/benchmarks/snowballing --live --slug pilot

python3 scripts/sls snowball smoke \
  --replay smoke-tests/YYYY-MM-DD_pilot-snowball

python3 scripts/sls snowball evaluate \
  --run-dir smoke-tests/YYYY-MM-DD_pilot-snowball
```

Smoke runs write only under `smoke-tests/`, including their own library. They do not read or modify the production library. Default smoke limits are 30 attempts per provider, 20 observations per seed/direction, zero retries, and a 10-second timeout. A cap causes an inconclusive completeness result. Use explicit larger budgets and `--max-relationships all` for a full benchmark run.

Replay copies the smoke snapshot into a `-replay` sibling directory and processes cached responses offline. Existing replay destinations are preserved; use `rebuild` and `evaluate` on them. Pass `--baseline OTHER_RUN` to `smoke` or `evaluate` to report added/missing edges separately from correctness findings.

The bundled benchmark contains three actual repository seed papers but **no independently verified citation ground truth**. It is a runnable connectivity pilot. Synthetic provider fixtures supply deterministic expectations for implementation tests and are never represented as evidence about real citation accuracy.

The existing benchmark and sample-review facilities remain optional. They are not a required testing stage, and the empty expected-edge file, unreviewed metadata, and null thresholds need not be filled in for this iteration. Do not interpret unknown citation accuracy as a demonstrated software defect or a demonstrated quality pass.

Report these distinctions:

- Implementation behavior: fixture-tested parsing, identity matching, edge direction, deduplication, provenance, and resume/replay.
- Retrieval status: successful, capped, rate limited, missing credentials, or otherwise incomplete, with counts and request evidence.
- Source-data limitations: citation correctness and omissions are not established; metadata may conflict and suspicious relationships may be present. Preserve those relationships and their provenance rather than silently correcting or discarding them.

Sampling is deterministic and stratified by provider, direction, and shared/unique provider contribution. Do not edit sample identifiers. Review correctness of the citation relationship and metadata, not merely topical relevance. Provider agreement alone is not ground truth.

`quality_metrics.csv` reports seed resolution, optional benchmark/sample metrics, metadata availability, duplicate collapse, and unresolved endpoints. Metrics without evidence remain undefined. `provider_overlap.csv` compares providers on commonly resolved seeds. The existing `quality_summary.md` distinguishes **pass**, **fail**, and **inconclusive**; missing evidence, unreviewed samples, and null thresholds still produce `inconclusive`. This is an informational limitation, not a component acceptance blocker. Check `validation.json` separately for retrieval restrictions. Live citation totals are not pinned as regression expectations.

Exit status: `0` means prepared/complete collection or quality pass; `1` means an execution error or quality failure; `2` means partial collection or inconclusive quality. Ordinary unit tests never access live services.

## Validation recorded during implementation

The bounded 2026-09-11 pilot used three existing treemap papers, at most three relationships per seed/direction, and 15 attempts per provider. OpenAlex and OpenCitations returned data in both directions. Across enabled providers, 26 observations reconciled to 23 unique edges and 25 participating candidates. Semantic Scholar returned HTTP 429 responses; later OpenCitations operations reached the configured budget. The resulting quality status was correctly inconclusive. Offline replay reproduced the counts.

That initial run validated live transport and normalization for two providers. All three adapters are covered by offline fixtures and failure tests. Local pilot artifacts are under `smoke-tests/2026-09-11_provider-network-pilot-snowball/` and are intentionally ignored by Git.

The 2026-09-12 `citation-check` run exercised all three providers: OpenAlex returned 16 observations through nine successful requests, OpenCitations returned 16 through 24 successful requests, and Semantic Scholar returned six through three successful requests after four HTTP 429 responses. The combined 38 observations became 32 unique edges and 34 participating candidates. Retrieval was deliberately capped at three relationships per seed/direction.

Both OpenAlex and OpenCitations returned a paper-to-itself relationship for DOI `10.1111/cgf.15087`. It is present in the source responses and is reported as an anomaly, not a proven false positive. This illustrates the accepted source-quality limitation; agreement between providers does not establish correctness. Citation accuracy and global recall remain unknown, with no requirement to resolve them in this iteration. The saved report is `smoke-tests/2026-09-12_citation-check-snowball/quality_summary.md`.
