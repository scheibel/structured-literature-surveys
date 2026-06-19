# Project Requirements

This project builds a repeatable, inspectable pipeline for structured literature-search data gathering and later coding. The user is an experienced researcher and software engineer. Keep assumptions explicit, gather requirements before implementation, and prefer reproducible artifacts over opaque automation.

Last consolidated: 2026-06-19.

## Working Agreement

- Treat this file as the persistent requirements record for the project.
- Do not implement pipeline scripts until the relevant requirements for a stable first version are clear.
- Design for repeatability first, then convenience.
- Preserve provenance for every query, source request, imported record, transformation, deduplication decision, screening decision, and coding decision.
- Keep the workflow modular so individual stages can be rerun without repeating the entire pipeline.
- Prefer transparent, human-readable files that can also be accessed reliably from scripts.

## Current Scope

The first implementation scope is literature discovery: identify papers that might be candidates for inclusion in a thorough survey.

The pipeline must not bypass subscription restrictions, paywalls, authentication barriers, robots rules, provider rate limits, or provider terms. Absence of an API key is not permission to parse HTML.

Access questions are deferred. Do not automatically retrieve PDFs, classify open-access status, infer institutional subscription access, or handle licensed full text unless this requirement changes later. The framework may inventory and manage PDFs that the executing researcher has validly obtained and placed in the project-level PDF folder, and it may report which candidates lack a local PDF.

For now, source connectors collect publicly available bibliographic metadata needed for candidate identification. The minimum required fields are:

- Authors
- Title
- Publication year
- DOI, when available
- Canonical publisher URL, when available

Additional citation metadata needed for valid BibTeX entries, such as venue, volume, issue, pages, publisher, or proceedings title, may be preserved when available from permitted exports or metadata sources. Abstracts, references, citation counts, affiliations, keywords, funding data, and full-text content extraction or analysis remain out of scope unless later added explicitly.

## Core Requirements

- Reproducibility: Each search run must be traceable to inputs, translated queries, request descriptors, retrieval timestamps, source versions where known, code version, and generated outputs.
- Provenance: Records must retain source database, source-specific query, exact URL or request descriptor, retrieval date/time, source identifiers, and transformation history.
- Auditability: Screening and coding decisions, once added, must include who/what made the decision, when it was made, and the applicable criteria or codebook version.
- Idempotence: Re-running a step with the same inputs should produce the same outputs or clearly explain differences.
- Human review: Automation may assist, but research-critical decisions should be reviewable and correctable by the researcher.
- Incrementality: The workflow should support update searches without losing prior decisions.
- Portability: Avoid hidden local state; important configuration belongs in project files.

## Search Run Workflow

A literature search starts from a user-provided query in a generic project query language. Example:

```text
('temporal' OR 'dynamic' OR 'animated') AND 'treemap'
```

The pipeline must translate the generic query into each configured digital library's expected query syntax. Each translated query must be recorded exactly as executed, together with the exact request URL or equivalent request descriptor used for that source.

Each triggered search runs in a separate directory whose name includes the current date and a human-readable keyword or slug. Candidate convention:

```text
searches/YYYY-MM-DD_keyword-slug/
```

Each search-run directory should contain source-specific results and a merged cleanup result. CSV is the preferred first-pass format for tabular records because it is human-readable, editable, and script-friendly. BibTeX should be obtained or generated for each persisted literature record where possible and reconciled into the durable project-level BibTeX database. Sidecar JSON or YAML may be used for request metadata, provenance, or nested data that does not fit cleanly in CSV.

Per digital library, persist at minimum:

- Source identifier, such as `acm`, `eg`, `ieee`, `sciencedirect`, `scopus`, `wos`, or `google_scholar_manual`.
- Original generic query.
- Source-specific translated query.
- Query semantics report describing source-specific translation choices, unsupported operators, ignored constructs, field mapping differences, wildcard/proximity limitations, and other semantic loss.
- Exact URL or request descriptor used.
- Retrieval timestamp.
- Source-reported result count, when visible or returned by an API.
- Imported record count.
- Connector status, such as `ok`, `missing_credentials`, `manual_export_required`, `not_configured`, `rate_limited`, or `error`.
- Run configuration snapshot, including enabled sources, disabled sources, connector modes, query version, configuration files, and script/code version or commit when available.
- Result records containing authors, title, publication year, DOI when available, and canonical publisher URL when available.
- BibTeX entries or BibTeX enrichment candidates for records where the source export, DOI lookup, or metadata enrichment can provide enough information.

