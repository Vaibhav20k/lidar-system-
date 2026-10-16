"""Check RoomPlan's doors and windows against the photos and the LiDAR, and find windows it missed. No vision model.

Usage: check_openings.py <capture.json> <raw-bundle-dir> <room.json> <out-room.json> [debug-dir]

RoomPlan sometimes misses a window, stretches a high window down to the floor and calls it a door, or takes a
lit picture for a window. Two measurements tell these apart:
  - glass is set back into the wall (the frame sits in the wall's thickness) or seen through (no surface at
    all), and in daylight it is brighter than the wall around it;
  - a picture is bright but flush with the wall; plain wall is flush and not bright.
So, wall by wall, this script maps brightness (from the stitched photos) and depth (from the LiDAR) on a 5 cm grid:
  1. bright cells that are set back or seen through form glass areas; each one big enough is a window;
  2. a RoomPlan window with no glass at all in it (solid wall at wall depth) is removed;
  3. a RoomPlan door whose lower part is solid wall at wall depth, with glass above, was a window: it becomes one;
  4. glass areas not covered by any opening become new windows.
Windows found or changed here are marked as estimated. Doors and openings that look like doors are not touched.
"""
import json
import sys
import uuid
from pathlib import Path

import cv2
import numpy as np

from elevation import build_wall, depth_points, load_scan

length = lambda w: float(np.hypot(w['end']['x'] - w['start']['x'], w['end']['z'] - w['start']['z']))

CELL = 0.05             # grid size, metres
BRIGHTER = 20           # glass is at least this much brighter (0-255) than the wall's typical brightness...
MIN_BRIGHT = 185        # ...and at least this bright
SET_BACK = 0.025        # glass sits at least this far behind the wall face
FLUSH = 0.015           # wall within this of the wall face counts as solid wall
MIN_WIDTH, MIN_HEIGHT = 0.5, 0.2
END_MARGIN = 0.1        # a window does not start right at the end of a wall (that is the corner's light)
DOOR_ABOVE = 0.35       # no windows looked for this far above a doorway (the next room's light shows through)
SET_BACK_SHARE = 0.4    # this share of a window's area must be set back or seen through
MIN_SILL = 0.3          # glass reaching lower than this is a glazed door or a reflection, not a window here
MIN_FILL = 0.7


def grids(wall, frames, points, normals, floor_y):
    """Brightness, depth relative to the wall face, and a seen-through flag, each on the wall's 5 cm grid."""
    ppm = 1 / CELL * 4
    image, source, _ = build_wall(wall, frames, ppm)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gray[source < 0] = np.nan
    rows, cols = int(round(wall.height / CELL)), int(round(wall.width / CELL))
    bright = np.full((rows, cols), np.nan, np.float32)
    for r in range(rows):
        for c in range(cols):
            patch = gray[r * 4:(r + 1) * 4, c * 4:(c + 1) * 4]
            if np.isfinite(patch).sum() > 4:
                bright[r, c] = np.nanmean(patch)
    rel = points - wall.origin
    a, b, n = rel @ wall.axis_x + wall.width / 2, wall.height / 2 - rel @ wall.axis_y, rel @ wall.inward
    on = (a >= 0) & (a < wall.width) & (b >= 0) & (b < wall.height)
    ix, iy = (a[on] / CELL).astype(int).clip(0, cols - 1), (b[on] / CELL).astype(int).clip(0, rows - 1)
    facing = (normals[on] @ wall.inward) > 0.8
    near = facing & (np.abs(n[on]) < 0.3)
    beyond = n[on] < -0.25
    depth = np.full((rows, cols), np.nan, np.float32)
    keys = iy[near] * cols + ix[near]
    order = np.argsort(keys)
    for key, start, count in zip(*np.unique(keys[order], return_index=True, return_counts=True)):
        if count >= 3:
            depth.flat[key] = np.median(n[on][near][order][start:start + count])
    seen_beyond = np.zeros((rows, cols), np.int32)
    np.add.at(seen_beyond, (iy[beyond], ix[beyond]), 1)
    seen_face = np.zeros((rows, cols), np.int32)
    np.add.at(seen_face, (iy[near], ix[near]), 1)
    # The wall face is the most common depth on the wall (the median is pulled by furniture and openings).
    values = depth[np.isfinite(depth)]
    face = 0.0
    if values.size:
        hist, edges = np.histogram(values, bins=np.arange(-0.3, 0.305, 0.005))
        face = float(edges[np.convolve(hist, np.ones(3), 'same').argmax()] + 0.0025)
    return bright, depth - face, (seen_beyond > 2 * seen_face) & (seen_beyond >= 3)


