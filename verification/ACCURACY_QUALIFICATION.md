# Structural accuracy qualification

## Current result

This delivery is an executable structural reconstruction foundation, not a qualified maximum-accuracy or zero-miss release. Synthetic tests verify selected algorithms and data flow. They do not measure phone error or room-level completeness. `absoluteAccuracyVerified` remains false everywhere.

Raw scan bundles and independent dimensions are on the user's other system. Do not upload them to GitHub. Place private data outside the repository when transferring it to the processing machine.

Remote verification: [run 39220783051](https://github.com/Vaibhav20k/lidar-system-/actions/runs/39220783051) passed geometry, web, Android debug build and unsigned iOS build at code commit `c692478c38712adbc2b2db2aea2b14f29276e304`. This supersedes earlier notes about native source being uncompiled. Signing, installation and physical-device behavior are still untested for these changes.

## Run the software checks

Use Node 24 and Python with NumPy and OpenCV (the current tested environment uses Python 3.14, NumPy 2.5.4 and opencv-python-headless 5.0.0.93).

```text
npm ci
npm test
npm run build
npm run test:e2e
python -m unittest discover -s scripts/scan_processing -p "test_*.py"
python scripts/scan_processing/benchmark_structure.py verification/benchmark/synthetic.json FRESH_OUTPUT_FOLDER
```

Chrome is needed for the browser tests. CI installs it. The scoring smoke case intentionally includes a failing example: exactly one of the two cases should pass basic checks and neither qualifies a release. GitHub Actions runs Python, web, Android and unsigned iOS build checks; a configured workflow is not proof that its remote jobs passed.

## Process a private capture

Keep an immutable original capture JSON, raw folder (manifest, frames JSONL, depth, confidence, RGB and tracking) and original normalized room JSON. Do not use a manually calibrated, already refined or edited room as refinement input.

```text
python scripts/scan_processing/geometry.py CAPTURE_JSON RAW_FOLDER ORIGINAL_ROOM_JSON OUTPUT_ROOM_JSON --report QUALITY_REPORT_JSON
python scripts/scan_processing/export_structural_benchmark.py OUTPUT_ROOM_JSON PREDICTIONS_JSON
python scripts/scan_processing/benchmark_structure.py PRIVATE_MANIFEST_JSON FRESH_REPORT_FOLDER
```

The server runs this geometry stage after optional furniture/fixture stages. Capture IDs must match retained bundles. Uploads never replace existing raw bundles. To retry an existing capture, call its process endpoint with the original input, rather than uploading again. A restart marks an active job interrupted; it does not claim completion or automatically resume.

The prediction exporter does not read reference dimensions. `width` and `depth` are the smaller and larger dimensions of the local profile's minimum rectangle; `height` is the local extrusion extent. For horizontal-underside beam proposals this means vertical drop, not beam run. Agree these names with the reference dataset before scoring. Circular columns use diameter. Varying sections need section-specific scoring and cannot pass this baseline exporter. Inferred dimensions are explicitly excluded from passing measurement gates, even if their numeric guess happens to match a reference.

## Collect the missing evidence

For each physical room, collect at least three genuinely independent scans and an inventory of every observable beam and column. Include stable IDs, annotated photos, cross-section shape, named endpoints, independent dimensions, instrument uncertainty, visible/hidden faces and whether finishes are included. Use separate control points for rigid alignment; never score calibration dimensions as independent targets.

Include freestanding/attached, rectangular/circular/concave/tapered columns; short, narrow, separated, intersecting and sloped beams; soffits; partial views; furniture lookalikes; reflective surfaces; weak tracking; and rooms containing no beams or columns. Split development/tuning/holdout by physical room or building, keeping repeat scans in one partition. Freeze parameters before opening holdout references.

Score misses, false positives and class confusions separately. Report error per dimension, reference uncertainty, median/p95/worst errors, repeated-scan ranges and rejected/rescan cases. Inspect per-face shape and critical junctions independently: the current center-based scorer cannot qualify surface shape, split/merge topology or hidden-space completeness.

## Remaining engineering gates

| Area | Remaining work before claiming the original plan complete |
| --- | --- |
| Capture | Run the compiled Swift changes on a device; measure frame loss, thermal behavior and storage; complete live per-face guidance. |
| Calibration | Verify actual RGB/depth alignment and characterize range/incidence/device bias on independent targets. |
| Registration | Real loop/revisit tests, large-room runtime and rejection behavior; full photometric bundle adjustment is not implemented. |
| Surfaces | Oriented surfels are available; adaptive TSDF, mesh fallback, full plane topology and source-weighted uncertainty are incomplete. |
| Columns | Closed concave outlines have source-plane refitting; wall-attached open profiles, taper/section changes and tall-furniture ambiguity need further work and data. |
| Beams | Horizontal underside proposals work; sloped beams, measured side/top intersections, holes and beam-column junction reconstruction remain incomplete. |
| Review | Full profile-vertex editing, elevation overlays and inference-aware snapping remain incomplete. |
| Release | Real holdout, instrument error budget, signed device scans and performance limits must pass. Unsigned native CI compilation has passed. |

These gaps must not be hidden by labeling candidates confirmed. Confirmation records human review only.

## Optional learned proposals (phase 11)

Deferred deliberately: no labeled training/validation dataset or approved model weights are available. First classify geometric detector misses on development rooms. Only add a semantic model if it improves independently measured instance recall without unacceptable false positives and passes separate building holdout tests. Keep geometric fitting, physical scale and source evidence authoritative. Never train or tune on holdout rooms. There is no installed model, downloaded weight file or claimed ML accuracy in this delivery.
