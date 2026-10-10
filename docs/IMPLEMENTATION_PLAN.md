# Roomform implementation plan

The product turns a guided room capture into an editable room model. Android and iPhone are required. The available physical device is an iPhone 16 Pro; Mac access is not confirmed. This repository implements the prototype milestone and records the remaining work for a validated release.

## Product flow

1. Detect native capture support and camera permission.
2. Capture on iOS with RoomPlan. Capture on Android with ARCore depth or guided floor-corner hit tests.
3. Normalize into meters, right-handed Y-up coordinates, with floor points on X/Z.
4. Review the scan, correct room geometry, label openings, and inspect furniture volumes.
5. Save locally, optionally sync to a private server, and export JSON, GLB, or SVG/printable floorplan. iOS can also share its original USDZ.

## Architecture

- React + TypeScript and Capacitor for one mobile/desktop review UI.
- Swift RoomPlan plugin for genuine LiDAR room capture; no browser camera imitation.
- Java ARCore activity for depth samples and floor-corner measurement. Automatic depth layout is experimental and limited to well-observed convex rooms; guided corner capture is the fallback.
- Shared validated geometry, native normalization, room editing, and export code.
- IndexedDB local persistence. Optional Node API with SQLite storage, revision checks, input validation, and token-based protection for network access.
- No raw indoor camera images uploaded by default.

## Milestones and acceptance

| Milestone | Acceptance |
| --- | --- |
| Shared model | Reject malformed geometry, unknown versions, invalid openings, and self-intersecting floorplans; preserve measurement provenance |
| Review studio | Sample explicitly identified; create/import/edit/save/reload/export works; responsive 2D and 3D views |
| iOS capture | Capability detection, permission, scan/cancel/finalize, RoomPlan normalization, original USDZ sharing |
| Android capture | Install/permission/session lifecycle, camera preview, tracking guidance, depth confidence sampling, guided corners, result return |
| Local sync | Roundtrip a room; reject unauthorized requests and stale revisions; preserve data after restart |
| Verification | Type checking, geometry tests, API integration tests, browser interaction and export tests |
| Device pilot | Build/sign on Mac, run on iPhone 16 Pro, compare physical room dimensions; run ARCore on representative Android devices |

## Deferred production work

Photorealistic textured meshes/Gaussian splats, unrestricted Android automatic semantic detection, multi-room alignment, multi-account cloud hosting, catalog/quotation integrations, app store publication, and measurement accuracy certification are later work. They must not be represented as completed by a sample scene or browser preview.

## Hardware validation

Use at least 10–20 varied rooms: furnished rectangles, L-shapes, non-right angles, white walls, mirrors, clutter, low light, and corridors. Compare wall lengths, diagonals, heights, floor area, and opening offsets with independent measurements. Report failed captures as well as median/P95 errors and correction time. Device scans remain unverified until this is done.

## Delivery sequence

Build the shared model and viewer first, connect native capture, add editing/export/persistence and optional sync, then run automated and browser checks. Produce an unsigned build where the host supports it. iOS requires Xcode on macOS and developer signing to install on the user's iPhone.

## Pilot-to-release plan

These are planning estimates for a small team, not completed work or delivery promises. A credible limited MVP remains approximately 3–5 months once device access and scope are fixed; Android automatic reconstruction is the largest uncertainty.

| Phase | Approximate effort | Exit condition |
| --- | --- | --- |
| Current prototype | Implemented in this repository | Shared editor, capture integrations, API and automated validation available; native/device status recorded separately |
| iPhone pilot | 1–2 weeks | Signed build on iPhone 16 Pro; 10–20 varied rooms; failed scans and measurement errors reported |
| Android pilot | 2–4 weeks | At least 3 representative devices; depth vs guided support matrix; tracking and thermal behavior measured |
| Reconstruction hardening | 3–6 weeks | Better line/plane fitting, scan coverage feedback, incomplete-room recovery, loop/drift evaluation; explicit unsupported cases |
| Product hardening | 2–4 weeks, partly parallel | Reliable interruption/recovery, persistent native file lifecycle, accessibility, export compatibility and backup/restore |
| Private team/cloud release | 2–4 weeks if needed | User accounts, room ownership, scoped access, TLS, rate limits, migrations, restore drills, retention controls and observability |
| Store beta | 1–3 weeks plus review | Signed release builds, app icons/screenshots, privacy declarations, TestFlight/Play internal testing and real-device regression checks |

Start with one room per capture and editable parametric geometry. Add multi-room alignment only after single-room drift is characterized. Add a separate photo/texture pipeline only if photorealism is a requirement; the current output deliberately uses semantic furniture volumes. A 3D visual model, a watertight textured mesh and a dimensionally dependable design model are separate acceptance criteria.

## Decisions before a public release

1. Confirm whether this is for home layout planning, professional measuring, interior design, or fabrication. This sets tolerances and mandatory manual verification.
2. Choose the supported Android device list from measured results; do not advertise identical iPhone/Android automation.
3. Confirm whether users need offline-only storage, private team sync or a multi-user cloud product. The current service is a single workspace API.
4. Select the required interchange formats and target design tools. JSON, GLB, SVG and original Apple USDZ are implemented; proprietary Cyncly interchange is not assumed.
5. Set pilot accuracy/completion targets, expected room sizes, scan duration/thermal limits and failure handling before expanding scope.

The public [Cyncly Scan listing](https://apps.apple.com/us/app/cyncly-scan/id6468987721) establishes comparable user-facing behavior; it does not prove Cyncly uses RoomPlan internally. The independent implementation here uses documented [Apple RoomPlan](https://developer.apple.com/augmented-reality/roomplan/) and [Google ARCore](https://developers.google.com/ar/develop/depth) capabilities.
