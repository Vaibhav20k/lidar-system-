"""Measure a room's ceiling from the LiDAR depth of a Roomform scan: its main height plus every region
at a different height (beams, soffits, lofts, dropped ceilings below it; coffers and recesses above it),
and write them into the room file.

Usage: ceiling.py <capture.json> <raw-bundle-dir> <room.json> <out-room.json> [debug.png]

No vision model is involved. Only downward-facing surfaces above head height are used, so wall tops,
tall furniture and lamps mostly drop out. The main height is the level with the largest measured area.
Parts of the ceiling the camera never looked at are assumed to sit at the main height unless a measured
region is right next to them.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from elevation import matrix

CELL = 0.05           # meters per grid cell
MIN_HEIGHT = 1.8      # ignore surfaces below this height above the floor
FACING_DOWN = -0.8    # world-space normal Y below this counts as a ceiling-like surface
LEVEL_BAND = 0.07     # cells within this of a level's height belong to it
MIN_LEVEL_AREA = 0.15 # square meters a height must cover to count as its own level
MIN_REGION_AREA = 0.12
TRACE = 4             # outlines are traced on a grid this many times finer so edges land on cell borders
FILL_REACH = 0.15     # unseen cells this close to a measured cell copy its level; enough to close gaps, not to grow regions


def ceiling_points(capture, raw: Path, floor_y):
    """World-space points on downward-facing surfaces above head height."""
    manifest = json.loads((raw / 'manifest.json').read_text())
    dw, dh = manifest['depthSize']
    u, v = np.meshgrid(np.arange(dw) + 0.5, np.arange(dh) + 0.5)
    chunks = []
    for line in (raw / 'frames.jsonl').read_text().splitlines():
        if not line.strip():
            continue
        f = json.loads(line)
        depth_file = raw / 'depth' / f"{f['frame']}.bin"
        if not f.get('depth') or not depth_file.exists():
            continue
        depth = np.fromfile(depth_file, dtype='<f4').reshape(dh, dw)
        confidence = np.fromfile(raw / 'confidence' / f"{f['frame']}.bin", dtype=np.uint8).reshape(dh, dw)
        k = np.array(f['intrinsics'], dtype=np.float64).reshape(3, 3).T
        scale = dw / f['imageWidth']
        camera = np.stack([(u - k[0, 2] * scale) / (k[0, 0] * scale) * depth, -(v - k[1, 2] * scale) / (k[1, 1] * scale) * depth, -depth], axis=-1)
        normal = np.cross(np.gradient(camera, axis=1), np.gradient(camera, axis=0))
        normal /= np.maximum(np.linalg.norm(normal, axis=-1, keepdims=True), 1e-9)
        normal[np.sum(normal * camera, axis=-1) > 0] *= -1  # face the camera
        pose = matrix(f['cameraToWorld'])
        world = camera @ pose[:3, :3].T + pose[:3, 3]
        world_normal_y = normal @ pose[1, :3]
        keep = (confidence >= 1) & (depth > 0.3) & (depth < 5) & (world[..., 1] - floor_y > MIN_HEIGHT) & (world_normal_y < FACING_DOWN)
        chunks.append(world[keep])
    return np.concatenate(chunks) if chunks else np.zeros((0, 3))


def main():
    capture = json.loads(Path(sys.argv[1]).read_text())
    raw, room_path, out_path = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    room = json.loads(room_path.read_text())
    walls = [(matrix(w['transform']), w) for w in capture['walls']]
    floor_y = min(m[1, 3] - w['height'] / 2 for m, w in walls)
    if len(room['floor']) < 3:
        raise SystemExit('This room has no floor outline, so the ceiling cannot be bounded.')
    # Depth is in scan coordinates. A room that was rescaled or had corners moved no longer lines up with it.
    by_id = {w['id']: w for w in room['walls']}
    for m, w in walls:
        match = by_id.get(w['id'])
        start = (m[0, 3] - m[0, 0] * w['width'] / 2, m[2, 3] - m[2, 0] * w['width'] / 2)
        if not match or abs(match['start']['x'] - start[0]) > 0.02 or abs(match['start']['z'] - start[1]) > 0.02 or abs(match['height'] - w['height']) > 0.02:
            raise SystemExit('This room file no longer matches its scan (edited or rescaled). Measure the ceiling on the unedited room.')

    # Work in a frame turned to the longest wall so beams and soffits come out axis-aligned.
    longest = max(walls, key=lambda mw: mw[1]['width'])[0]
    angle = np.arctan2(longest[2, 0], longest[0, 0])
    cos, sin = np.cos(angle), np.sin(angle)
    to_room = lambda x, z: (x * cos + z * sin, -x * sin + z * cos)
    to_world = lambda a, b: (a * cos - b * sin, a * sin + b * cos)

    floor = np.array([to_room(p['x'], p['z']) for p in room['floor']])
    origin = floor.min(axis=0) - 0.1
    cols, rows = (np.ceil((floor.max(axis=0) + 0.1 - origin) / CELL)).astype(int)
    inside = np.zeros((rows, cols), np.uint8)
    # fillPoly takes pixel centres, so shift by half a cell to cover exactly the cells inside the outline.
    cv2.fillPoly(inside, [np.round((floor - origin) / CELL - 0.5).astype(np.int32)], 1)
    inside = inside.astype(bool)

    points = ceiling_points(capture, raw, floor_y)
    a, b = to_room(points[:, 0], points[:, 2])
    ix, iz = ((a - origin[0]) / CELL).astype(int), ((b - origin[1]) / CELL).astype(int)
    ok = (ix >= 0) & (ix < cols) & (iz >= 0) & (iz < rows)
    ix, iz, heights = ix[ok], iz[ok], (points[ok, 1] - floor_y).astype(np.float32)
    top = np.full((rows, cols), -np.inf, np.float32)
    np.maximum.at(top, (iz, ix), heights)
    # Average the points near each cell's top so one stray reading does not set the height.
    near = heights > top[iz, ix] - 0.08
    total, count = np.zeros((rows, cols), np.float64), np.zeros((rows, cols), np.int32)
    np.add.at(total, (iz[near], ix[near]), heights[near])
    np.add.at(count, (iz[near], ix[near]), 1)
    seen = inside & (count >= 4)
    height = np.where(seen, total / np.maximum(count, 1), 0).astype(np.float32)
    if seen.sum() * CELL * CELL < 1:
        raise SystemExit('Too little of the ceiling was scanned to measure it.')

    # Peel off height levels, largest first.
    found, free = [], seen.copy()
    while len(found) < 8:
        values = height[free]
        if values.size * CELL * CELL < MIN_LEVEL_AREA:
            break
        bins = np.arange(values.min(), values.max() + 0.05, 0.025)
        if len(bins) < 2:
            bins = np.array([values.min(), values.min() + 0.025])
        hist, edges = np.histogram(values, bins=bins)
        peak = edges[hist.argmax()] + 0.0125
        members = free & (np.abs(height - peak) < LEVEL_BAND)
        if members.sum() * CELL * CELL < MIN_LEVEL_AREA:
            break
        found.append((float(height[members].mean()), int(members.sum())))
        free &= ~members
    if not found:
        raise SystemExit('No flat ceiling area large enough to measure was found (sloped or vaulted ceiling, or too little scanned).')
    found.sort(reverse=True)  # by height, so neighbouring indices are neighbouring heights
    levels = [h for h, _ in found]
    main = int(np.argmax([area for _, area in found]))  # the ceiling proper is the level most of the room sits under
    distance = np.abs(height[..., None] - np.array(levels))
    label = np.where(seen & (distance.min(axis=-1) < LEVEL_BAND * 1.5), distance.argmin(axis=-1), -1)

    # Unseen cells: copy a measured neighbour if one is close, otherwise assume the main ceiling.
    known = label >= 0
    dist, nearest = cv2.distanceTransformWithLabels((~known).astype(np.uint8), cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    lookup = np.zeros(nearest.max() + 1, np.int32)
    lookup[nearest[known]] = label[known]
    filled = np.where(known, label, np.where(dist * CELL <= FILL_REACH, lookup[nearest], main))
    filled = cv2.medianBlur(filled.astype(np.uint8), 5).astype(np.int32)
    filled[~inside] = -1

    def outline(contour):
        epsilon = 1.2 * TRACE
        while True:
            approx = cv2.approxPolyDP(contour, epsilon, True)
            if len(approx) <= 120:
                break
            epsilon *= 1.5
        # A traced pixel centre sits half a fine pixel inside the region's edge.
        return [dict(zip(('x', 'z'), (round(float(c), 3) for c in to_world(*((point[0] + 0.5) / TRACE * CELL + origin))))) for point in approx]

    regions = []
    for index in range(len(levels)):
        if index == main:
            continue
        # A 3x3 opening removes specks up to 10 cm wide without shifting the mask (an even kernel would).
        mask = cv2.morphologyEx((filled == index).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        fine = cv2.resize(mask, None, fx=TRACE, fy=TRACE, interpolation=cv2.INTER_NEAREST)
        contours, hierarchy = cv2.findContours(fine, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        area = lambda contour: cv2.contourArea(contour) * (CELL / TRACE) ** 2
        for i, contour in enumerate(contours):
            if hierarchy[0][i][3] != -1 or area(contour) < MIN_REGION_AREA:
                continue  # holes are attached to their parent below
            points_out = outline(contour)
            if len(points_out) < 3:
                continue
            region = {'points': points_out, 'height': round(levels[index], 3), 'evidence': 'estimated'}
            holes = [h for h in (outline(c) for j, c in enumerate(contours) if hierarchy[0][j][3] == i and area(c) >= 0.05) if len(h) >= 3][:32]
            if holes:
                region['holes'] = holes
            regions.append(region)
    regions = regions[:64]

    coverage = round(float(seen.sum() / inside.sum()), 2)
    room['ceiling'] = {'height': round(levels[main], 3), 'coverage': coverage, 'regions': regions}
    out_path.write_text(json.dumps(room, indent=2))
    print(f"{room['name']}: ceiling {levels[main]:.2f} m (largest measured level), all levels {[round(l, 2) for l in levels]}, {len(regions)} other regions, {coverage:.0%} of the ceiling measured")
    for region in regions:
        xs, zs = zip(*(to_room(p['x'], p['z']) for p in region['points']))
        delta = region['height'] - levels[main]
        print(f"   {abs(delta):.2f} m {'above' if delta > 0 else 'below'} the ceiling (at {region['height']:.2f} m), about {max(xs) - min(xs):.2f} x {max(zs) - min(zs):.2f} m")

    if len(sys.argv) > 5:
        palette = np.array([(60, 60, 200), (80, 200, 240), (90, 200, 90), (220, 160, 60), (200, 90, 200), (160, 160, 160), (90, 90, 90), (40, 40, 40)], np.uint8)
        image = np.zeros((rows, cols, 3), np.uint8)
        image[filled >= 0] = palette[filled[filled >= 0] % len(palette)]
        image[inside & ~seen] = (image[inside & ~seen] * 0.45).astype(np.uint8)  # dim = not measured
        cv2.imwrite(sys.argv[5], cv2.resize(image, None, fx=5, fy=5, interpolation=cv2.INTER_NEAREST))


if __name__ == '__main__':
    main()