def main():
    capture_path, raw, room_path, out_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    debug = Path(sys.argv[5]) if len(sys.argv) > 5 else None
    capture, walls, frames = load_scan(capture_path, raw)
    room = json.loads(room_path.read_text())
    floor_y = min(w.origin[1] - w.height / 2 for w in walls)
    points, normals = depth_points(raw)
    by_id = {w['id']: w for w in room['walls']}
    kept, notes = [], []
    openings = room.get('openings', [])
    for wall in walls:
        if wall.id not in by_id or wall.width < 0.6:
            kept += [o for o in openings if o['wallId'] == wall.id]
            continue
        number = list(by_id).index(wall.id) + 1
        bright, depth, through = grids(wall, frames, points, normals, floor_y)
        rows, cols = bright.shape
        typical = np.nanmedian(bright)
        bright_enough = np.isfinite(bright) & (bright >= max(MIN_BRIGHT, typical + BRIGHTER))
        behind = through | (np.nan_to_num(depth, nan=-1) <= -SET_BACK)
        glass = bright_enough
        glass = cv2.morphologyEx(cv2.morphologyEx(glass.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        # Through a door or an open passage the camera sees the next room, often bright: never look for windows there.
        for o in [o for o in openings if o['wallId'] == wall.id and o['kind'] != 'window']:
            x0, x1 = int((o['offset'] - 0.1) / CELL), int((o['offset'] + o['width'] + 0.1) / CELL) + 1
            y0, y1 = int((wall.height - o['sill'] - o['height'] - DOOR_ABOVE) / CELL), int((wall.height - o['sill'] + 0.1) / CELL) + 1
            if o['kind'] == 'door' and o['width'] > 1.6:
                continue  # a "door" this wide may be a window RoomPlan stretched down; it is checked below
            glass[max(0, y0):y1, max(0, x0):x1] = 0
        solid = np.isfinite(depth) & (np.abs(depth) <= FLUSH) & ~through
        shapes = []
        count, labels, stats, _ = cv2.connectedComponentsWithStats(glass)
        for i in range(1, count):
            x, y, w, h, area = stats[i]
            sill = wall.height - (y + h) * CELL
            if (w * CELL >= MIN_WIDTH and h * CELL >= MIN_HEIGHT and sill >= MIN_SILL and area / (w * h) >= MIN_FILL
                    and x * CELL >= END_MARGIN and (x + w) * CELL <= wall.width - END_MARGIN):
                set_back = float(behind[labels == i].mean())
                shapes.append({'offset': x * CELL, 'width': w * CELL, 'sill': sill, 'height': h * CELL, 'set_back': set_back})
        # Bright alone could be a lit picture or a pale panel: a window must also sit back in the wall or be seen
        # through, or be the twin of one that does (same height band on the same wall), or overlap a RoomPlan window.
        sure = [g for g in shapes if g['set_back'] >= SET_BACK_SHARE]
        twin = lambda g: any(abs(g['sill'] - t['sill']) <= 0.06 and abs(g['height'] - t['height']) <= 0.1 for t in sure if t is not g)
        roomplan = lambda g: any(o['kind'] == 'window' and o['wallId'] == wall.id and not (g['offset'] + g['width'] <= o['offset'] or g['offset'] >= o['offset'] + o['width'])
                                 and not (g['sill'] + g['height'] <= o['sill'] or g['sill'] >= o['sill'] + o['height']) for o in openings)
        found = [g for g in shapes if g in sure or twin(g) or roomplan(g)]
        cell_box = lambda o: (int(o['offset'] / CELL), int((wall.height - o['sill'] - o['height']) / CELL), max(1, int(o['width'] / CELL)), max(1, int(o['height'] / CELL)))
        share = lambda mask, box: float(mask[max(0, box[1]):box[1] + box[3], max(0, box[0]):box[0] + box[2]].mean()) if box[2] > 0 and box[3] > 0 else 0.0
        used = set()
        for o in [o for o in openings if o['wallId'] == wall.id]:
            box = cell_box(o)
            inside = [k for k, g in enumerate(found) if not (g['offset'] + g['width'] <= o['offset'] or g['offset'] >= o['offset'] + o['width'])
                      and not (g['sill'] + g['height'] <= o['sill'] or g['sill'] >= o['sill'] + o['height'])]
            if o['kind'] == 'window':
                if not inside and share(solid, box) >= 0.8:
                    notes.append(f"wall {number}: removed a {o['width']:.2f} m window with solid wall behind it (a picture or panel)")
                    continue
                used.update(inside)
                kept.append(o)
            elif o['kind'] == 'door' and inside:
                used.update(inside)  # glass seen through this door belongs to it, not to a new window
                lower = (box[0], int((wall.height - min(1.8, o['sill'] + o['height'])) / CELL), box[2], int((min(1.8, o['sill'] + o['height']) - max(0.3, o['sill'])) / CELL))
                if share(solid, lower) >= 0.8 and share(through, lower) < 0.05:
                    notes.append(f"wall {number}: a {o['width']:.2f} x {o['height']:.2f} m 'door' has solid wall below and glass above; it is a window")
                    continue
                kept.append(o)
            else:
                kept.append(o)
        for k, g in enumerate(found):
            if k in used:
                continue
            kept.append({'id': str(uuid.uuid4()), 'wallId': wall.id, 'kind': 'window', 'offset': round(g['offset'], 3), 'width': round(min(g['width'], length(by_id[wall.id]) - g['offset'] - 0.005), 3),
                         'height': round(g['height'], 3), 'sill': round(g['sill'], 3), 'evidence': 'estimated'})
            notes.append(f"wall {number}: window found from glass, {g['offset']:.2f}-{g['offset'] + g['width']:.2f} m along, {g['width']:.2f} x {g['height']:.2f} m, sill {g['sill']:.2f} m")
        if debug:
            debug.mkdir(parents=True, exist_ok=True)
            view = np.zeros((rows, cols, 3), np.uint8)
            view[..., 1] = np.nan_to_num(bright, nan=0).astype(np.uint8)
            view[solid] = (90, 90, 90)
            view[glass.astype(bool)] = (255, 200, 0)
            cv2.imwrite(str(debug / f'wall{number}_glass.png'), cv2.resize(view, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST))
    room['openings'] = kept
    if notes:
        room['warnings'] = [w for w in room.get('warnings', []) if not w.startswith('Doors and windows checked')][:49] + [
            'Doors and windows checked against photos and depth: ' + '; '.join(notes)[:430]]
    out_path.write_text(json.dumps(room, indent=2))
    print(f"{room['name']}: " + ('; '.join(notes) if notes else 'doors and windows agree with photos and depth'))


if __name__ == '__main__':
    main()
