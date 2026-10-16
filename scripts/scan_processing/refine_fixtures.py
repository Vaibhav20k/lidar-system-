"""Measure the exact edges of electrical plates from the photos, without a vision model.

Usage: refine_fixtures.py <capture.json> <raw-bundle-dir> <room.json> <out-room.json> [debug-dir]

detect.py finds a plate and the vision model draws a box around it; that box is a little loose (a 22 x 8 cm board came
out 23 x 10 cm). Here the box only says where to look. For each plate:
  1. close-ups at ~0.7 mm per pixel are made on the wall surface measured around it, from the camera frames (and wall
     stills) that agree with each other on where things are;
  2. on each side of the box, walking from the wall towards the plate, the first strong edge is the plate's outer edge
     (switch modules inside the plate are edges too, so the strongest edge is not taken);
  3. this is done row by row and photo by photo; a side moves only when the rows and the photos agree, and how far
     they spread is kept as the measurement's tolerance.
Run after apply_fixtures.py and before refit_walls.py / floor_level.py: offsets and sills are along RoomPlan's walls.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from detect import agreeing_depth, agreeing_photos, local_surface, real_surface
from elevation import Wall, best_frames, depth_points, load_frames, render_patch

PLATES = {'switch_board', 'socket', 'switch', 'regulator', 'distribution_box', 'other_electrical'}
PPM = 1500                # close-up scale, pixels per metre (0.67 mm per pixel)
MARGIN = 0.06             # wall shown around the box
SEARCH = 0.03             # how far either side of the box's edge to look for the real one
ROW_AGREE = 0.003         # rows must agree on an edge within this (median absolute deviation)
PHOTO_AGREE = 0.006       # photos must agree on an edge within this
STRONG = 12.0             # weakest gradient that counts as an edge


def plate_edges(patch, seen, box, ppm):
    """Edges of the plate in one close-up, in metres from the patch centre: dict side -> (position, row spread) or None."""
    gray = cv2.GaussianBlur(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).astype(np.float32), (3, 3), 0)
    # Only pixels the photo really saw; the grey fill's border would otherwise pass for a plate edge.
    valid = cv2.erode(seen.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    gx, gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0)), np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1))
    h, w = gray.shape
    cx, cy = w / 2, h / 2
    left, right = cx + box['u0'] * ppm, cx + box['u1'] * ppm
    top, bottom = cy - box['v1'] * ppm, cy - box['v0'] * ppm
    reach = SEARCH * ppm
    edges = {}
    # Rows through the middle of the plate for the side edges; columns through the middle for top and bottom.
    rows = np.arange(int(top + 0.25 * (bottom - top)), int(bottom - 0.25 * (bottom - top)))
    cols = np.arange(int(left + 0.25 * (right - left)), int(right - 0.25 * (right - left)))
    for name, start, step, grad, lines, vertical in (('left', left - reach, 1, gx, rows, True), ('right', right + reach, -1, gx, rows, True),
                                                      ('top', top - reach, 1, gy, cols, False), ('bottom', bottom + reach, -1, gy, cols, False)):
        # Walk from outside the box inwards, but never past a third of the plate (the other side's edge is there).
        span = (right - left) if vertical else (bottom - top)
        end = start + step * (2 * reach + span / 3)
        steps = np.arange(int(round(start)), int(round(end)), step)
        steps = steps[(steps >= 0) & (steps < (w if vertical else h))]
        if len(steps) < 5 or len(lines) < 5:
            edges[name] = None
            continue
        hits = []
        for line in lines:
            if line < 0 or line >= (h if vertical else w):
                continue
            profile = grad[line, steps] if vertical else grad[steps, line]
            ok = valid[line, steps] if vertical else valid[steps, line]
            if not ok.all() or profile.max() < STRONG:
                continue
            hits.append(steps[int(np.argmax(profile >= 0.5 * profile.max()))])
        if len(hits) < 0.5 * len(lines):
            edges[name] = None
            continue
        hits = np.array(hits, float)
        at = float(np.median(hits))
        spread = float(np.median(np.abs(hits - at))) / ppm
        position = (at - cx) / ppm if vertical else (cy - at) / ppm
        edges[name] = (position, spread)
    return edges


def main():
    capture_path, raw, room_path, out_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    debug = Path(sys.argv[5]) if len(sys.argv) > 5 else None
    capture, room = json.loads(capture_path.read_text()), json.loads(room_path.read_text())
    walls = {w['id']: Wall(w) for w in capture['walls']}
    floor_y = min(w.origin[1] - w.height / 2 for w in walls.values())
    frames = load_frames(raw)
    points, normals, seen_at = depth_points(raw, with_frames=True)
    surfaces = {}
    if debug:
        debug.mkdir(parents=True, exist_ok=True)
    for number, fixture in enumerate(room.get('fixtures', [])):
        if fixture['kind'] not in PLATES or fixture['wallId'] not in walls:
            continue
        scanned = walls[fixture['wallId']]
        if scanned.id not in surfaces:
            surfaces[scanned.id] = real_surface(scanned, points, normals, seen_at, floor_y)[0]
        wall = surfaces[scanned.id]
        u = fixture['offset'] + fixture['width'] / 2 - wall.width / 2
        v = fixture['sill'] + fixture['height'] / 2 - wall.height / 2
        surface, _ = local_surface(wall, (points, normals), (u, v))
        surface = agreeing_depth(surface, frames, (u, v))
        size = (fixture['width'] + 2 * MARGIN + 2 * SEARCH, fixture['height'] + 2 * MARGIN + 2 * SEARCH)
        views = best_frames(surface, frames, (u, v), limit=6)
        rendered = [p for p in (render_patch(surface, frames[i], (u, v), size, PPM, with_mask=True) for i in views) if p is not None]
        if len(rendered) < 2:
            print(f"{fixture['kind']} {number}: too few clear photos, box kept")
            continue
        group, _ = agreeing_photos([image for image, _ in rendered], PPM)
        patches = [rendered[g][0] for g in group]
        masks = [rendered[g][1] for g in group]
        box = {'u0': -fixture['width'] / 2, 'u1': fixture['width'] / 2, 'v0': -fixture['height'] / 2, 'v1': fixture['height'] / 2}
        per_photo = [plate_edges(p, m, box, PPM) for p, m in zip(patches, masks)]
        result, spreads = {}, []
        for name in ('left', 'right', 'top', 'bottom'):
            seen = [e[name] for e in per_photo if e[name] is not None and e[name][1] <= ROW_AGREE]
            if len(seen) < max(2, len(per_photo) // 2):
                continue
            positions = np.array([s[0] for s in seen])
            middle = float(np.median(positions))
            if np.max(np.abs(positions - middle)) > PHOTO_AGREE:
                continue
            result[name] = middle
            spreads.append(float(np.max(np.abs(positions - middle))) + float(np.median([s[1] for s in seen])))
        u0, u1 = result.get('left', box['u0']), result.get('right', box['u1'])
        v0, v1 = result.get('bottom', box['v0']), result.get('top', box['v1'])
        width, height = u1 - u0, v1 - v0
        old = (fixture['width'], fixture['height'])
        if not result or width < 0.5 * old[0] or height < 0.5 * old[1] or width > old[0] + 0.06 or height > old[1] + 0.06:
            print(f"{fixture['kind']} {number}: edges not clear enough, box kept ({len(result)} sides found)")
            continue
        tolerance = max(spreads) if spreads else None
        # Keep the plate on its wall and inside the room's limits (offset >= 0, within the wall, at most 2 x 1 m).
        on = next(w for w in room['walls'] if w['id'] == fixture['wallId'])
        length = float(np.hypot(on['end']['x'] - on['start']['x'], on['end']['z'] - on['start']['z']))
        width, height = min(width, 2.0, length), min(height, 1.0)
        fixture['offset'] = round(min(max(0.0, fixture['offset'] + (u0 - box['u0'])), max(0.0, length - width)), 4)
        fixture['sill'] = round(max(0.0, fixture['sill'] + (v0 - box['v0'])), 4)
        fixture['width'], fixture['height'] = round(width, 4), round(height, 4)
        measured = ', '.join(result)
        fixture['note'] = (f"Edges measured on {len(patches)} photos ({measured}; others from the detector's box), "
                           f"within about {tolerance * 1000:.0f} mm. " + str(fixture.get('note') or ''))[:200]
        if len(result) == 4:
            fixture['tolerance'] = round(tolerance, 4)
        print(f"{fixture['kind']} {number}: {old[0] * 100:.1f} x {old[1] * 100:.1f} cm -> {width * 100:.1f} x {height * 100:.1f} cm "
              f"(sides from photos: {measured}; photos agree within {tolerance * 1000:.1f} mm; bottom moved {(v0 - box['v0']) * 100:+.1f} cm)")
        if debug:
            for k, p in enumerate(patches[:3]):
                marked = p.copy()
                h, w = marked.shape[:2]
                draw = lambda a, b, c, d, colour: cv2.rectangle(marked, (int(w / 2 + a * PPM), int(h / 2 - d * PPM)), (int(w / 2 + b * PPM), int(h / 2 - c * PPM)), colour, 2)
                draw(box['u0'], box['u1'], box['v0'], box['v1'], (0, 0, 255))
                draw(u0, u1, v0, v1, (0, 200, 0))
                cv2.imwrite(str(debug / f"{fixture['kind']}_{number}_{k}.jpg"), marked)
    out_path.write_text(json.dumps(room, indent=2))


if __name__ == '__main__':
    main()
