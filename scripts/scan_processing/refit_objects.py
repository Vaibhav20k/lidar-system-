"""Tighten RoomPlan's furniture boxes to the furniture's real edges.

Usage: refit_objects.py <capture.json> <raw-bundle-dir> <room.json> <out-room.json> [debug-dir]

RoomPlan draws each piece of furniture as a generous box: a wardrobe comes out wider than it is, taking in the
gap beside it and some of the wall. For furniture with a broad flat front (storage, fridges, ovens) this script:
  1. finds which face the camera saw most and where that face really is, from LiDAR;
  2. makes a straightened photo of that face, at a fixed 4 px per cm, from the keyframes;
  3. looks for the vertical edges nearest the box's sides, row by row, taking the first strong edge coming
     from inside the furniture, so a dark gap beside it is not counted as furniture;
  4. moves the box's sides to those edges, but only when the rows agree on where the edge is.
Heights and depths are not changed. Every refit is reported with how much it moved and how sure it is.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from elevation import Wall, build_wall, depth_points, matrix

PPM = 400                 # straightened photo scale: pixels per metre
SEARCH_IN, SEARCH_OUT = 0.30, 0.05   # how far inside / outside the box side to look for the real edge, metres
MAX_SHRINK = 0.35        # never take away more than this share of the width: door seams and handles are edges too
AGREE = 0.02              # rows must agree within this (median absolute deviation, metres) to trust an edge
KINDS = {'storage', 'refrigerator', 'oven', 'dishwasher', 'washerDryer', 'stove'}


def front_face(o, points, normals, floor_y):
    """The vertical face of the box the scan saw most: its outward normal, horizontal axis, half width, and real offset."""
    m = matrix(o['transform']); c = m[:3, 3]
    ax, az = m[:3, 0] / np.linalg.norm(m[:3, 0]), m[:3, 2] / np.linalg.norm(m[:3, 2])
    best = None
    for normal, across, half_across, half_depth in ((az, ax, o['width'] / 2, o['depth'] / 2), (-az, ax, o['width'] / 2, o['depth'] / 2),
                                                    (ax, az, o['depth'] / 2, o['width'] / 2), (-ax, az, o['depth'] / 2, o['width'] / 2)):
        rel = points - c
        out, side, h = rel @ normal, rel @ across, points[:, 1] - floor_y
        near = (np.abs(side) < half_across) & (out > half_depth - 0.25) & (out < half_depth + 0.15) & (normals @ normal > 0.85) & (h > 0.2) & (h < o['height'] + 0.1)
        if best is None or near.sum() > best[0]:
            best = (int(near.sum()), normal, across, half_across, float(np.median(out[near])) if near.sum() else half_depth)
    return best


def edge_profile(image, valid, column_from, column_to, rows, inward_step):
    """Per row, the first strong vertical edge met when walking from inside the furniture outwards."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gradient = np.abs(cv2.Sobel(cv2.GaussianBlur(gray, (5, 5), 0), cv2.CV_32F, 1, 0, ksize=3))
    gradient[~valid] = 0
    found = []
    order = range(column_from, column_to, inward_step)
    for r in rows:
        line = gradient[r]
        window = [line[c] for c in order]
        if not window or max(window) < 25:
            continue
        threshold = 0.5 * max(window)
        for c, g in zip(order, window):
            if g >= threshold:
                found.append(c); break
    return np.array(found)


