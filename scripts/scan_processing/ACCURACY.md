# Local geometry accuracy checks

Run with Python + NumPy + OpenCV. No network/API requests are made by these commands.

```sh
python -m unittest discover -s scripts/scan_processing -p 'test_*.py'
python scripts/scan_processing/geometry.py capture.json raw-directory original.room.json checked.room.json --report quality.json
python scripts/scan_processing/benchmark_geometry.py scan-directory output-directory
```

The estimator never receives tape targets. The benchmark scores only after estimation.

- Raw repeat checks precede surface-layer filtering. Per-frame medians prevent dense views dominating. Missing repeat evidence fails closed.
- High-confidence depth only; robust equal frame/spatial-cell plane fitting, entire held-out frames, 10 mm per-plane residual budget.
- Rigid segment registration handles rotation and translation with full six-degree observability, held-out spatial patches, connected segments, and loop closure within 10 mm at room corners. Underconstrained or inconsistent registration is withheld. Scale is never changed.
- Footprints update atomically only when every corner is constrained by two accepted wall fits. Floor/ceiling datum updates require both surfaces to pass, sufficient coverage, and level variation <=20 mm. Rejected candidates retain original geometry.
- Quality status is consistency evidence, never a certificate of absolute accuracy. Beam heights remain independently unverified. No beam/column equality or tape-based rescaling is imposed.
- The server uses this path after photo-based operations. Native imports start unverified. Geometry edits invalidate the check.
- iOS now records tracking reasons/mapping status/gaps, 15 cm / 8 degree keyframes plus two repeat frames, and revisit guidance. There is a 360-frame storage cap. This capture change requires installing a new iOS build; existing captures cannot gain missing telemetry.

## Replay outcome, 17 October 2026

All four real captures failed the complete consistency gate (0/4 accepted). No geometric corrections passed; before/after dimensions are unchanged. Same-room repeat spread remains 13.25 cm length, 20.51 cm width and 9.50 cm height. This is an explicit failure to achieve 2 cm accuracy on the existing evidence.

Synthetic regressions: the old guard accepted a known 10 cm jump as ~0.5 mm; the fixed guard rejects it. Stable walls pass. A supported synthetic 4 cm floor-datum error is corrected, while a good floor with an inconsistent ceiling is left unchanged. Translation recovery and underconstrained/held-out rejection are tested.

Before claiming 2 cm: install the capture changes, collect independent repeat scans including start/end revisits and floor/ceiling coverage, and compare against independently measured control points. Require both absolute error <=20 mm and repeat range <=20 mm, report rejection rate, and test additional rooms. Existing tests do not establish physical-device accuracy. Joint global bundle adjustment and an on-phone geometry solver remain future work. The current rigid graph accepts only independently validated edges; it does not invent missing constraints.

## Ceiling wait recovery (build 3)

The first 360-frame build-2 capture saved its room at 12:02:43 UTC and wrote its completed ceiling at 12:08:44 UTC. The user confirmed the room eventually opened. The controller waited without a deadline for the estimator, and build 2 used unoptimized Swift.

Build 3 uses optimized Swift, stops the capture session before waiting, limits the final UI wait to 15 seconds, offers Save without ceiling, and cooperatively cancels ceiling work after a 30-second total budget or early exit. Deferred ceilings are explicitly labelled; JSON, USDZ and raw depth remain available for laptop processing. Worker completion cannot finish the screen twice or overwrite a deferred result after dismissal. Per-stage timing is saved in each raw bundle's ceiling-processing.json. Beam-point buckets preserve the former stretch boundaries and point order while avoiding a full point-array scan for every stretch.


## Root-cause investigation and build 4 (17 October)

The reported 14–20 cm is P90 early/late **surface disagreement**, not a calibrated measurement of camera drift. Glass, clutter, depth error and pose error can all contribute. Smaller 10 cm spatial patches still disagreed by 13–18 cm in the newest scan. RGB-D/SIFT matching of one long revisit yielded a candidate 1.33 degree / 4.48 cm pose change; held-out image reprojection improved from 23.45 to 1.10 pixels. This is evidence of inconsistency, not independently surveyed camera ground truth.

The recorded tracking interruptions were startup only: 3 before the first saved frame, 0 during capture. However 137/360 saved frames had weak world mapping (133 limited, 4 unavailable). Tracking and mapping are now reported separately. The final 2.60 seconds were beyond the 360-frame raw cap; this short tail does not explain the entire error.

The previous translation-only solver cannot represent angular errors. It is replaced by a six-degree rigid solver that rotates normals along with points, validates withheld spatial patches, checks graph closure and rejects insufficient geometry. The newest capture has 30 segments, at most 8 shared stable patches on any edge, and no connected correction graph. The solver correctly leaves its walls unchanged; synthetic recovery does not certify this scan.

Ceiling aggregation previously selected samples near each cell's maximum and weighted dense views more heavily. The replacement takes a median within each frame then across frames. Sparse observed cells remain for classifying ceiling versus beams, with repeat coverage tracked separately (otherwise an old sparse scan falsely selected a beam as its main ceiling). Both native Swift and offline Python carry repeat spread and explicitly unverified RoomPlan floor-datum metadata. The floor candidate in the newest scan differs from RoomPlan's floor by 7.74 cm but fails consistency; applying it is unsafe.

Build 4 adds mapping/motion coaching and local ARKit feature IDs/world positions for diagnosing future revisits. It retains the bounded ceiling wait and all scan preservation behavior. It does not implement on-phone six-degree geometry correction; that solver runs locally on the laptop.

Final 5-scan replay: 0/5 meet all three dimensions within 2 cm. Newest length/width unchanged at 6.3090 / 4.5626 m; ceiling 3.284 -> 3.279 m. Other height changes: 15:39 3.184 -> 3.178 (worse), 16:48 3.278 -> 3.271 (better), 16:50 3.183 -> 3.180 (worse), 15:28 3.233 -> 3.230 (better). The fix removes estimator bias; it is not a claim that every absolute error improves. No tape values enter correction algorithms. Hypothetical height 3.246 m is not substituted for the confirmed reference 3.226 m.

Reproduce diagnostics (all local, no network):

```sh
python scripts/scan_processing/diagnose_capture.py capture.json raw-dir output-dir
python scripts/scan_processing/check_visual_revisits.py raw-dir output-dir
python -m unittest discover -s scripts/scan_processing -p 'test_*.py'
```

Next physical validation must independently isolate pose and depth errors: use visible static targets on opaque walls and on the room-side glass plane, record known control distances without feeding them to the estimator, revisit the same targets/corners with floor + two wall directions visible, and compare individual scans at the same endpoints/heights. Do not claim 2 cm until both absolute error and independent repeat spread pass. The rear-reference laser placement was confirmed by the user; no body-length offset is assumed. Blank walls/glass are visible in the actual RGB frames, but their exact share of the total error remains unproven.
