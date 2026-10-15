"""Put each wall where the LiDAR saw it and each corner where two real walls meet.

Usage: refit_walls.py <capture.json> <raw-bundle-dir> <room.json> <out-room.json>

RoomPlan squares every corner to exactly 90 degrees. Real rooms are 0.2-3 degrees out of square, so along a
4-5 m wall its corners can be 4-11 cm from the real ones. For each wall this script:
  1. takes the LiDAR points near RoomPlan's wall plane that face into the room, leaving out doors and windows;
  2. on each 50 cm stretch, finds the back-most surface with real support, band by band between 0.3 and 1.9 m
     (whiteboards, panels and cabinets hang in front of a wall, never behind it, and rarely cover it top to bottom), drops stretches that disagree with the rest, and fits a vertical
     plane through the others, letting it shift and turn a little;
  3. checks that small patches of the wall seen early and late in the scan agree, and keeps RoomPlan's line if
     they do not (the phone's tracking slipped, and the slip would be copied into the room);
  4. moves each corner to where the fitted planes of the two walls meeting there cross.
A wall seen too little keeps RoomPlan's line; a corner that would move implausibly far keeps RoomPlan's corner.
Doors, windows and electrical fixtures keep their place in the room; their offsets along the wall are recomputed.
Run it last: the other scripts expect RoomPlan's walls.
"""
import json
import sys
from pathlib import Path

import numpy as np

from elevation import Wall, depth_points

NEAR = 0.15           # points this close to RoomPlan's plane can belong to the wall
LAYER = 0.02          # points this close to the fitted plane count as the wall surface
FACING = 0.9          # point normal against the wall's inward direction
MIN_POINTS = 300
MIN_SPAN = 0.4        # the points must cover this share of the wall's length (and at least 30 cm)
MAX_TURN = np.radians(3)   # real rooms measured so far are 0.2-3 degrees out of square
MAX_CORNER_MOVE = 0.2
STRETCH = 0.5         # the wall is looked at in stretches this long
BAND = 0.4            # and, within a stretch, in height bands this tall
# A wall is measured where a tape would be held and where switches are. Above that, many walls step back (a ledge
# with the upper wall recessed) or carry whiteboards; the lower wall is what the room's size is measured against.
TAPE_LOW, TAPE_HIGH = 0.3, 1.5
BACK_SHARE = 0.25     # a layer needs this share of the stretch's densest layer to count as a surface
AGREE = 0.015         # stretches whose wall position is further than this from the line are dropped
MIN_AGREEING = 0.6    # at least this share of a wall's stretches must agree
MIN_TURN_SPAN = 1.5   # a wall seen along less than this may shift but not turn
MIN_TURN_STRETCHES = 5   # nor one with fewer agreeing stretches than this
CELL = 0.25           # wall patch size for the tracking check
MAX_SLIP = 0.025      # patches seen early and late in the scan must agree within this (90% of them)
MIN_CORNER_ANGLE = np.radians(20)  # walls meeting flatter than this do not give a reliable crossing


