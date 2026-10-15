"""Measure every height from the real floor, as the LiDAR saw it, instead of RoomPlan's single flat floor.

Usage: floor_level.py <capture.json> <raw-bundle-dir> <room.json> <out-room.json>

RoomPlan puts one flat floor under the whole room at the bottom of its lowest wall. Real floors are a few cm off
that, slope a little, and some rooms have a raised platform or a step RoomPlan ignores. This script:
  1. maps the floor inside the room on a 10 cm grid from upward-facing LiDAR points near floor height;
  2. takes each cell's lowest solid surface (mats and boxes lie on the floor), groups the cells into levels and
     drops small ones (a stool, a low box), keeping steps and platforms;
  3. takes the largest level as the room's floor (height zero) and shifts every height in the room to it;
  4. gives a wall standing on a real step or platform (5 cm or more, 1 m2 or more) that level as its base, so a
     switch, socket, door or window there is measured from the floor right under it. Wall tops stay where they were.
A floor too scattered to trust (a polished floor reflects the LiDAR) leaves every height as RoomPlan measured it.
Run it last: it changes the heights the other scripts measured from RoomPlan's floor.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from elevation import Wall, depth_points

CELL = 0.10           # floor map grid, metres
BELOW, ABOVE = 0.10, 0.25   # look for floor this far below / above RoomPlan's floor (a platform or a step is within it)
FACING_UP = 0.95
MIN_CELL_POINTS = 15
LEVEL_BAND = 0.025    # cells within this of a level's peak belong to it
MIN_LEVEL_AREA = 0.25 # square metres a level needs to count as floor
ALONG_WALL = 0.6      # a wall's base is the floor up to this far in front of it
MIN_WALL_CELLS = 5
NOTE_STEP = 0.03      # a wall whose floor differs from the room's floor by more than this gets a note
MIN_MAIN_SHARE = 0.6  # the main level must be this share of the measured floor, or the floor is not trusted
MIN_STEP, MIN_STEP_AREA = 0.05, 1.0   # a step or platform: at least this high or low, over at least this many m2
MOST = 0.7            # a wall stands on a step when this share of the floor along it is that step


def floor_map(points, normals, floor_y, outline):
    """Floor height per grid cell inside the room (relative to RoomPlan's floor), with cells of small levels removed."""
    up = (normals[:, 1] > FACING_UP) & (points[:, 1] > floor_y - BELOW) & (points[:, 1] < floor_y + ABOVE)
    xz, height = points[up][:, [0, 2]], points[up, 1] - floor_y
    origin = outline.min(axis=0) - CELL
    cols, rows = (np.ceil((outline.max(axis=0) + CELL - origin) / CELL)).astype(int)
    inside = np.zeros((rows, cols), np.uint8)
    cv2.fillPoly(inside, [np.round((outline - origin) / CELL - 0.5).astype(np.int32)], 1)
    ix, iz = ((xz - origin) / CELL).astype(int).T
    ok = (ix >= 0) & (ix < cols) & (iz >= 0) & (iz < rows)
    ix, iz, height = ix[ok], iz[ok], height[ok]
    ok = inside[iz, ix].astype(bool)
    ix, iz, height = ix[ok], iz[ok], height[ok]
    cells = {}
    order = np.lexsort((ix, iz))
    keys = iz[order] * cols + ix[order]
    for key, start, count in zip(*np.unique(keys, return_index=True, return_counts=True)):
        if count >= MIN_CELL_POINTS:
            # Mats, bed bases and boxes lie on the floor, never under it: the floor is the lowest solid surface.
            values = np.sort(height[order][start:start + count])
            cells[int(key)] = float(np.median(values[: max(MIN_CELL_POINTS, count // 4)]))
    if not cells:
        return None
    # Levels are peaks in the cell heights: take the biggest, remove its cells, repeat. A floor that only slopes
    # gently stays one level; a platform or a step a few cm up becomes its own.
    keys = np.array(list(cells)); values = np.array([cells[k] for k in keys])
    free, levels = np.ones(len(keys), bool), []
    while free.sum() * CELL * CELL >= MIN_LEVEL_AREA:
        hist, edges = np.histogram(values[free], bins=np.arange(-BELOW, ABOVE + 0.01, 0.01))
        peak = edges[np.argmax(np.convolve(hist, np.ones(3), 'same'))] + 0.005
        members = free & (np.abs(values - peak) <= LEVEL_BAND)
        if members.sum() * CELL * CELL < MIN_LEVEL_AREA:
            break
        levels.append((members.sum() * CELL * CELL, float(np.median(values[members])), set(keys[members].tolist())))
        free &= ~members
    if not levels:
        return None
    kept = set().union(*(members for _, _, members in levels))
    centres = {k: origin + (np.array([k % cols, k // cols]) + 0.5) * CELL for k in kept}
    return {k: (centres[k], cells[k]) for k in kept}, sorted(levels, key=lambda l: -l[0])


def main():
    capture = json.loads(Path(sys.argv[1]).read_text())
    raw, room_path, out_path = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    room = json.loads(room_path.read_text())
    walls = [Wall(w) for w in capture['walls']]
    floor_y = min(w.origin[1] - w.height / 2 for w in walls)
    if any(abs(w.get('baseY', 0)) > 1e-6 for w in room['walls']):
        raise SystemExit('This room already has wall bases set (heights measured, or edited). Measure the floor on the room as scanned.')
    if len(room['floor']) < 3:
        raise SystemExit('This room has no floor outline, so the floor cannot be bounded.')
    outline = np.array([[p['x'], p['z']] for p in room['floor']])
    points, normals = depth_points(raw)
    found = floor_map(points, normals, floor_y, outline)
    if not found:
        raise SystemExit('Too little of the floor was scanned to measure it.')
    cells, levels = found
    main_area, main_level, _ = levels[0]
    print(f"{room['name']}: floor {main_level * 100:+.1f} cm from RoomPlan's, over {main_area:.1f} m2; "
          f"other levels: {', '.join(f'{(h - main_level) * 100:+.0f} cm over {a:.1f} m2' for a, h, _ in levels[1:]) or 'none'}")

    total = sum(a for a, _, _ in levels)
    if main_area < MIN_MAIN_SHARE * total:
        # A polished or wet floor reflects the LiDAR and scatters it into many levels: none of them can be trusted.
        room['warnings'] = [x for x in room.get('warnings', []) if not x.startswith('Heights measured from')][:49] + [
            f"Heights measured from RoomPlan's floor: the LiDAR floor was too scattered to use (main level only {main_area / total:.0%} of it; a shiny floor?)."]
        out_path.write_text(json.dumps(room, indent=2))
        print(f'  floor too scattered ({main_area / total:.0%} on the main level): heights left on RoomPlan\'s floor')
        return
    # Steps and platforms: levels clearly above or below the floor, big enough to stand on.
    steps = [(h, members) for a, h, members in levels[1:] if a >= MIN_STEP_AREA and abs(h - main_level) >= MIN_STEP]
    keys = list(cells)
    centres = np.array([cells[k][0] for k in keys])
    level_of = np.full(len(keys), -1)
    for index, (_, members) in enumerate(steps):
        level_of[[i for i, k in enumerate(keys) if k in members]] = index
    notes = []
    for w in room['walls']:
        s, e = np.array([w['start']['x'], w['start']['z']]), np.array([w['end']['x'], w['end']['z']])
        length = np.linalg.norm(e - s); t = (e - s) / length
        # The room is on the side of the wall where the floor outline is: try both and keep the one with floor.
        rel = centres - s; along, across = rel @ t, rel @ np.array([-t[1], t[0]])
        best = None
        for side in (1, -1):
            near = (along > 0) & (along < length) & (side * across > 0) & (side * across < ALONG_WALL)
            if best is None or near.sum() > best.sum():
                best = near
        # A wall takes a step's height only when most of the floor along it is that step. Smaller differences are
        # within what LiDAR measures on a floor seen at a shallow angle, so the wall stays on the room's floor.
        base = 0.0
        if best.sum() >= MIN_WALL_CELLS:
            counts = np.bincount(level_of[best] + 1, minlength=len(steps) + 1)
            if counts[1:].size and counts[1:].max() >= MOST * best.sum():
                base = steps[int(counts[1:].argmax())][0] - main_level
        top = w.get('baseY', 0.0) + w['height']
        for item in [o for o in room.get('openings', []) if o['wallId'] == w['id']]:
            item_top = item['sill'] + item['height'] - main_level - base
            item['sill'] = round(max(0.0, item['sill'] - main_level - base), 4)
            item['height'] = round(max(0.1, item_top - item['sill']), 4)
        for item in [f for f in room.get('fixtures', []) if f['wallId'] == w['id']]:
            item['sill'] = round(max(0.0, item['sill'] - main_level - base), 4)
        w['baseY'] = round(base, 4)
        w['height'] = round(top - main_level - base, 4)
        if abs(base) > NOTE_STEP:
            notes.append(f"{abs(base) * 100:.0f} cm {'higher' if base > 0 else 'lower'} along the {length:.2f} m wall")
        if base:
            print(f"  wall {w['id'][:8]} {length:.2f} m: stands on a step {base * 100:+.1f} cm from the room's floor ({int(best.sum())} cells)")

    ceiling = room.get('ceiling')
    if ceiling:
        ceiling['height'] = round(ceiling['height'] - main_level, 3)
        for region in ceiling.get('regions', []):
            region['height'] = round(region['height'] - main_level, 3)
        for item in ceiling.get('items', []):
            item['height'] = round(item['height'] - main_level, 3)
    for o in room.get('objects', []):
        o['position']['y'] = round(o['position']['y'] - main_level, 4)

    note = f"Heights measured from the LiDAR floor ({main_level * 100:+.1f} cm from RoomPlan's)."
    if notes:
        note += ' Floor not level: ' + '; '.join(notes) + '. Heights on those walls are from the floor next to them.'
    room['warnings'] = [x for x in room.get('warnings', []) if not x.startswith('Heights measured from the LiDAR floor')][:49] + [note]
    out_path.write_text(json.dumps(room, indent=2))


if __name__ == '__main__':
    main()
