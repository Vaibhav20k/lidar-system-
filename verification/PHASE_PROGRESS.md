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
