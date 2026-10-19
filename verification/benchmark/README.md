# Phase 0 — repeatable scoring foundation

These tools score measurements and structural instances. They do not improve a scan or certify accuracy. The structural interchange format is deliberately separate from the application room schema until Phase 1.

## Run synthetic scoring

From the repository root, use Python with NumPy (the existing scan-processing environment also works):

```powershell
.tools/analysis-venv/Scripts/python.exe scripts/scan_processing/benchmark_structure.py verification/benchmark/synthetic.json .tools/benchmark-smoke
.tools/analysis-venv/Scripts/python.exe -m unittest discover -s scripts/scan_processing -p 'test_*.py'
```

Use a fresh output directory each run. Reports are never overwritten. Outputs are `structure-results.json` and `structure-dimensions.csv`. The exact synthetic case must pass the basic checks; the deliberately wrong case must fail with a 30 mm width error, missing beam and extra prediction. Neither is qualified for release.

## Input contract

`manifest.schema.json` documents the structural manifest. The CLI performs domain validation without requiring a JSON Schema package. Paths resolve relative to the manifest, so private datasets can live outside Git. Partition leakage by physical room is rejected. The matching/tolerance configuration applies to every case and must be frozen before evaluating holdout data.

Both observation files have `version: 1`, `units: "meters"`, and `elements`. Each element contains a unique `id`, `kind`, three-number `center` and named positive `dimensions`. Reference elements additionally declare `observable` and optional nonnegative `uncertaintyMeters` (default zero is appropriate only for synthetic or documented exact references). Production reference uncertainty must be recorded deliberately.

Dimension names designate the same physical cross-section/endpoints in reference and prediction; the scorer cannot verify that semantic correspondence. A rectangular column can use `width`, `depth`, `height`; a circular column can use `diameter`, `height`. That does not test full shape: an incorrectly box-shaped render with the correct diameter can still pass basic dimension checks.

Declare `sharedFrame: true` only when the files already use the same metric coordinate frame. Otherwise provide independent corresponding 3D `controls` with a frozen `maxResidualMeters`. The fit uses rotation/translation only, rejects collinear controls, and never rescales dimensions. Never reuse control targets as accuracy test dimensions. The software checks numeric consistency, not independence of physically collected targets.

Matching is minimum-cost maximum-cardinality one-to-one matching of centers inside `matchDistanceMeters`, ignoring class and identifier. This association gate is not the 20 mm accuracy threshold. Multiple possible associations are flagged and cannot pass basic checks without resolving the input correspondence. Misses, extras and class confusions are explicit. A merged prediction cannot cover two references; a duplicate cannot earn a second true positive. These flags are not yet a geometrically verified split/merge classification.

Only observable references enter recall. Predictions for excluded hidden references remain unmatched extras; therefore keep an observable-only prediction set for this initial scorer. A later stage will score inferred candidates separately. No hidden structure is counted as measured.

Dimension acceptance is `abs(error) + referenceUncertainty <= tolerance`. Missing dimensions fail. Error summaries describe observed values and separately report missing counts. Position is center distance after rigid alignment, with the same uncertainty budget. Repeats group by physical room, stable reference ID and dimension; fewer than three measured scans or any missing measurement fails the repeatability gate. Repeat reference dimensions/visibility must agree.

Reports include input hashes, source commit, dirty-tree flag, runtime versions, configuration, case-level results and repeat ranges. Current output explicitly sets `releaseQualified: false`: critical edges, surface shape, occlusion completeness and processing-time checks are future phases. Do not convert a basic pass into a room-level accuracy claim.

## Historical geometry replay

```text
python scripts/scan_processing/benchmark_geometry.py PRIVATE_SCAN_DIRECTORY FRESH_OUTPUT_DIRECTORY
python scripts/scan_processing/benchmark_geometry.py PRIVATE_SCAN_DIRECTORY FRESH_OUTPUT_DIRECTORY --manifest verification/benchmark/legacy-geometry.json
```

The previous positional command still works. Four legacy scan IDs and their historical reference values now live in a named manifest and separate reference files. Reference files are loaded after refinement has written its output. Input bytes and the manifest/reference files are hashed. Output cannot be inside the source scan directory or overwrite prior results.

This legacy mode intentionally preserves bounding-extent scoring and the old `within_2cm` definition, without a reference-uncertainty budget. It is historical comparison only. Do not confuse it with the new structural scoring protocol or use it as a new acceptance gate. Missing raw bundles are not in this repository, so physical replay remains pending.

## Dataset collection before physical acceptance

1. Inventory each observable beam/column with stable IDs and photographs of named measurement endpoints.
2. Record independent instrument measurements, uncertainty, finish/cladding inclusion and the shared datum.
3. Acquire at least three independent scans per room, including alternate routes; repeated exports do not count.
4. Split by physical room/building, keeping repeats in one partition. Freeze the holdout and matching rules.
5. Include no-structure rooms, furniture lookalikes, separate short beams, wide/freestanding/circular columns, occlusion and reflective surfaces.
6. Report all failures and rejected/rescan cases. Do not tune on holdout results or score supplied calibration distances as independent targets.

Keep private photographs and raw data out of Git. Only synthetic fixtures and reference manifests intended for sharing belong here. Phase 0 validates scoring behavior, not sensor performance.
