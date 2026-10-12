# Working session summary — 11 and 12 October 2026

This picks up from [SESSION_SUMMARY_2026-10-10.md](SESSION_SUMMARY_2026-10-10.md). It records what was run on devices, what was built on top of the scans, what an independent review found, and what is still unproven. All code is on the branch `ios-scanner-registration-raw-capture`; `main` is unchanged and no pull request is open.

## Where things stand

- **The app now runs on real iPhones.** It was installed from Xcode on an iPhone 16 Pro and an iPhone 14 Pro (iOS 18.5) and used to scan seven rooms: three in a home on 11 October and four at a site on 12 October.
- **Raw depth recording works.** Every raw bundle has one depth map per keyframe (49 to 143 keyframes per room, 27 to 86 MB per bundle). This was the main open question in the previous summary.
- **Three things RoomPlan does not report can now be added to a room after the scan**, by Python scripts run on a Mac: electrical plates (switch boards, sockets), the ceiling with its beams and soffits, and, experimentally, wall-mounted AC units.
- **None of this runs inside the app yet.** The scan has to be copied off the phone and the scripts run by hand. See [scripts/scan_processing/README.md](../scripts/scan_processing/README.md).
- **No measurement has been checked against a tape or laser.** Every accuracy statement below is a consistency check between two methods, not ground truth.

The scans themselves are not in this repository. They contain photos of private interiors and people, and are kept locally.

## What the device runs showed