Immediately after source-specific collection, run a cleanup step that merges all source result files for the search run and removes duplicates. Persist the deduplicated output in the same search-run directory as editable CSV, with enough provenance to trace each merged candidate back to all contributing source records.

Deduplication matching should prefer stable evidence in this order: DOI, canonical publisher URL, then normalized title plus year. This matching policy is separate from the human-facing candidate ID policy below. Potential duplicates that cannot be resolved confidently should be retained and flagged for manual review rather than silently discarded.

If the source-reported result count and imported record count differ, the validation report must flag the mismatch. This is expected to catch pagination limits, export caps, UI mistakes such as exporting only the selected page, and partial API/manual exports.

A researcher should be able to open a search-run directory, see what was queried, inspect each source result, and inspect the merged candidate list without running code.

## Project-Level Literature Assets

The framework should prepare and manage durable project-level folders for literature assets that are expected to remain stable across search runs. These folders live outside the dated `searches/YYYY-MM-DD_keyword-slug/` directories.

Candidate convention:

```text
library/
  pdfs/
  bibtex/
  manifests/
```

`library/pdfs/` is where the executing researcher places validly obtained PDF files. The framework must not download PDFs automatically unless a later requirement explicitly authorizes a reviewed connector. It should treat researcher-provided PDFs as durable artifacts and should not duplicate them per search run.

`library/bibtex/` contains project-level BibTeX metadata, including a consolidated BibTeX database for known candidate papers. BibTeX metadata is expected to remain stable across runs and should not live only inside run-date-encoded directories.

The framework should be able to reconcile search results, BibTeX entries, and existing PDFs. For each deduplicated candidate, it should identify whether:

- A matching PDF already exists in `library/pdfs/`.
- A matching BibTeX entry already exists in the consolidated BibTeX database.
- The candidate appears in one or more search-run result files.
- The candidate is missing a local PDF.
- The candidate is missing or has incomplete BibTeX metadata.

Reconciliation matching should prefer DOI, then canonical publisher URL, then normalized title plus year. This is evidence for matching, not the default candidate ID format. PDF filenames may be researcher-chosen, so the framework should maintain a manifest mapping stable candidate identifiers to PDF paths instead of relying only on filenames.

When a local PDF is missing, the framework should report it to the executing researcher with the candidate metadata and canonical publisher URL, so the researcher can assess and obtain the PDF through valid access routes. The framework should not determine whether access is available. Missing-PDF reports should be human-readable and script-friendly, preferably CSV plus optional Markdown summary.

Search-run directories may contain reports that reference project-level PDFs and BibTeX entries, but the durable PDF and BibTeX stores should remain outside the dated run directories.

## Candidate Identity And Artifact Integrity

Each deduplicated literature candidate must have a stable internal candidate ID. The canonical candidate ID should be the preferred BibTeX citation key when enough metadata is available, so candidate identity and BibTeX key policy reinforce each other.

The preferred human-readable key pattern is `firstauthorYYYYshorttitle`, using ASCII slug normalization. This key should be used both as the candidate ID and as the BibTeX citation key where possible. DOI and canonical publisher URL should be used as stronger matching evidence for deduplication and reconciliation, but they should not force unreadable candidate IDs unless the human-readable key cannot be generated.

Candidate ID generation should prefer:

1. Normalized first-author/year/short-title key, when enough metadata exists.
2. DOI-derived fallback key, when the human-readable key cannot be generated.
3. Canonical publisher URL-derived fallback key, when DOI is missing.
4. Stable source-record-derived fallback key, when no better evidence exists.

Collisions must be resolved deterministically, for example by appending a stable suffix. Manual citation-key or candidate-ID overrides are allowed but must be recorded in a manifest so keys remain stable across runs.

Candidate IDs must link CSV rows, BibTeX entries, PDF manifests, source manifests, deduplication decisions, and manual action reports. If the BibTeX key must differ from the candidate ID, the mapping must be explicit.

