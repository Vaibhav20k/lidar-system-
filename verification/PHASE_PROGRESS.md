# Implementation progress

## Phase 1 — versioned structural model

Implemented room schema v2 with non-mutating v1 migration and explicit structural elements. Canonical geometry supports polygon extrusion, circular columns and corresponding polygon sections. Elements preserve capture identity, observed/inferred face labels, processing version, source-frame IDs, connections and unverified dimensional quality. Unit quaternions and simple profiles are validated. Unsupported experimental `structure.py` output is rejected explicitly instead of silently disappearing.

SQLite reads migrate legacy rooms; IndexedDB retains its existing stores and migrates documents through `parseRoom` on read/write. JSON and API round trips preserve new fields. Global calibration scales structural geometry and invalidates quality. Detailed rendering and mesh fallback are deferred to their geometry/display phases; no detection or physical accuracy claim is introduced.

Validation: 31 TypeScript/API tests pass, including five new structural/migration tests. TypeScript and production build pass. Physical validation pending raw scans/reference geometry.

## Phase 2 — deterministic geometry losses

Both ceiling implementations now enumerate supported beam runs, split missing observations and abrupt profile changes, and no longer discard all but the longest run or require half a wall. Runs still require four supported 25 cm samples and the existing face/underside consistency checks. Unsupported extensions to hidden corners were removed. This is not yet a general short/narrow beam detector.

Ceiling-region holes no longer create false overhead clearances. Calibration scales fixture depth, invalidates tolerances and rejects oversized results instead of clamping them. Structural origins follow height-datum changes. GLB export enables ceiling geometry/items explicitly.

Validation: 39 Python tests, 34 TypeScript/API tests and production build pass. Swift source updated for matching interval semantics; native compilation/device validation remains pending macOS/Xcode. Geometry-fitting/recall improvements on real captures have not been established.

## Phase 3 — bounded capture selection and coverage foundation

Implemented a bounded 360-primary/120-reserve viewpoint policy. Redundant camera cells stop consuming slots after three observations; new views and revisits can use the reserve. Frame-write failures and policy version are recorded. The offline patch-coverage utility distinguishes repeated frames from spatially/angularly distinct views and never claims unseen space is covered.

Validation: 42 Python tests pass, including redundant-view, reserved-revisit and distinct-view coverage cases. Swift source mirrors the replay policy; native compilation, frame-rate/thermal trials and live per-face guidance still require completion/device validation. This is the capture-selection foundation, not a completed active surface-guidance system.

## Phase 4 — canonical observation geometry

Depth, ceiling and cloud consumers share pixel-center unprojection with independent horizontal/vertical intrinsic scaling. Camera poses must be rigid and source-frame indices survive missing depth records. Normals across large depth discontinuities and low-confidence samples are excluded from measurement observations. Original depth is retained; no learned sensor correction is applied.

Validation: 46 Python tests pass, including nonuniform-resize reprojection, rigid-pose rejection, missing-frame identity and confidence exclusion. Device calibration, range/incidence bias fitting and physical accuracy remain pending data from the other system.

## Phase 5 — global rigid alignment

Added an anchored, robust rigid pose graph for redundant validated segment overlaps. Original observations are transformed only after global held-out surface checks, room-corner loop closure and bounded-displacement checks pass. Disconnected/underconstrained scans retain original poses. No scale or surveyed dimension enters optimization.

Validation: 48 Python tests pass, including global loop compromise, fixed anchor and disconnected graph rejection. This is segment-level depth alignment; photometric bundle adjustment, device trajectory tests and real-loop qualification remain pending.

## Phase 6 — evidence-preserving surface foundation

Added oriented surfels with equal contribution per source frame, retained frame membership, per-axis inter-frame variance and separate normal bins. Opposing faces are not averaged. No gaps are filled; final dimensions must use source points. Display cloud voxel grouping now uses exact coordinates and requires support from distinct frames instead of duplicate pixels.