def main():
    capture = json.loads(Path(sys.argv[1]).read_text())
    raw, room_path, out_path = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    debug = Path(sys.argv[5]) if len(sys.argv) > 5 else None
    room = json.loads(room_path.read_text())
    walls = [Wall(w) for w in capture['walls']]
    floor_y = min(w.origin[1] - w.height / 2 for w in walls)
    points, normals = depth_points(raw)
    from elevation import load_frames
    frames = load_frames(raw)
    if debug:
        debug.mkdir(parents=True, exist_ok=True)
    by_id = {o['id']: o for o in room['objects']}
    for o in capture['objects']:
        if o['category'] not in KINDS or o['width'] < 0.4 or o['height'] < 0.5 or o['id'] not in by_id:
            continue
        seen, normal, across, half, offset = front_face(o, points, normals, floor_y)
        if seen < 500:
            print(f"{o['category']} {o['width'] * 100:.0f} cm: front not seen well enough ({seen} points), left as RoomPlan drew it")
            continue
        m = matrix(o['transform']); c = m[:3, 3]
        bottom = c[1] - o['height'] / 2 - floor_y
        span = 2 * half + 2 * SEARCH_OUT + 0.1
        # Straightened photo of the face, seen from outside: the view's right must be `across` rotated to face us.
        right = np.cross(np.array([0.0, 1.0, 0.0]), normal); right /= np.linalg.norm(right)
        centre = c + normal * offset; centre[1] = floor_y + bottom + o['height'] / 2
        surface = Wall.plane('front', centre, right, normal, span, o['height'] + 0.1)
        image, source, meta = build_wall(surface, frames, PPM)
        valid = source >= 0
        rows_all = np.arange(int(0.1 * image.shape[0]), int(0.9 * image.shape[0]), 2)
        mid = image.shape[1] / 2
        sides = {}
        for name, sign in (('left', -1), ('right', 1)):
            box_edge = int(mid + sign * half * PPM)
            # Look in no further than a quarter of the width, so on narrow furniture the search cannot reach the other side.
            reach = min(SEARCH_IN, 0.25 * 2 * half)
            inner, outer = int(box_edge - sign * reach * PPM), int(box_edge + sign * SEARCH_OUT * PPM)
            hits = edge_profile(image, valid, inner, outer, rows_all, sign)
            if hits.size < 20:
                sides[name] = None; continue
            at = float(np.median(hits)); spread = float(np.median(np.abs(hits - at))) / PPM
            sides[name] = ((at - mid) / PPM, spread, hits.size)
        new_left = sides['left'][0] if sides['left'] and sides['left'][1] <= AGREE else -half
        new_right = sides['right'][0] if sides['right'] and sides['right'][1] <= AGREE else half
        width = new_right - new_left
        if width < (1 - MAX_SHRINK) * 2 * half:
            print(f"{o['category']} {2 * half * 100:.0f} cm: edges would shrink it to {width * 100:.0f} cm, too much to trust; left as RoomPlan drew it")
            continue
        notes = []
        for name, value, old in (('left', sides['left'], -half), ('right', sides['right'], half)):
            if value is None or value[1] > AGREE:
                notes.append(f'{name}: kept')
            else:
                notes.append(f'{name}: moved {(value[0] - old) * 100:+.1f} cm (rows agree within {value[1] * 100:.1f} cm)')
        print(f"{o['category']}: RoomPlan {2 * half * 100:.1f} cm wide -> {width * 100:.1f} cm  [{'; '.join(notes)}; front seen by {seen} points, {meta['coverage']:.0%} of the face photographed]")
        target = by_id[o['id']]
        target['size']['x' if abs(o['width'] / 2 - half) < 1e-6 else 'z'] = round(width, 3)
        # The photo's horizontal axis is `right`, so the new middle is that far along it from the old one.
        move = right * (new_right + new_left) / 2
        target['position']['x'] = round(target['position']['x'] + float(move[0]), 4)
        target['position']['z'] = round(target['position']['z'] + float(move[2]), 4)
        target['evidence'] = 'estimated'
        if debug:
            marked = image.copy()
            for x, colour in ((mid - half * PPM, (0, 0, 255)), (mid + half * PPM, (0, 0, 255)), (mid + new_left * PPM, (0, 200, 0)), (mid + new_right * PPM, (0, 200, 0))):
                cv2.line(marked, (int(x), 0), (int(x), marked.shape[0]), colour, 2)
            cv2.putText(marked, f"red: RoomPlan {2 * half * 100:.0f} cm   green: edges {width * 100:.1f} cm", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
            cv2.imwrite(str(debug / f"{o['category']}_{o['id'][:8]}.jpg"), marked)
    out_path.write_text(json.dumps(room, indent=2))


if __name__ == '__main__':
    main()
