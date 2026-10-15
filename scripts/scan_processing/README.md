# Scan post-processing

Python scripts that add what RoomPlan does not report to a Roomform room file, using the raw bundle the
iOS scanner saves beside each scan (`<id>-raw/`: JPEG keyframes, LiDAR depth, confidence, camera poses).
After a scan the phone uploads the bundle and the Roomform server (`server/scans.ts`) runs them in this order:
`ceiling.py`, `refit_objects.py`, `detect.py` + `apply_fixtures.py` (only with `ROOMFORM_DETECT=1`),
`refit_walls.py` (unless `ROOMFORM_REFIT_WALLS=0`), `floor_level.py`. They can also be run by hand.

| Script | Adds | Uses a vision model |
| --- | --- | --- |
| `elevation.py` | A straightened photo of every wall, stitched from keyframes | No |
| `detect.py` | Switch boards, switches and sockets, placed on their wall | Yes (OpenRouter) |
| `apply_fixtures.py` | Writes `detect.py` results into a room file as `fixtures` | No |
| `ceiling.py` | Ceiling height plus beams, soffits, lofts and recesses as `ceiling` | No |
| `refit_objects.py` | Furniture box widths pulled in to the edges seen on the photos | No |
| `refit_walls.py` | Walls fitted to LiDAR and corners where real walls meet (not forced to 90°) | No |
| `floor_level.py` | Heights measured from the LiDAR floor; walls on a real step or platform get its level | No |
| `cloud.py` | Coloured point cloud for the editor's LiDAR scan view | No |
| `find_wall_units.py` | Experimental: AC-shaped boxes mounted high on walls | No |

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`detect.py` needs `OPENROUTER_API_KEY` in the environment or in a `.env` file here or at the repo root.
`.env` is git-ignored. **Running `detect.py` uploads photos of the scanned walls to OpenRouter and the
model provider**; `--dry-run` only writes the tiles it would send. A four-wall room cost about USD 0.11.

## Typical run

`<id>.json` is the capture the scanner writes, `<id>-raw` its raw bundle, and `room.json` the room exported
from the editor (or produced by `normalizeCapture`) **before any edits**: the scripts refuse a room that has
been rescaled or had corners moved, because depth and photos are in scan coordinates.

```sh
.venv/bin/python detect.py <id>.json <id>-raw out/<name>
.venv/bin/python apply_fixtures.py room.json out/<name>/fixtures.json out/<name>/room.json
.venv/bin/python ceiling.py <id>.json <id>-raw out/<name>/room.json out/<name>/room.json
```

Import the resulting room file in the editor. `out/<name>/wallN_detected.jpg` shows each confirmed plate.

## Limits worth knowing

- Positions are a ray-to-wall-plane intersection, so they inherit RoomPlan's wall-plane error (a few cm).
- `refit_walls.py` and `floor_level.py` must run last: the other scripts expect RoomPlan's walls and floor.
  A wall mostly covered (whiteboards, wardrobes, panelling) keeps RoomPlan's line, and so does a shiny floor.
  Two phones scanning the same room still disagreed on one wall's angle; this has not been checked against a
  tape measure yet.
- A plate hidden behind furniture or a person during the scan cannot be found.
- Keyframes are often motion-blurred, so counts of switches and sockets inside a board are usually missing.
- Ceiling regions are only as complete as the scan: cells the camera never looked at are assumed to be at
  the main ceiling height. Point the camera at the whole ceiling once per room.
- Pipes, conduits and wires are too thin for the depth map and are not detected.
