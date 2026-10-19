# Implementation progress

## Phase 1 — versioned structural model

Implemented room schema v2 with non-mutating v1 migration and explicit structural elements. Canonical geometry supports polygon extrusion, circular columns and corresponding polygon sections. Elements preserve capture identity, observed/inferred face labels, processing version, source-frame IDs, connections and unverified dimensional quality. Unit quaternions and simple profiles are validated. Unsupported experimental `structure.py` output is rejected explicitly instead of silently disappearing.

SQLite reads migrate legacy rooms; IndexedDB retains its existing stores and migrates documents through `parseRoom` on read/write. JSON and API round trips preserve new fields. Global calibration scales structural geometry and invalidates quality. Detailed rendering and mesh fallback are deferred to their geometry/display phases; no detection or physical accuracy claim is introduced.

Validation: 31 TypeScript/API tests pass, including five new structural/migration tests. TypeScript and production build pass. Physical validation pending raw scans/reference geometry.
