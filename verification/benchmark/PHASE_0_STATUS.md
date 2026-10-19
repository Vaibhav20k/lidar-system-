# Phase 0 implementation checkpoint

Date: 19 October 2026. Branch: `codex/accuracy-phase-0`. Baseline commit: `7b43c43`.

Implemented the benchmark foundation:

- Structural scorer separate from estimation, consuming already-written benchmark observations.
- Named dimensional error with reference uncertainty, missing-data accounting, class errors, one-to-one instance association and ambiguity flags.
- Rigid control alignment without scale and per-reference repeatability scoring.
- Input/scorer hashes, runtime and Git metadata, immutable JSON/CSV reports, partition-leak checks.
- Synthetic exact and deliberately wrong fixtures.
- Legacy four-scan replay moved to a named manifest, with references loaded only after estimation writes results.
- Integer-coordinate scoring bug fixed in the legacy axis calculation.
- Reference collection and test protocol documented.

Verification: 36 Python tests pass (20 pre-existing and 16 new). Synthetic CLI report contains one basic pass and one deliberate failure. All reports remain `releaseQualified: false`. `git diff --check` passes.

No capture, detector, renderer or room-schema behavior changed. No raw photographs were uploaded. No new physical accuracy result is available because historical raw bundles and surveyed structural reference data are absent from the checkout.

Remaining benchmark capabilities: face/edge shape errors, visibility-aware surface comparisons, verified split/merge classification, visual overlays and real-device baseline collection. These depend on richer reference/structural geometry. Current center-based matching is an initial association method, not a full shape metric; ambiguous cases require review. Data independence and correct physical endpoints must be verified during collection.

Next implementation phase: explicit versioned structural geometry, provenance and migration, with lossless import/save/sync/export tests. Keep the benchmark interchange format independent from that application schema.