Validation: 51 Python tests pass. Full adaptive TSDF/mesh reconstruction and topology-qualified plane extraction remain deferred; this foundation supports candidate segmentation without implying complete observed surfaces.

## Phase 7 — column candidate extraction

Added independent-frame, multi-height cross-section persistence with wall-plane exclusion. Fully supported rectangular sides and broad circular arcs retain their different shapes; open profiles stay unknown-structural candidates with inferred faces. Source frame IDs and observed vertical extent are retained; no hidden extension to floor/ceiling is asserted.

Validation: 54 Python tests pass, including rectangular/circular shapes, separate columns, short furniture and single-frame rejection. General concave/tapered profiles, attached-column recovery and tall-furniture discrimination remain incomplete. Real recall and dimensional accuracy have not been qualified.

## Phase 8 — beam and soffit candidates

Added connected underside-footprint proposals with independent-frame support, actual height-gap splitting and preserved polygon boundaries. Multiple overhead runs survive. Candidate depth to the ceiling is explicitly inferred, not measured. Main ceiling surfaces do not become beams. Hole-bearing regions are withheld instead of filled.

Validation: 56 Python tests pass, including separate beams, ceiling exclusion and single-frame rejection. Sloped beams, validated side-plane intersections, hole/mesh geometry and junction topology remain incomplete; no zero-miss guarantee is made.

## Phase 9 — processing and persistence integration

Structural candidates now run on the same corrected observations and floor datum as geometry QA, retain capture identity and survive server result parsing. Jobs persist across restart (interrupted work is marked failed, never silently successful); stages have time limits. Raw uploads are staged and validated before placement, cannot overwrite retained captures and use bounded traversal-safe extraction. A delayed processing result cannot overwrite a changed/deleted saved room.

Validation: 58 Python tests, 35 TypeScript/API tests and production build pass. Raw archive traversal/no-overwrite, job restart and capture identity are tested. Reprocessing retained bundles uses the process endpoint; durable automatic job resumption and native device end-to-end processing remain pending.

## Phase 10 — canonical rendering, review and export

Polygon extrusions (including concave profiles), circular columns and varying polygon sections derive their 3D surfaces from canonical geometry. Supported and inferred faces have distinct opacity. Candidates appear in the floor plan and inspector, can be focused, edited, kept or rejected, and retain evidence after rejection. Confirmation never sets absolute accuracy. GLB carries structural geometry and provenance in node extras; JSON/SVG use the same model.

Validation: 38 TypeScript/API tests, production build and all three Chrome end-to-end tests pass. Tests check concave cap area, rotation/radius, rejection visibility, persistent review and actual GLB geometry metadata. Full profile-vertex editing, wall-elevation overlays and inferred-face measurement snapping remain pending.

## Phase 11 — learned proposals decision

Deferred, as this phase is conditional on labeled data and measured benefit. No model weights were downloaded and no training or ML accuracy claim is made. The required development/holdout experiment is documented in ACCURACY_QUALIFICATION.md.

## Phase 12 — automation and qualification handoff

Added push/PR CI for Python geometry, web tests/build/browser checks and existing native builds. Added canonical-room benchmark export and an explicit inferred-dimension gate: a numerically correct guess cannot pass a measurement test. Closed concave column outlines now refit source-supported planes after raster topology proposal; the L-profile regression preserves its reentrant corner. Inferred display faces can be selected for review but cannot be used as measurement surfaces.

Validation: 61 Python tests, 38 TypeScript/API tests, three Chrome end-to-end tests and production build pass locally. Scoring smoke: one intended pass and one intended failure, with releaseQualified false. Remote CI/native build results must be checked separately.

The full original plan is NOT complete: phases 3–8 and 10 retain the engineering gaps listed above; phase 11 is conditional/deferred and physical phase-12 qualification is blocked on scans/reference measurements from the other system. See ACCURACY_QUALIFICATION.md for the remaining gates and exact replay procedure. No maximum-accuracy or zero-miss guarantee is established.