def fit_line(wall: Wall, a, d, h):
    """The wall surface as `across = offset + slope * along`, from points given by position along the wall (a),
    distance in front of RoomPlan's plane (d) and height (h). Returns (slope, offset, info) or (None, reason, None)."""
    # Whiteboards, panels, cabinets and pictures hang in front of a wall, never behind it: on each stretch the
    # wall is the back-most layer with real support, even when something in front of it has more points.
    stretches = []
    for s0 in np.arange(0, wall.width, STRETCH):
        backs = []
        # Look band by band up the wall: below a whiteboard a table may hide the wall, above it the wall shows.
        for b0 in np.arange(TAPE_LOW, TAPE_HIGH, BAND):
            part = (a >= s0) & (a < s0 + STRETCH) & (h >= b0) & (h < b0 + BAND)
            if part.sum() < 60:
                continue
            hist, edges = np.histogram(d[part], bins=np.arange(-NEAR, NEAR + 0.005, 0.005))
            support = np.convolve(hist, np.ones(3), 'same')
            solid = np.nonzero(support >= max(BACK_SHARE * support.max(), 30))[0]
            if not solid.size:
                continue
            # Pooling smears a layer by a bin either way; its peak is the layer itself.
            first = solid[0] + int(np.argmax(hist[solid[0]:solid[0] + 3]))
            backs.append(edges[first] + 0.0025)
        # The back-most surface that at least two bands agree on; one band alone may be a stray reading.
        backs = np.sort(backs)
        agreed = [v for v in backs if np.sum(np.abs(backs - v) <= 0.02) >= 2]
        if agreed:
            stretches.append((s0 + STRETCH / 2, agreed[0]))
    if len(stretches) < 2:
        return None, f'{len(stretches)} usable stretch', None
    centres, layers = np.array(stretches).T
    # Where something covers the wall top to bottom (a wardrobe, panelling), that stretch's back layer is the thing
    # in front. Drop stretches in front of the line first, so such a step is not mistaken for a turned wall.
    keep = np.ones(len(centres), bool)
    while keep.sum() > 2:
        slope, offset = np.polyfit(centres[keep], layers[keep], 1)
        error = np.where(keep, layers - (offset + slope * centres), 0.0)
        if error.max() > AGREE:
            keep[np.argmax(error)] = False
        elif error.min() < -2 * AGREE:
            keep[np.argmin(error)] = False
        else:
            break
    slope, offset = np.polyfit(centres[keep], layers[keep], 1)
    if keep.mean() < MIN_AGREEING:
        return None, f'only {keep.sum()} of {len(keep)} stretches agree on where the wall is (much of it covered)', None
    # Over a short run a centimetre of noise already reads as a turn of a degree or two: shift only.
    turnable = np.ptp(centres[keep]) + STRETCH >= MIN_TURN_SPAN and keep.sum() >= MIN_TURN_STRETCHES
    if not turnable:
        slope, offset = 0.0, float(np.median(layers[keep]))
    if np.ptp(centres[keep]) + STRETCH < max(0.3, MIN_SPAN * wall.width):
        return None, f'bare wall seen along only {(np.ptp(centres[keep]) + STRETCH) * 100:.0f} of {wall.width * 100:.0f} cm (something covers the rest)', None
    # Refine on the points of the agreeing stretches.
    for _ in range(3):
        inside = np.zeros(len(a), bool)
        for c in centres[keep]:
            inside |= np.abs(a - c) <= STRETCH / 2
        layer = inside & (np.abs(d - (offset + slope * a)) < LAYER)
        if layer.sum() < MIN_POINTS:
            return None, f'{layer.sum()} points on the surface', None
        slope, offset = np.polyfit(a[layer], d[layer], 1) if turnable else (0.0, float(np.median(d[layer])))
    span = np.percentile(a[layer], 97) - np.percentile(a[layer], 3)
    if span < max(0.3, MIN_SPAN * wall.width):
        return None, f'seen along {span * 100:.0f} of {wall.width * 100:.0f} cm', None
    if abs(np.arctan(slope)) > MAX_TURN:
        return None, f'fit turned {np.degrees(np.arctan(slope)):.1f} degrees', None
    spread = float(np.std(d[layer] - (offset + slope * a[layer])))
    return slope, offset, {'points': int(layer.sum()), 'spread': spread, 'span': span, 'stretches': f'{keep.sum()}/{len(keep)}'}


