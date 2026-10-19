# Device acceptance checklist

The user has an iPhone 16 Pro. Mac access and an Android test device are not yet confirmed. Browser fixtures and synthetic point clouds are not evidence of native sensor accuracy.

## First iPhone run

- Compile and install the signed Xcode build; check the bridge reports `roomplan` support.
- Deny camera access and verify actionable feedback. Grant it in Settings and retry.
- Start and cancel a scan. Verify a second scan starts successfully and camera resources are released.
- Capture a well-lit rectangular room, moving around all walls and corners. Finish; check the editor receives measured walls/openings/objects and inferred floor.
- Save, force-close and reopen the app. Check the same room and dimensions.
- Export JSON, GLB and the original USDZ. Open the GLB in an independent glTF viewer and USDZ in Quick Look. Check axes, furniture positions and opening offsets.
- Scan an L-shaped room. Check concavity; a missing floor should be reported when wall endpoints cannot form a loop.
- Background the app mid-capture and confirm it terminates with an interruption message rather than silently accepting a partial room.
- Check iPhone safe areas, rotation, keyboard editing, file sharing and offline use.

## First Android run

- Test both an ARCore depth-capable phone and an ARCore phone without depth; record exact model, OS and Google Play Services for AR version.
- Test ARCore installation/update flow, camera denial, cancel/retry, tracking loss and background/resume.
- Check the preview fills the screen with correct orientation and the crosshair corresponds to the actual floor hit.
- Mark corners in perimeter order, including an L-shaped outline. Compare dimensions against a tape/laser reference.
- Test floor vs tabletop detection; incorrectly marking a tabletop must be corrected by restarting the scan.
- Use depth estimation on empty convex rooms only during the initial trial. Test incomplete coverage, blank walls, mirrors and clutter; record failures, not just successes.
- Verify automatic depth capture never claims semantic door/window/furniture labels. Add these during review.

## Measurement study

Use 10–20 varied rooms, three captures per room/device/operator. Record wall lengths, diagonals, height, floor area, opening size/offset, capture duration and manual correction time. Record mean, median, P95 and worst absolute/relative errors and capture failure rate. A proposed initial engineering goal is <=5 cm median wall-length error in ordinary furnished rooms, but this is **not an achieved result or a guaranteed accuracy specification**. Set final acceptance thresholds after the pilot and intended use are agreed.

Native scan artifacts can be recovered from Xcode's downloaded app container (`Library/Application Support/RoomformScans`) or the Android debug app's private `files/captures` folder. Import a raw JSON into the editor to reproduce normalization issues. Do not use recordings from unrelated rooms or sample scenes as successful device measurements.

## Accuracy and structure benchmark additions — 19 October 2026

Follow [the Phase 0 benchmark protocol](../verification/benchmark/README.md) for new measurement trials. Before scanning, assign stable room/element IDs, photograph measurement endpoints, record independent reference uncertainty and separate control points from scored dimensions. Acquire at least three independent captures per room. Include separate short beams, freestanding/wide/circular columns and negative furniture-lookalike cases. Keep private images/raw bundles outside Git. The current scorer evaluates instances and named dimensions only; full shape and unobserved-region checks remain pending and no new device accuracy is certified.

The new structural-accuracy work targets 20 mm on required dimensions of accepted scans, including reference uncertainty, and 20 mm repeat range. This supersedes the older 5 cm median pilot proposal above for this workstream. Neither target has been achieved by the new code; Phase 0 changes scoring only.