Raw source exports are immutable once placed in a search-run directory. Scripts must write normalized, merged, deduplicated, and enriched artifacts as derived outputs instead of rewriting raw exports. Derived artifacts may be regenerated from raw exports, manifests, and configuration.

PDF manifests should record candidate ID, PDF path, match method, file size, checksum, and optional human notes. The framework should not rely on PDF filenames as identifiers.

Each search run should produce a manual action queue in a human-readable and script-friendly format. It should include missing PDFs, missing or incomplete BibTeX, ambiguous duplicates, incomplete metadata, export count mismatches, unresolved candidate IDs, and sources that still require manual action.

Each search run must define resume/re-run behavior. The default should avoid overwriting raw exports. If a run directory already exists, the framework should either resume safely, overwrite only derived artifacts, or create a clearly numbered/date-suffixed run directory; the chosen behavior must be recorded.

The project should maintain small representative test fixtures for each supported manual export/API format so parsers, normalization, deduplication, BibTeX generation, and reconciliation can be tested without touching live services.

## Source Connector Requirements

Last checked: 2026-06-19.

| Source | Connector status for v1 | Key/access requirement | Notes |
| --- | --- | --- | --- |
| ACM Digital Library | Manual BibTeX/RIS export ingest implemented for the spike; no public official search API found. | No usable public no-key API quota found. | The framework prepares ACM browser-search instructions and imports researcher-obtained BibTeX/RIS exports. Direct non-browser access to an ACM search URL returned a Cloudflare JavaScript challenge. If the UI exposes RSS/feed links, treat them as auxiliary unless ACM documents complete search export behavior. HTML/feed polyfill requires explicit compliance review. |
| EG Digital Library / Eurographics | Use OAI-PMH first; DSpace REST only if needed. | No key required for verified public OAI-PMH endpoint. | Verified `https://diglib.eg.org/server/oai/request` and `https://diglib.eg.org/server/api`. OAI formats include `oai_dc`, `qdc`, `mods`, `marc`, `mets`, `rdf`, `xoai`, and `dim`. |
| Springer Nature / SpringerLink | Metadata API connector and manual CSV/BibTeX/RIS export ingest implemented for the spike. | Springer API key required for API mode; no no-key API quota found. Manual browser export is acceptable when performed by the researcher through SpringerLink UI controls. | Use `SPRINGER_API_KEY` from the environment for API mode. Persist only redacted API URLs and redact API-key fields in raw JSON. If the key is missing, record `missing_credentials` or use `springer-prepare` plus `springer-import`; do not fall back to SpringerLink HTML parsing. Springer CSV author fields may require manual review because exported authors can be concatenated without separators. CSV exports may be capped at 1,000 records; validation should flag suspected export caps and the researcher should partition the search or use API pagination. |
| IEEE Xplore | Use documented IEEE Metadata API. | API key required; no no-key quota found. | Each query requires a reviewed account/API key. Record missing keys as `missing_credentials`; do not fall back to page scraping. |
| ScienceDirect | Use Elsevier ScienceDirect APIs when configured. | Elsevier API key required; full API behavior may depend on institutional entitlements. | For metadata-driven v1, record missing key, quota, or denied request states explicitly. Avoid HTML parsing as a key workaround. |
| Scopus | Use Elsevier Scopus APIs when configured. | Elsevier API key required; full API behavior may depend on institutional entitlements. | Scopus is an Elsevier product, not Clarivate. Keep this source separate from Web of Science. |
| Clarivate Web of Science | Use Clarivate Web of Science APIs when configured. | API key and paid license required for Web of Science API Expanded; no no-key quota found. | This was the intended Clarivate source. Keep it as `wos`, alongside `scopus`. |
| Google Scholar | Do not use as an automated v1 connector. | No official API key path and no official bulk API. | Google Scholar help says automated software should respect `robots.txt`, bulk access is unavailable, and result lists are capped at 1,000. Use manual citation exports or alert workflows only if required. |

Connector policy: if a source requires an API key or account even for public metadata, record `missing_credentials`, `not_configured`, or an equivalent state. Do not use HTML parsing merely to avoid credentials. Any future HTML or feed polyfill must capture legal/robots review, request throttling, user-agent/contact identity, reproducibility limits, and evidence that no usable API/export route exists for the required metadata.