def fit_wall(wall: Wall, points, normals, frames, floor_y, openings):
    """The wall's real line in plan (a point and a direction in x/z), with how well it was seen, or None and why."""
    rel = points - wall.origin
    across, along, up = rel @ wall.inward, rel @ wall.axis_x + wall.width / 2, rel @ wall.axis_y
    height = points[:, 1] - floor_y
    near = ((np.abs(across) < NEAR) & (along > 0) & (along < wall.width) & (normals @ wall.inward > FACING)
            & (height > TAPE_LOW) & (height < min(TAPE_HIGH + BAND, wall.height - 0.15)) & (np.abs(up) < wall.height / 2))
    # Through a door or window the camera sees whatever is beyond it.
    for o in openings:
        near &= ~((along > o['offset'] - 0.05) & (along < o['offset'] + o['width'] + 0.05) & (height > o['sill'] - 0.05) & (height < o['sill'] + o['height'] + 0.05))
    if near.sum() < MIN_POINTS:
        return None, f'{near.sum()} points'
    a, d, h, seen_at = along[near], across[near], height[near], frames[near]
    slope, offset, info = fit_line(wall, a, d, h)
    if slope is None:
        return None, offset
    # Tracking check: small patches of the wall seen both early and late in the scan must agree. If the phone's
    # tracking slipped between visits, the patches disagree and the slip would be copied into the room.
    slips = []
    for s0 in np.arange(0, wall.width, CELL):
        for b0 in np.arange(0.15, wall.height, CELL):
            cell = (a >= s0) & (a < s0 + CELL) & (h >= b0) & (h < b0 + CELL)
            if cell.sum() < 200:
                continue
            hist, edges = np.histogram(d[cell], bins=np.arange(-NEAR, NEAR + 0.005, 0.005))
            on = cell & (np.abs(d - (edges[hist.argmax()] + 0.0025)) < 0.02)
            middle = np.median(seen_at[on])
            early, late = on & (seen_at <= middle), on & (seen_at > middle)
            if early.sum() >= 40 and late.sum() >= 40:
                slips.append(abs(np.median(d[late]) - np.median(d[early])))
    if len(slips) >= 5:
        info['slip'] = float(np.percentile(slips, 90))
        if info['slip'] > MAX_SLIP:
            return None, f'seen {info["slip"] * 100:.1f} cm apart early and late in the scan (tracking slipped)'
    flat = lambda v: np.array([v[0], v[2]])
    direction = flat(wall.axis_x) + slope * flat(wall.inward)
    start = flat(wall.origin) - flat(wall.axis_x) * wall.width / 2
    info.update(shift=offset + slope * wall.width / 2, turn=np.degrees(np.arctan(slope)), offset=offset, slope=slope)
    return (start + offset * flat(wall.inward), direction / np.linalg.norm(direction), info), None


def cross(p1, d1, p2, d2):
    """Where two plan lines meet, or None when they are too close to parallel."""
    det = d1[0] * -d2[1] - d1[1] * -d2[0]
    if abs(det) < np.sin(MIN_CORNER_ANGLE):
        return None
    t = ((p2 - p1)[0] * -d2[1] - (p2 - p1)[1] * -d2[0]) / det
    return p1 + t * d1