| Question from 10 October | Answer |
| --- | --- |
| Is the scan button enabled? | Yes. The plugin registration fix works. |
| Was depth recorded during a RoomPlan scan? | Yes, 256x192 depth and confidence for every keyframe. |
| How big is a raw bundle? | 27 to 86 MB for one room (the README's "roughly 100 MB" was an estimate). |
| Does the camera resume after "Next room"? | Still unknown. All seven scans were single-room sessions. |
| How accurate is it? | Not measured. |

Two things came up that the earlier research did not predict:

- **Rooms with columns or wall jogs did not get a floor.** Three of the four site rooms have 6 to 9 walls including short ones (0.2 to 0.8 m), and their wall ends did not chain into a closed outline. The normaliser now falls back to the floor polygon RoomPlan reports on iOS 17 and later.
- **RoomPlan gives walls of one room different heights** (2.12 m to 3.92 m in one site room) where beams, soffits or lofts meet them. It reports no ceiling surface at all, so the editor used to show rooms open at the top.

## What was built

### Editor (TypeScript)

- **Furniture shapes.** RoomPlan's object attributes (chair type, leg type, arms, storage type) are passed through and used to draw recognisable stand-in shapes; chair and sofa backs and bed pillows are marked on the floor plan. The sofa back was on the wrong side and is fixed.
- **`fixtures` on a room.** Wall-mounted electrical plates, placed like openings (offset along the wall, sill above its base), with kind, size, confidence and optional switch and socket counts. Shown in 3D, on the floor plan and in the inspector, where they can be edited or removed.
- **`ceiling` on a room.** A main height plus regions at other heights (lower: beams, soffits, lofts; higher: coffers). A Ceiling toggle draws a see-through lid with solid blocks hanging from it; the floor plan shows the regions as dashed outlines. Without measured data the lid sits at wall-top height.
- **Floor fallback** described above.
- Scale calibration scales fixtures and ceiling regions with the rest of the room.

### Scan post-processing (Python, `scripts/scan_processing/`)

- **`elevation.py`** projects keyframes onto each RoomPlan wall plane to make a straightened, fixed-scale photo of the wall, using depth to dim furniture standing in front of it.
- **`detect.py`** sends tiles of those photos to a vision model through OpenRouter, then re-renders each candidate from single frames and asks the model to confirm, classify and tighten it. Candidates seen through an open doorway are rejected with depth. The position on the wall comes from geometry, not from the model.
- **`ceiling.py`** takes only downward-facing surfaces above head height from the depth maps, builds a height map, splits it into levels and outlines the regions. No vision model.
- **`find_wall_units.py`** (experimental) finds flat faces 10 to 45 cm in front of a wall and flags those with the size and mounting height of a split AC. No vision model.

## Results

### Electrical plates (three home scans, model `google/gemini-3.8-flash`)

- 8 plates confirmed across the three scans. Every one was checked by eye on the annotated wall photo and sits on a real electrical plate.
- Two false candidates were located through open doorways in the next room; the depth check removed both.
- Two of the scans are of the same room. The same switch board came out 1.3 cm apart along the wall and 0.1 cm apart in height; the same junction box 1.7 cm and 1.6 cm apart. This is repeatability, not accuracy.
- The second scan of that room missed a low socket board that the first found, because a person and a basket were in front of it.
- Cost was USD 0.11 per room, 42 to 46 API calls each.
- Missed plates were not assessed: nobody has counted the real plates in those rooms.
- Switch and socket counts inside a board mostly came back empty because the keyframes are motion-blurred.

### Ceiling (four site scans)

| Room | Main ceiling | Other regions | Ceiling measured | Assessment |
| --- | --- | --- | --- | --- |
| 1 | 3.46 m | 3 | 47% | Plausible |
| 2 | 3.28 m | 14, including 8 coffers 0.25 m above | 83% | Matches a 3x3 coffered ceiling seen in the height map |
| 3 | 3.88 m | 14 | 52% | Not trustworthy; a cluttered patch in the middle of the room |
| 4 | 3.84 m | 2: a soffit at 2.85 m (4.89 x 0.89 m) and a box at 3.20 m (0.94 x 1.99 m) | 66% | Matches photos of the room |

In room 4 the three measured levels (3.84, 3.20, 2.85 m) agree with RoomPlan's wall heights (3.84, 3.22, 2.91 m), which come from a different method.

### AC units (depth only)

Three AC-like boxes were flagged in six rooms: one in site room 4 and two in site room 1. All three are AC units on the photos. More than 60 other boxes (cupboards, tables, a fridge) were found and none was flagged. Whether an AC was missed in the other rooms is not known.

## Independent review

A separate reviewer read the diff and the scripts without being told what the author believed, and confirmed most findings by running code on the sample scans. A data audit ran alongside it.

**Found correct:** matrix layout, projection signs, wall offsets and sills (three fixtures reprojected into raw frames land on the plates), wall start points against captures on all ten room files, and the Swift attribute export. Across all seven scans LiDAR depth agrees with RoomPlan's wall planes to within about 0 to 5 cm on most walls.

**Fixed after the review:**

| Defect | Effect |
| --- | --- |
| The room side of a wall was guessed from the room's centre | One short wall in site room 4 was rendered from behind (coverage 28%, 94.5% after the fix); plates there would have been mirrored and drawn on the outside face |
| The main ceiling was the highest level found, not the largest | In site room 1 a 1.8 m² patch became the ceiling and 8.8 m² of real ceiling became a "drop" |
| Region outlines were shifted and shrunk by 5 to 7 cm; beams under 20 cm vanished | The bars between coffers in site room 2 were cut |
| Neighbouring plates were merged by centre distance | Two sockets 10 cm apart became one |
| Scripts ran silently on an edited or rescaled room | Every plate would be misplaced with no warning; they now stop |
| One odd model reply could abort a run, and an unreadable reply was cached forever | |
| The on-screen marker around plates went into GLB exports; tile overlap was smaller than the largest board | |

**Still open from the review:**

- Moving a floor corner does not move fixtures or ceiling regions with it, and in rooms that use the scanned-floor fallback it moves the floor but no wall. This needs a design decision (snap corners to wall ends, or disable corner editing for scanned floors).
- The schema accepts a ceiling region outside the floor, a self-intersecting region outline, and two fixtures at the same spot.

## Verification

| Check | Result |
| --- | --- |
| Type check and production build | Passed |
| Unit and API tests (`npm test`) | 15 passed |
| Browser end-to-end tests (`npm run test:e2e`) | 2 passed |
| Signed build installed on iPhone 16 Pro and iPhone 14 Pro | Done from Xcode with a free Apple ID (expires after 7 days) |
| New unit tests for fixtures, ceiling and the floor fallback | **Not written** |
| CI for this session's commits | Not checked |

## Decisions and their reasons

1. **Detect on straightened wall photos, not on raw frames.** The plate is always seen square-on at a known scale, its position on the wall falls straight out of the image, and a room needs a handful of images rather than every keyframe.
2. **Vision model now, own on-device model later.** There is no labelled data for Indian switch boards. The model's detections, once corrected by users in the editor, are that data.
3. **Geometry for position, depth for big things only.** A switch plate stands about 1 cm proud of a wall, below what the depth map resolves; a forum post describes the 256x192 map as interpolated from a 24x24 grid of laser points. Depth is therefore used for ceilings, beams, AC units and doorway checks, not for small plates. Detecting empty electrical boxes by their recess is for the same reason unlikely to work from depth and has not been tried.
4. **The API key never goes in the app.** It can be extracted from an app bundle. Phone-side detection needs a server that holds the key.

## Not built yet

- The pipeline from phone to results: upload of the raw bundle, a server job that runs the scripts, results returned to the app. A local version with the Mac as the server on the same Wi-Fi is the smallest step.
- User consent for uploading interior photos. The README's statement that no camera images are uploaded is true of the app but not of `detect.py`.
- Empty electrical boxes, conduit pipes and holes on walls under construction. This needs scans of such a site; none of the seven has them.
- AC units as room elements in the editor.
- A sharpness check in the recorder and a close-up step for reading the contents of a switch board.
- Editing of ceiling regions in the editor.
- A combined plan of several rooms, the Android CI failure, and the unused "fallback wall height" field on iOS, all carried over from 10 October.

## What to do next

1. Measure. Tape-measure three or four plates and one beam in a scanned room and compare; count the real plates in the scanned rooms to learn the miss rate.
2. Scan with the ceiling in mind: stand in the middle of each room and sweep the camera across the whole ceiling once.
3. Decide the corner-editing question above, then write the missing tests.
4. Build the local phone-to-Mac pipeline so a scan produces fixtures and ceiling without manual steps.