## RSS And Feed Policy

RSS or Atom feeds advertised by digital libraries should not be treated as authoritative bulk search APIs unless the provider explicitly documents them as complete, stable, paginated search export interfaces for the queried result set.

RSS/Atom is primarily a syndication/update format. It may represent recent additions, alerts, table-of-contents updates, or current matching items, but the format itself does not guarantee complete historical coverage, stable ordering, total counts, pagination, or the metadata fields required by this project.

For this project, RSS/Atom may be used only as:

- An alert/update monitor after an initial search has already been captured through a more suitable mechanism.
- A manual or auxiliary source when the feed URL is provided by the user and source terms allow it.
- A documented connector only if the provider states that the feed completely represents the relevant search result and exposes enough metadata for deduplication.

If a digital library exposes an RSS link for a search page, persist the feed URL as provenance if useful, but do not assume it contains the complete result set.

## Human-Assisted Acquisition Workflow

Because several target digital libraries restrict automated page access or require API credentials, v1 should support a human-executed, machine-ingested workflow as a primary path, not merely a fallback.

In this workflow, the pipeline prepares a dated search-run directory and generates source-specific query instructions. The researcher then opens each digital library in a normal browser session, executes the prepared query manually, verifies the result count and filters, and exports the available citation/search results using the source's supported UI export feature where permitted.

The scripts should then ingest those manual exports, validate them, normalize them to the project metadata schema, merge them, and deduplicate them. This preserves repeatability for downstream processing while keeping source interaction compliant and human-controlled.

The search-run directory should include:

- `query.md` or equivalent: original generic query, source-specific translated queries, and step-by-step manual execution notes per source.
- `sources/<source>/`: one directory per digital library.
- Raw manually exported files, kept unchanged.
- A source manifest recording the exact search URL, translated query, UI filters, result count reported by the source, export format, export timestamp, and any manual notes.
- A query semantics report for each source-specific translation.
- Normalized per-source CSV generated by script from the manual export.
- Merged and deduplicated candidate CSV.
- Project-level consolidated BibTeX database update and reconciliation report for the deduplicated candidate set.
- Validation report listing missing fields, parse warnings, duplicate decisions, source-reported count versus imported count, and sources that still require manual action.
- Manual action queue for missing PDFs, missing BibTeX, ambiguous duplicates, incomplete metadata, export mismatches, and unresolved candidate IDs.

Recommended human steps per source:

1. Open the source in a normal browser session.
2. Run the generated source-specific query.
3. Apply only documented filters captured in the manifest.
4. Record the visible result count and final search URL.
5. Export citation/search results using the source's own export controls, preferably CSV, RIS, BibTeX, or another structured format.
6. Save the export unchanged in the source directory.
7. Run the project import/cleanup script on the search-run directory.

Recommended script responsibilities:

- Generate source-specific query instructions before the manual search.
- Create the search-run directory structure.
- Provide empty manifest templates for each source.
- Parse supported manual export formats.
- Normalize records to the candidate metadata schema.
- Obtain or generate BibTeX entries from source exports, DOI lookup, or metadata enrichment where possible.
- Validate required fields and report gaps.
- Compare source-reported result counts with imported record counts.
- Merge and deduplicate records while retaining provenance back to raw source files.
- Maintain the project-level consolidated BibTeX database for the deduplicated candidate set.
- Maintain candidate IDs, BibTeX keys, PDF manifests, and manual override mappings.
- Never automate browser interaction or bypass source restrictions unless a later requirement explicitly authorizes a reviewed connector.

This workflow is implemented for ACM Digital Library in the current spike and should be the default for sources such as Google Scholar, and an acceptable fallback for API-backed sources when credentials are unavailable.

## Candidate Pipeline Stages

First implementation:

1. Protocol/search definition
2. Source configuration
3. Generic query parsing
4. Source-specific query translation
5. Query semantics reporting
6. Source-specific metadata collection or manual-export intake
7. Source result persistence
8. Export completeness validation
9. Merge and deduplicate
10. Candidate ID and BibTeX key assignment
11. Search-run quality checks
12. Project-level PDF and BibTeX reconciliation
13. Manual action queue generation
14. Consolidated BibTeX database generation
15. Analysis-ready candidate export
16. Archival package for the search run

