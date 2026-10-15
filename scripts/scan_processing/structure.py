"""Find the built structure RoomPlan flattens away: columns and pilasters standing out from walls, boxed
corners, niches set into walls, and beams or dropped ceilings, each with measured width, depth and height.

Usage: structure.py <capture.json> <raw-bundle-dir> <room.json> <out-room.json> [debug-dir]

Everything is measured from LiDAR depth, with no vision model:
  - Walls: faces parallel to a wall are mapped by how far they stand in front of (or behind) it. A strip
    that stands proud over most of the height above furniture is a column; one set back is a niche.
  - Ceiling: the regions ceiling.py measured are fitted with rectangles and reported as beams (long and
    narrow) or dropped ceilings, with their drop below the main ceiling.
Run ceiling.py on the room first. Results are written into the room file as `structure`.
"""
import json
import sys
import uuid
from pathlib import Path

import cv2
import numpy as np

from elevation import Wall, depth_points, matrix

CELL = 0.04          # meters per cell of the wall map
PROUD = 0.06         # a face this far in front of the wall surface is not the wall
ABOVE_FURNITURE = 1.9  # judge columns above this height, where little furniture reaches
MIN_WALL = 0.6


def wall_map(wall, points, normals, floor_y, top):
    """Per cell of a wall (along x height above floor): how far the visible parallel face stands from the wall plane."""
    rel = points - wall.origin
    a, n = rel @ wall.axis_x, rel @ wall.inward
    h = points[:, 1] - floor_y
    keep = (np.abs(a) < wall.width / 2) & (h > 0.05) & (h < top) & (n > -0.6) & (n < 1.6) & (normals @ wall.inward > 0.8)
    cols, rows = max(1, int(wall.width / CELL)), max(1, int(top / CELL))
    ix = ((a[keep] + wall.width / 2) / CELL).astype(int).clip(0, cols - 1)
    iy = (h[keep] / CELL).astype(int).clip(0, rows - 1)
    # Median per cell: sort by cell then by distance, and take each cell's middle value.
    cell = iy * cols + ix
    order = np.lexsort((n[keep], cell))
    cell, value = cell[order], n[keep][order]
    starts = np.flatnonzero(np.r_[True, cell[1:] != cell[:-1]])
    counts = np.diff(np.r_[starts, len(cell)])
    stand = np.full(rows * cols, np.nan, np.float32)
    enough = counts >= 4
    stand[cell[starts][enough]] = value[(starts + counts // 2)[enough]]
    stand = stand.reshape(rows, cols)
    # The wall surface itself is wherever most of the map sits; RoomPlan's plane can be a few cm off it.
    seen = stand[~np.isnan(stand)]
    if seen.size < 200:
        return None, 0.0
    # Look only near the scanned plane: a wardrobe covering most of a wall must not be mistaken for the wall.
    hist, edges = np.histogram(seen, bins=np.arange(-0.15, 0.15, 0.01))
    if hist.sum() < 200:
        return None, 0.0
    surface = float(edges[hist.argmax()] + 0.005)
    return stand - surface, surface


def runs(flags):
    """Start and end (exclusive) of each run of True."""
    padded = np.r_[False, flags, False].astype(np.int8)
    change = np.flatnonzero(np.diff(padded))
    return list(zip(change[::2], change[1::2]))


def wall_features(wall, number, stand, top, objects):
    """Columns (proud strips) and niches (set-back strips) on one wall."""
    rows, cols = stand.shape
    band = slice(int(ABOVE_FURNITURE / CELL), max(int(ABOVE_FURNITURE / CELL) + 3, int((top - 0.25) / CELL)))
    upper = stand[band]
    observed = ~np.isnan(upper)
    features = []
    for kind, sign in (('column', 1), ('niche', -1)):
        strong = np.where(observed, sign * upper > PROUD, False)
        # A strip counts where most of what was seen above furniture is proud, and enough was seen to say so.
        share = strong.sum(axis=0) / np.maximum(observed.sum(axis=0), 1)
        strip = (share > 0.7) & (observed.sum(axis=0) > 0.35 * upper.shape[0])
        strip = cv2.morphologyEx(strip.astype(np.uint8)[None, :], cv2.MORPH_CLOSE, np.ones((1, 3), np.uint8))[0].astype(bool)
        for start, end in runs(strip):
            width = (end - start) * CELL
            if not (0.12 <= width <= 1.6):
                continue
            part = stand[:, start:end]
            proud = sign * part > PROUD
            depth_values = sign * part[proud]
            if depth_values.size < 30:
                continue
            depth = float(np.median(depth_values))
            heights = np.flatnonzero(proud.any(axis=1))
            # Where furniture hid the lower part, nothing contradicts the strip continuing to the floor.
            seen_rows = np.flatnonzero((~np.isnan(part)).any(axis=1))
            hidden_below = seen_rows.size == 0 or heights[0] <= seen_rows[0] + 2
            bottom = 0.0 if hidden_below or heights[0] * CELL < 0.3 else float(heights[0] * CELL)
            offset = start * CELL
            center = offset + width / 2 - wall.width / 2
            # A wardrobe or tall cabinet against the wall is furniture, not structure.
            if kind == 'column' and any(abs(o['along'] - center) < o['width'] / 2 + 0.1 and depth < o['stand'] + 0.15 for o in objects):
                continue
            features.append({
                'kind': kind, 'wallId': wall.id, 'wall': number, 'offset': round(float(offset), 3), 'width': round(float(width), 3), 'depth': round(depth, 3),
                'bottom': round(bottom, 3), 'top': round(float(min(top, (heights[-1] + 1) * CELL)), 3),
                'atCorner': bool(start <= 2 or end >= cols - 2), 'depthSpread': round(float(np.std(depth_values)), 3),
                'seen': round(float(observed[:, start:end].mean()), 2),
            })
    return features


def objects_on_wall(wall, capture):
    """RoomPlan furniture standing against this wall: where it is along the wall, how wide, and how far its front stands out."""
    found = []
    for o in capture['objects']:
        m = matrix(o['transform'])
        rel = m[:3, 3] - wall.origin
        half_w, half_d = o['width'] / 2, o.get('depth', 0) / 2
        # The box may be turned either way relative to the wall, so project its two horizontal axes onto the wall's.
        along = abs(m[:3, 0] @ wall.axis_x) * half_w + abs(m[:3, 2] @ wall.axis_x) * half_d
        out = abs(m[:3, 0] @ wall.inward) * half_w + abs(m[:3, 2] @ wall.inward) * half_d
        if abs(rel @ wall.axis_x) < wall.width / 2 + along and -0.2 < rel @ wall.inward - out < 0.6 and o['height'] > 1.2:
            found.append({'along': float(rel @ wall.axis_x), 'width': float(2 * along), 'stand': float(rel @ wall.inward + out)})
    return found


def ceiling_features(room):
    """Fit a rectangle to every measured ceiling region and name it by shape."""
    ceiling = room.get('ceiling') or {}
    main = ceiling.get('height')
    features = []
    for region in ceiling.get('regions', []):
        outline = np.array([[p['x'], p['z']] for p in region['points']], np.float32)
        (cx, cz), (side_a, side_b), angle = cv2.minAreaRect(outline)
        length, width = max(side_a, side_b), min(side_a, side_b)
        if width < 0.12 or length < 0.3:
            continue
        drop = main - region['height']
        fill = cv2.contourArea(outline) / max(side_a * side_b, 1e-6)
        if drop > 0.05:
            kind = 'beam' if length >= 2.2 * width and width <= 1.3 else 'dropped_ceiling'
        elif drop < -0.05:
            kind = 'recess'
        else:
            continue
        # minAreaRect's angle belongs to side_a; the long axis is the direction a beam runs.
        along = np.radians(angle if side_a >= side_b else angle + 90)
        features.append({
            'kind': kind, 'x': round(float(cx), 3), 'z': round(float(cz), 3), 'rotation': round(float(along), 4),
            'length': round(float(length), 3), 'width': round(float(width), 3), 'underside': round(region['height'], 3),
            'drop': round(float(drop), 3), 'rectangular': round(float(fill), 2),
        })
    return features


def plan_features(room):
    """Shapes in the room's outline: boxed corners, columns standing out of a wall, niches and steps in a wall.

    Read from the floor polygon alone. Walking round the room with the inside on one hand, a turn into the room
    is a reflex corner; the pattern of reflex corners and the lengths of the edges at them say what the shape is.
    """
    points = [(p['x'], p['z']) for p in room.get('floor', [])]
    count = len(points)
    if count < 5:
        return []
    area = sum(points[i][0] * points[(i + 1) % count][1] - points[(i + 1) % count][0] * points[i][1] for i in range(count))
    orientation = 1 if area > 0 else -1
    edge = lambda i: float(np.hypot(points[(i + 1) % count][0] - points[i % count][0], points[(i + 1) % count][1] - points[i % count][1]))

    def reflex(i):
        (ax, az), (bx, bz), (cx, cz) = points[(i - 1) % count], points[i % count], points[(i + 1) % count]
        return ((bx - ax) * (cz - bz) - (bz - az) * (cx - bx)) * orientation < 0

    turns = [reflex(i) for i in range(count)]
    SHORT = 1.3
    found = []
    for i in range(count):
        before, after, nxt = turns[(i - 1) % count], turns[(i + 1) % count], turns[(i + 2) % count]
        at = {'x': round(points[i][0], 3), 'z': round(points[i][1], 3)}
        if turns[i] and after and not before and not nxt and edge(i) <= 1.6 and min(edge(i - 1), edge(i + 1)) <= SHORT:
            # Two reflex corners in a row: something stands out of an otherwise straight wall.
            found.append({'kind': 'column', 'width': round(edge(i), 3), 'depth': round((edge(i - 1) + edge(i + 1)) / 2, 3), 'sides': [round(edge(i - 1), 3), round(edge(i + 1), 3)], **at})
        elif not turns[i] and not after and before and nxt and edge(i) <= 2.0 and min(edge(i - 1), edge(i + 1)) <= SHORT:
            found.append({'kind': 'niche', 'width': round(edge(i), 3), 'depth': round((edge(i - 1) + edge(i + 1)) / 2, 3), 'sides': [round(edge(i - 1), 3), round(edge(i + 1), 3)], **at})
        elif turns[i] and not before and not after:
            a, b = edge(i - 1), edge(i)
            if max(a, b) <= SHORT:
                found.append({'kind': 'boxed_corner', 'width': round(max(a, b), 3), 'depth': round(min(a, b), 3), 'sides': [round(a, 3), round(b, 3)], **at})
            elif min(a, b) <= SHORT:
                found.append({'kind': 'wall_step', 'width': round(max(a, b), 3), 'depth': round(min(a, b), 3), 'sides': [round(a, 3), round(b, 3)], **at})
    return found


def main():
    capture = json.loads(Path(sys.argv[1]).read_text())
    raw, room_path, out_path = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    debug = Path(sys.argv[5]) if len(sys.argv) > 5 else None
    room = json.loads(room_path.read_text())
    walls = [Wall(w) for w in capture['walls']]
    floor_y = min(w.origin[1] - w.height / 2 for w in walls)
    top = (room.get('ceiling') or {}).get('height') or max(w.height for w in walls)
    points, normals = depth_points(raw)
    if debug:
        debug.mkdir(parents=True, exist_ok=True)

    on_walls = []
    for number, wall in enumerate(walls, start=1):
        if wall.width < MIN_WALL:
            continue
        # Each wall is judged up to its own top: above that is a beam or lower ceiling, which the ceiling pass measures.
        wall_top = min(top, wall.origin[1] + wall.height / 2 - floor_y)
        stand, surface = wall_map(wall, points, normals, floor_y, wall_top)
        if stand is None:
            continue
        found = wall_features(wall, number, stand, wall_top, objects_on_wall(wall, capture))
        on_walls += found
        if debug:
            image = cv2.applyColorMap((np.clip((np.nan_to_num(stand, nan=0) + 0.3) / 0.9, 0, 1) * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
            image[np.isnan(stand)] = 0
            image = cv2.flip(cv2.resize(image, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST), 0)
            for f in found:
                x0, x1 = int(f['offset'] / CELL * 4), int((f['offset'] + f['width']) / CELL * 4)
                cv2.rectangle(image, (x0, image.shape[0] - int(f['top'] / CELL * 4)), (x1, image.shape[0] - int(f['bottom'] / CELL * 4) - 1), (255, 255, 255), 2)
            cv2.imwrite(str(debug / f'wall{number}_stand.png'), image)
        print(f'wall {number}: {wall.width:.2f} m long, judged to {wall_top:.2f} m, surface {surface * 100:+.1f} cm from the scanned plane, {len(found)} feature(s)')

    overhead = ceiling_features(room)
    outline = plan_features(room)
    room['structure'] = {
        'plan': [{'id': str(uuid.uuid4()), 'evidence': 'measured', **f} for f in outline],
        'walls': [{'id': str(uuid.uuid4()), 'evidence': 'estimated', **f} for f in on_walls],
        'ceiling': [{'id': str(uuid.uuid4()), 'evidence': 'estimated', **f} for f in overhead],
    }
    out_path.write_text(json.dumps(room, indent=2))
    inch = lambda meters: f'{meters / 0.0254:.1f} in'
    print(f"\n{room['name']}: {len(outline)} in the outline, {len(on_walls)} on walls, {len(overhead)} overhead")
    for f in outline:
        note = f"step of {f['depth']:.2f} m ({inch(f['depth'])}) in the wall line" if f['kind'] == 'wall_step' else f"{f['width']:.2f} x {f['depth']:.2f} m ({inch(f['width'])} x {inch(f['depth'])})"
        print(f"  {f['kind']:<12} {note}; edges {f['sides'][0]:.2f} and {f['sides'][1]:.2f} m")
    for f in on_walls:
        where = 'at the corner' if f['atCorner'] else f"{f['offset']:.2f} m from the wall start"
        print(f"  {f['kind']:<7} wall {f['wall']} {where}: {f['width']:.2f} m wide ({inch(f['width'])}), stands {'out' if f['kind'] == 'column' else 'back'} {f['depth']:.2f} m ({inch(f['depth'])}), from {f['bottom']:.2f} to {f['top']:.2f} m; seen {f['seen']:.0%}, depth varies ±{f['depthSpread'] * 100:.0f} cm")
    for f in overhead:
        print(f"  {f['kind']:<15} {f['length']:.2f} x {f['width']:.2f} m ({inch(f['length'])} x {inch(f['width'])}), underside at {f['underside']:.2f} m ({inch(f['underside'])}), {abs(f['drop']):.2f} m ({inch(abs(f['drop']))}) {'below' if f['drop'] > 0 else 'above'} the ceiling; {f['rectangular']:.0%} rectangular")


if __name__ == '__main__':
    main()
