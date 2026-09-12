# Synthetic citation fixtures

These are handcrafted API-shaped bibliographic responses, not live captures. Their explicit graph is A → C, B → C, B → A. With A and B as seeds, both directions produce four observations per provider and three unique edges across all providers.

The fixture HTTP responder in `tests/test_snowball.py` implements each provider's actual response shape and pagination contract. It exercises the real request journal, raw-response persistence, normalization, deduplication, and library/replay paths. Additional tests inject null endpoints, metadata failures, wrong IDs/direction, interruption, rate limits, and cursor loops.

DOIs `10.1234/a`, `/b`, and `/c` are synthetic test identifiers and must never be queried in live smoke tests.