def main():
    capture = json.loads(Path(sys.argv[1]).read_text())
    raw, room_path, out_path = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    room = json.loads(room_path.read_text())
    scan_walls = {w['id']: Wall(w) for w in capture['walls']}
    floor_y = min(w.origin[1] - w.height / 2 for w in scan_walls.values())
    xy = lambda p: np.array([p['x'], p['z']])
    for w in room['walls']:
        scan = scan_walls.get(w['id'])
        start = (scan.origin - scan.axis_x * scan.width / 2) if scan else None
        if scan is None or abs(w['start']['x'] - start[0]) > 0.02 or abs(w['start']['z'] - start[2]) > 0.02:
            raise SystemExit('This room file no longer matches its scan (edited or rescaled). Refit the walls on the unedited room.')

    points, normals, frames = depth_points(raw, with_frames=True)
    lines, kept = {}, []
    for w in room['walls']:
        fitted, reason = fit_wall(scan_walls[w['id']], points, normals, frames, floor_y, [o for o in room.get('openings', []) if o['wallId'] == w['id']])
        old_start, old_end = xy(w['start']), xy(w['end'])
        if fitted:
            p, d, info = fitted
            if d @ (old_end - old_start) < 0:
                d = -d
            lines[w['id']] = (p, d, True)
            checked = f"early and late views agree within {info['slip'] * 100:.1f} cm" if 'slip' in info else 'tracking not cross-checked'
            print(f"{w['id'][:8]} {np.linalg.norm(old_end - old_start) * 100:5.0f} cm: moved {info['shift'] * 100:+.1f} cm, turned {info['turn']:+.2f} deg "
                  f"({info['points']} points along {info['span'] * 100:.0f} cm, {info['stretches']} stretches agree, surface within {info['spread'] * 1000:.0f} mm, {checked})")
        else:
            lines[w['id']] = (old_start, (old_end - old_start) / np.linalg.norm(old_end - old_start), False)
            kept.append(w['id'])
            print(f"{w['id'][:8]} {np.linalg.norm(old_end - old_start) * 100:5.0f} cm: kept as RoomPlan drew it ({reason})")

    # Corners: every wall end that meets another wall's start.
    ends = {}
    for w in room['walls']:
        for which in ('start', 'end'):
            ends[(w['id'], which)] = xy(w[which])
    new_ends, moved_corners, held = dict(ends), [], 0
    for a in room['walls']:
        for b in room['walls']:
            if a is b or np.linalg.norm(xy(a['end']) - xy(b['start'])) > 0.03:
                continue
            pa, da, fa = lines[a['id']]; pb, db, fb = lines[b['id']]
            old = (xy(a['end']) + xy(b['start'])) / 2
            if not (fa or fb):
                continue
            corner = cross(pa, da, pb, db)
            if corner is None:  # a straight run or a shallow bend: put the joint on each line where it was
                new_ends[(a['id'], 'end')] = pa + ((old - pa) @ da) * da
                new_ends[(b['id'], 'start')] = pb + ((old - pb) @ db) * db
                continue
            if np.linalg.norm(corner - old) > MAX_CORNER_MOVE:
                held += 1
                continue
            new_ends[(a['id'], 'end')] = new_ends[(b['id'], 'start')] = corner
            moved_corners.append((old, corner))
    # Free wall ends slide onto their wall's new line.
    for w in room['walls']:
        p, d, fitted = lines[w['id']]
        for which in ('start', 'end'):
            key = (w['id'], which)
            if fitted and np.array_equal(new_ends[key], ends[key]):
                new_ends[key] = p + ((ends[key] - p) @ d) * d

    def place(items, walls_before):
        """Keep each opening or fixture where it is in the room; work out its new offset along its wall."""
        for item in items:
            wall = walls_before.get(item['wallId'])
            if not wall:
                continue
            s0, e0 = xy(wall['start']), xy(wall['end'])
            anchor = s0 + (e0 - s0) / np.linalg.norm(e0 - s0) * item['offset']
            s1, e1 = new_ends[(wall['id'], 'start')], new_ends[(wall['id'], 'end')]
            length = float(np.linalg.norm(e1 - s1))
            offset = float((anchor - s1) @ ((e1 - s1) / length))
            item['offset'] = round(min(max(offset, 0.0), max(length - item['width'], 0.0)), 4)
            item['width'] = round(min(item['width'], length), 4)

    walls_before = {w['id']: json.loads(json.dumps(w)) for w in room['walls']}
    for w in room['walls']:
        for which in ('start', 'end'):
            w[which] = {'x': round(float(new_ends[(w['id'], which)][0]), 4), 'z': round(float(new_ends[(w['id'], which)][1]), 4)}
        if w['id'] not in kept:
            w['evidence'] = 'measured'
    place(room.get('openings', []), walls_before)
    place(room.get('fixtures', []), walls_before)

    # The floor outline follows the corners it shares with the walls.
    old_corners = list(ends.items())
    for point in room['floor']:
        p = xy(point)
        key, at = min(old_corners, key=lambda kv: np.linalg.norm(kv[1] - p))
        if np.linalg.norm(at - p) < 0.03:
            point['x'], point['z'] = round(float(new_ends[key][0]), 4), round(float(new_ends[key][1]), 4)

    note = f'Walls fitted to LiDAR; {len(room["walls"]) - len(kept)} of {len(room["walls"])} walls measured, corners no longer forced square.'
    if kept:
        note += f' {len(kept)} wall{"s" if len(kept) > 1 else ""} kept RoomPlan\'s line (too little bare wall seen, or the scan was not sure).'
    if held:
        note += f' {held} corner{"s" if held > 1 else ""} would have moved over {MAX_CORNER_MOVE * 100:.0f} cm and kept RoomPlan\'s position; check {"them" if held > 1 else "it"}.'
    room['warnings'] = [w for w in room.get('warnings', []) if not w.startswith('Walls fitted to LiDAR')][:49] + [note]
    out_path.write_text(json.dumps(room, indent=2))
    moves = [np.linalg.norm(new - old) * 100 for old, new in moved_corners]
    print(f"{room['name']}: {len(moved_corners)} corners moved (largest {max(moves, default=0):.1f} cm), {held} held, {len(kept)} walls kept")


if __name__ == '__main__':
    main()