Later phases, out of current scope:

- Title/abstract screening
- Full-text status tracking
- Full-text screening
- Codebook management
- Study coding
- Reporting exports

## Open Requirements Questions

- What exact grammar should the generic query language support in v1: Boolean operators only, quoted phrases, field scoping, wildcards, proximity, date ranges?
- Which sources are mandatory for the first implementation versus optional connectors?
- Should manual exports use one normalized import template per source or one universal intake template?
- What should the canonical merged CSV columns be beyond the current required candidate metadata fields and provenance columns?
- Should the canonical working store remain plain files only, or should SQLite be added after the file workflow is stable?
- How should deduplication expose confidence and manual override decisions?
- Should the project support multiple independent surveys in one repository or one survey per repository?
- What outputs are required for publication, replication packages, or archive deposit?
- Are LLM-assisted search, screening, or coding steps ever in scope, and if so, what safeguards are required?

## Decisions

- Requirements are gathered before scripts are written.
- The persistent requirements record lives in `AGENTS.md`.
- Current v1 scope is metadata-driven discovery plus local asset reconciliation, not access management, access-rights assessment, or full-text retrieval.
- The framework may manage local PDFs that the researcher validly obtained, but it must not retrieve PDFs or assess access rights automatically.
- Search results are persisted per dated search-run directory.
- CSV is the preferred first-pass format for result records; JSON/YAML sidecars are acceptable for provenance; BibTeX is maintained in a durable project-level database; PDFs are managed in a durable project-level folder outside dated search-run directories.
- The cleanup step merges source results and deduplicates immediately after collection.
- Candidate IDs and BibTeX keys share one stable, readable key policy where possible; DOI and canonical URL are matching evidence rather than the default human-facing ID.
- Raw source exports are immutable; scripts regenerate derived artifacts instead of rewriting raw inputs.
- Each search run must report query translation semantics, export completeness, validation issues, and manual follow-up actions.
- Each search run must preserve a configuration snapshot and define safe resume/re-run behavior.
- The project should maintain representative parser/deduplication/reconciliation fixtures for supported source export formats.
- Scopus and Clarivate Web of Science are separate sources.
- RSS/Atom is not a default bulk search mechanism.

## References

- ACM Digital Library overview: `https://www.acm.org/publications/digital-library`
- ACM reader/crawler policy reference: `https://www.acm.org/publications/policies/roles-and-responsibilities`
- Eurographics Digital Library: `https://diglib.eg.org/`
- Eurographics OAI-PMH base URL: `https://diglib.eg.org/server/oai/request`
- Eurographics DSpace REST root: `https://diglib.eg.org/server/api`
- Springer Nature Developer Portal: `https://dev.springernature.com/`
- Springer Nature Meta API documentation: `https://dev.springernature.com/docs/api-endpoints/meta-api/`
- Springer Nature API base URL: `https://api.springernature.com/`
- IEEE Xplore API portal: `https://developer.ieee.org/`
- IEEE Xplore Metadata API details: `https://developer.ieee.org/docs/read/Metadata_API_details`
- IEEE API Query Basics: `https://developer.ieee.org/docs/read/Searching_the_IEEE_Xplore_Metadata_API`
- IEEE API Terms: `https://developer.ieee.org/API_Terms_of_Use2`
- Elsevier Developer Portal: `https://dev.elsevier.com/`
- Elsevier Scopus APIs: `https://dev.elsevier.com/sc_apis.html`
- Elsevier ScienceDirect APIs: `https://dev.elsevier.com/sd_apis.html`
- Elsevier API key settings and quotas: `https://dev.elsevier.com/api_key_settings.html`
- Clarivate API index: `https://developer.clarivate.com/apis`
- Clarivate Web of Science API Expanded: `https://developer.clarivate.com/apis/wos`
- Google Scholar help: `https://scholar.google.com/intl/en/scholar/help.html`
- RSS 2.0 specification: `https://www.rssboard.org/rss-specification`
- Atom Syndication Format RFC 4287: `https://www.rfc-editor.org/rfc/rfc4287`
