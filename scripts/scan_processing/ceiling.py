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
from beam_runs import supported_runs
from observations import frame_observations

import uuid

from elevation import Wall, depth_points, matrix

CELL = 0.05           # meters per grid cell
MIN_HEIGHT = 1.8      # ignore surfaces below this height above the floor
FACING_DOWN = -0.8    # world-space normal Y below this counts as a ceiling-like surface
LEVEL_BAND = 0.07     # cells within this of a level's height belong to it
MIN_LEVEL_AREA = 0.15 # square meters a height must cover to count as its own level
MIN_REGION_AREA = 0.12
TRACE = 4             # outlines are traced on a grid this many times finer so edges land on cell borders
FILL_REACH = 0.15     # unseen cells this close to a measured cell copy its level; enough to close gaps, not to grow regions
FACING_SIDE = 0.3     # world-space normal Y within this of 0 counts as a vertical face (a beam's side)
MAX_GROW = 0.12       # how far a lower region's edge may be pushed out to reach its measured side face
FACE_SPAN = 0.25      # side-face points are pooled along this much of the edge before looking for the face
FACE_MIN = 3.0        # pooled points per fine cell needed to call it a face


def ceiling_points(capture, raw: Path, floor_y):
    """World-space points above head height: on downward-facing surfaces, and on vertical faces (beam sides)."""
    chunks, sides, ids = [], [], []
    for f in frame_observations(raw, min_confidence=2, max_range=5):
        p,n = f['points'],f['normals']
        above=p[:,1]-floor_y>MIN_HEIGHT
        down=above & (n[:,1]<FACING_DOWN)
        chunks.append(p[down]); ids.append(np.full(int(down.sum()),f['frameId'],np.int32))
        sides.append(p[above & (np.abs(n[:,1])<FACING_SIDE)])
    empty=np.empty((0,3))
    return (np.concatenate(chunks) if chunks else empty, np.concatenate(sides) if sides else empty,
            np.concatenate(ids) if ids else np.empty(0,np.int32))


def balanced_cell_heights(cells, heights, frames, size):
    """Median within each view, then across views; never select an upper tail.

    Three views support repeat quality. Sparse cells retain a candidate height
    for classifying ceiling versus beams; they do not become repeat evidence.
    """
    groups = {}
    order = np.lexsort((frames, cells))
    keys = np.column_stack([cells[order], frames[order]])
    if not len(keys):
        return np.zeros(size), np.zeros(size, bool), []
    starts = np.r_[0, np.flatnonzero(np.any(np.diff(keys, axis=0), axis=1)) + 1]
    ends = np.r_[starts[1:], len(keys)]
    for start, end in zip(starts, ends):
        if end - start >= 2:
            groups.setdefault(int(keys[start, 0]), []).append(float(np.median(heights[order[start:end]])))
    result, seen, spreads = np.zeros(size), np.zeros(size, bool), []
    for cell, values in groups.items():
        result[cell] = np.median(values)
        if len(values) >= 3:
            seen[cell] = True
            spreads.append(float(np.quantile(values, .9, method='lower') - np.quantile(values, .1, method='lower')))
    return result, seen, spreads


BEAM_STRETCH = 0.25        # walls are read in stretches this long for beams running along them
BEAM_MIN_DROP = 0.10       # a beam hangs at least this far below the ceiling (a cornice is only the top few cm)
BEAM_MIN_DEPTH = 0.04      # and stands at least this far out from the wall
BEAM_MAX_DEPTH = 0.35      # narrow beams: read as before
BEAM_MAX_DROP = 0.45       # a narrow beam along a wall hangs no lower than this; lower faces are door frames, blind boxes, cupboards
# Big beams and bulkheads (25-90 cm out, down to 90 cm below the ceiling). The grid draws them with ragged edges, so they
# are read from their faces too, with tests that only a beam passes: its bottom is joined to the wall and fills the way
# out to its front face (a curtain rod or a fan blade floats free of the wall), and it is not the head of a door or window.
BIG_MIN_DEPTH = 0.25
BIG_MAX_DEPTH = 0.9
BIG_MAX_DROP = 0.9
BEAM_ATTACH = 0.06         # a big beam's bottom starts within this of the wall
BEAM_BIN = 0.02            # it is followed outward from the wall in steps this wide
BEAM_SPAN = 0.8            # and must be seen across this share of the way from the wall to the front face
HEAD_GAP = 0.08            # a bottom this close to the top of an opening below it is that opening's head
FRAME_POINTS = 20          # points a single frame needs on each surface to measure a big beam on its own
MIN_FRAMES = 3             # frames that must measure a stretch for their median to be used
BULKHEAD_DEPTH = 0.6       # this far out or more it is reported as a lowered ceiling (bulkhead) rather than a beam
BEAM_MIN_LENGTH = 1.5
FACE_FILL = 0.7            # the beam's front face must be seen in this share of its height


def wall_beams(capture, raw, floor_y, ceiling_height, room_walls):
    """Beams running along a wall, read from the beam's own faces instead of the ceiling grid.

    A beam along a wall is a flat vertical face standing out from the wall, running continuously from the beam's
    bottom up to the ceiling, with a downward-facing bottom and no ceiling visible behind it. A narrow one (5-15 cm) is only one or two cells of the
    ceiling grid, where the cleanup takes it for noise and cells shared with the ceiling read as ceiling. A cornice
    has a face only in the top few cm, so it never passes. Each wall is read in 25 cm stretches; a beam is the longest
    run of stretches that show one. Returns (regions, notes)."""
    points, normals, frame_of = depth_points(raw, with_frames=True)
    height = points[:, 1] - floor_y
    regions, notes = [], []
    for w in capture['walls']:
        wall = Wall(w)
        if wall.width < 0.8 or w['id'] not in room_walls:
            continue
        rel = points - wall.origin
        along, out = rel @ wall.axis_x, rel @ wall.inward
        facing, down = normals @ wall.inward > 0.8, normals[:, 1] < -0.8
        near = (np.abs(along) < wall.width / 2) & (out > -0.2) & (out < BIG_MAX_DEPTH + 0.2)
        starts = np.arange(-wall.width / 2, wall.width / 2 - BEAM_STRETCH / 2, BEAM_STRETCH)
        # Where the wall surface is, stretch by stretch; where a wardrobe hides it, the rest of the wall tells.
        surfaces = []
        for s0 in starts:
            body = near & (along >= s0) & (along < s0 + BEAM_STRETCH) & facing & (height > 1.0) & (height < 2.0) & (np.abs(out) < 0.15)
            if body.sum() < 40:
                surfaces.append(None); continue
            hist, edges = np.histogram(out[body], bins=np.arange(-0.15, 0.155, 0.01))
            surfaces.append(float(edges[np.convolve(hist, np.ones(3), 'same').argmax()] + 0.005))
        known = [v for v in surfaces if v is not None]
        if len(known) < 3:
            continue
        typical = float(np.median(known))
        # Tops of doors and windows on this wall, as (from, to, height above the floor) along it.
        heads = []
        for o in capture.get('openings', []):
            m = np.array(o['transform'], float).reshape(4, 4).T
            rel_o = m[:3, 3] - wall.origin
            if abs(rel_o @ wall.inward) < 0.3 and abs(rel_o @ wall.axis_x) < wall.width / 2 + 0.2:
                c = rel_o @ wall.axis_x
                heads.append((c - o['width'] / 2, c + o['width'] / 2, m[1, 3] + o['height'] / 2 - floor_y))

        def big_beam(stretch, wall_at, s0):
            """A big beam in one stretch: (bottom, wall_at, front) or None."""
            under = stretch & down & (height > 1.9) & (height < ceiling_height - BEAM_MIN_DROP) & (out > wall_at - 0.03) & (out < wall_at + BIG_MAX_DEPTH + 0.05)
            if under.sum() < 15:
                return None
            hist, edges = np.histogram(height[under], bins=np.arange(1.9, ceiling_height, 0.01))
            smooth = np.convolve(hist, np.ones(3), 'same')
            bins = np.arange(-0.03, BIG_MAX_DEPTH + 0.05, BEAM_BIN)
            for peak in np.argsort(smooth)[::-1][:4]:
                if smooth[peak] < 6:
                    return None
                bottom = float(edges[peak] + 0.005)
                steps = np.histogram(out[under & (np.abs(height - bottom) < 0.02)] - wall_at, bins=bins)[0] >= 2
                if not steps.any() or int(np.argmax(steps)) * BEAM_BIN - 0.03 > BEAM_ATTACH:
                    continue  # not joined to the wall
                if any(a - 0.1 < s0 + BEAM_STRETCH and b + 0.1 > s0 and abs(top_o - bottom) < HEAD_GAP for a, b, top_o in heads):
                    return None  # the head of a door or window
                # The wall just under the beam, seen in the same views as the beam: a scan that drifted a few cm between
                # looking low and looking high would otherwise put the wall and the face out of step.
                below = stretch & facing & (height > bottom - 0.5) & (height < bottom - 0.05) & (np.abs(out - wall_at) < 0.12)
                if below.sum() >= 40:
                    counts, cuts = np.histogram(out[below], bins=np.arange(wall_at - 0.12, wall_at + 0.125, 0.01))
                    wall_at = float(cuts[np.convolve(counts, np.ones(3), 'same').argmax()] + 0.005)
                top = ceiling_height - 0.06
                face = stretch & facing & (height > bottom + 0.02) & (height < top) & (out > wall_at + BIG_MIN_DEPTH - 0.03) & (out < wall_at + BIG_MAX_DEPTH + 0.05)
                bands = np.arange(bottom + 0.02, top, 0.02)
                if len(bands) < 2 or face.sum() < 15:
                    return None
                filled = np.mean([((height[face] >= b) & (height[face] < b + 0.02)).sum() >= 3 for b in bands])
                front = float(np.median(out[face]))
                first = int(round(0.03 / BEAM_BIN))
                span = steps[first:max(first + 1, int((front - wall_at + 0.03) / BEAM_BIN))]
                if (filled < FACE_FILL or not BIG_MIN_DEPTH <= front - wall_at <= BIG_MAX_DEPTH
                        or ceiling_height - bottom > BIG_MAX_DROP or span.mean() < BEAM_SPAN):
                    return None
                # Depth and drop are measured inside each frame and the median taken. One frame is rigid; a scan whose
                # tracking slipped puts the same beam in two places 10-30 cm apart, and pooled points mix the copies.
                depths, drops = [], []
                idx = np.where(stretch & (out < wall_at + BIG_MAX_DEPTH + 0.2))[0]
                for f in np.unique(frame_of[idx]):
                    m = idx[frame_of[idx] == f]
                    # Only the beam's own bottom (an AC or a lamp under it faces down too), and the back-most upright
                    # surface under it as the wall (an AC's front stands out in front of the wall).
                    under_f = m[down[m] & (np.abs(height[m] - bottom) < 0.12)]
                    if len(under_f) < FRAME_POINTS:
                        continue
                    bottom_f = float(np.median(height[under_f]))
                    wall_f = m[facing[m] & (height[m] > bottom_f - 0.5) & (height[m] < bottom_f - 0.05)]
                    if len(wall_f) < FRAME_POINTS:
                        continue
                    back = float(np.percentile(out[wall_f], 10))
                    wall_out = float(np.median(out[wall_f][np.abs(out[wall_f] - back) < 0.03]))
                    face_f = m[facing[m] & (height[m] > bottom_f + 0.03) & (height[m] < ceiling_height - 0.08) & (out[m] > wall_out + BIG_MIN_DEPTH - 0.05)]
                    if len(face_f) < FRAME_POINTS:
                        continue
                    face_out = float(np.median(out[face_f]))
                    depths.append(face_out - wall_out)
                    beside_f = m[down[m] & (out[m] > face_out + 0.05) & (np.abs(height[m] - ceiling_height) < 0.2)]
                    if len(beside_f) >= FRAME_POINTS:
                        drops.append(float(np.median(height[beside_f])) - bottom_f)
                if len(depths) >= MIN_FRAMES:
                    front = wall_at + float(np.median(depths))
                    if not BIG_MIN_DEPTH <= front - wall_at <= BIG_MAX_DEPTH:
                        return None
                if len(drops) >= MIN_FRAMES:
                    bottom = ceiling_height - float(np.median(drops))
                return (bottom, wall_at, front)
            return None

        found = []
        for s0, wall_at in zip(starts, surfaces):
            stretch = near & (along >= s0) & (along < s0 + BEAM_STRETCH)
            wall_at = typical if wall_at is None or abs(wall_at - typical) > 0.05 else wall_at
            narrow = stretch & (out < BEAM_MAX_DEPTH + 0.2)  # the narrow-beam reading looks no further out than it always has
            under = narrow & down & (height > 1.9) & (height < ceiling_height - BEAM_MIN_DROP) & (out > wall_at - 0.03)
            if under.sum() < 15:
                found.append(big_beam(stretch, wall_at, s0)); continue
            hist, edges = np.histogram(height[under], bins=np.arange(1.9, ceiling_height, 0.01))
            bottom = float(edges[np.convolve(hist, np.ones(3), 'same').argmax()] + 0.005)
            top = ceiling_height - 0.06  # above this a cornice may stand out too
            face = narrow & facing & (height > bottom + 0.02) & (height < top) & (out > wall_at + BEAM_MIN_DEPTH - 0.01)
            bands = np.arange(bottom + 0.02, top, 0.02)
            if len(bands) < 2 or face.sum() < 15:
                found.append(big_beam(stretch, wall_at, s0)); continue
            filled = np.mean([((height[face] >= b) & (height[face] < b + 0.02)).sum() >= 3 for b in bands])
            front = float(np.median(out[face]))
            if filled < FACE_FILL or front - wall_at < BEAM_MIN_DEPTH or ceiling_height - bottom > BEAM_MAX_DROP:
                found.append(big_beam(stretch, wall_at, s0)); continue
            found.append((bottom, wall_at, front))
        for best in supported_runs(found):
            hits = [f for f in found[best[0]:best[1]] if f is not None]
            length = (best[1] - best[0]) * BEAM_STRETCH
            if len(hits) < 4 or length < 1.0:
                continue
            bottom, wall_at, front = (float(np.median([f[k] for f in hits])) for k in range(3))
            # One beam has one bottom and one face. Most stretches must agree on both; a few may not, where something stands
            # in front of the face (boxes on a wardrobe, a curtain) - the typical value is the beam.
            # The bottom must be one height; the depth may change gradually (a beam face not quite parallel to a bowed
            # wall), so it must follow a straight line along the wall rather than be one number.
            centres = np.array([-wall.width / 2 + (i + 0.5) * BEAM_STRETCH for i in range(best[0], best[1]) if found[i] is not None])
            bottoms, depths = np.array([f[0] for f in hits]), np.array([f[2] - f[1] for f in hits])
            trend = np.polyfit(centres, depths, 1) if len(hits) >= 3 else np.array([0.0, float(np.median(depths))])
            # Heights come in 1 cm steps, so the bottom gets 4 cm: a tighter limit flips on a millimetre of rounding.
            # A big beam's face is far from the wall, so a few cm of scan drift weigh on it more: 4 cm there.
            agree = (np.abs(bottoms - np.median(bottoms)) <= 0.04) & (np.abs(depths - np.polyval(trend, centres)) <= (0.04 if np.median(depths) >= BIG_MIN_DEPTH else 0.03))
            if agree.mean() < 0.6:
                continue
            bottom, wall_at, front = (float(np.median([f[k] for f, ok in zip(hits, agree) if ok])) for k in range(3))
            fronts = np.array([f[2] for f in hits])
            front_line = np.polyfit(centres[agree], fronts[agree], 1) if agree.sum() >= 3 else np.array([0.0, front])
            # A beam is solid from its bottom to the ceiling, so the ceiling is never seen behind its face. Behind a pipe,
            # a light track or a curtain rail it is (in the scans so far: at most 0.24x the beam's bottom for beams, 30x for a pipe).
            margin = max(0.01, 0.25 * (front - wall_at))  # a fixed 2 cm would leave nothing to look at behind a 4-6 cm beam
            strip = near & down & (along >= -wall.width / 2 + best[0] * BEAM_STRETCH) & (along < -wall.width / 2 + best[1] * BEAM_STRETCH) & (out > wall_at + margin) & (out < front - margin)
            seen_ceiling, seen_bottom = (strip & (np.abs(height - ceiling_height) < 0.04)).sum(), (strip & (np.abs(height - bottom) < 0.03)).sum()
            if seen_bottom < 30 or seen_ceiling > 0.5 * seen_bottom:
                continue
            u0, u1 = -wall.width / 2 + best[0] * BEAM_STRETCH, min(wall.width / 2, -wall.width / 2 + best[1] * BEAM_STRETCH)
            inner = min(0.0, wall_at) - 0.01
            f0, f1 = (float(np.polyval(front_line, u)) for u in (u0, u1))
            corners = [wall.origin + wall.axis_x * u + wall.inward * o for u, o in ((u0, inner), (u1, inner), (u1, f1), (u0, f0))]
            regions.append({'points': [{'x': round(float(c[0]), 3), 'z': round(float(c[2]), 3)} for c in corners], 'height': round(bottom, 3), 'evidence': 'measured'})
            d0, d1 = sorted(float(np.polyval(trend, u)) * 100 for u in (u0, u1))
            out_text = f'{(front - wall_at) * 100:.0f} cm' if d1 - d0 < 2 else f'{d0:.0f}-{d1:.0f} cm'
            kind = 'lowered ceiling (bulkhead)' if front - wall_at >= BULKHEAD_DEPTH else 'beam'
            notes.append(f"{kind} along a {wall.width:.2f} m wall: {out_text} out from the wall, {(ceiling_height - bottom) * 100:.0f} cm below the ceiling, {u1 - u0:.2f} m long")
    return regions, notes


def polygon_mask(polygons, low, size):
    mask = np.zeros(size, np.uint8)
    for poly in polygons:
        cv2.fillPoly(mask, [np.round((np.array(poly) - low) * 50).astype(np.int32)], 1)
    return mask.astype(bool)


def hanging_fixtures(regions, room_walls_xz, ceiling_height):
    """Split off "lower ceiling" blobs that are really a fan or a hanging lamp: small, round, away from every wall.
    They are kept, as ceiling items for a person to check, not drawn as part of the ceiling."""
    kept, items = [], []
    for region in regions:
        pts = np.array([[p['x'], p['z']] for p in region['points']])
        size = np.ptp(pts, axis=0)
        def wall_distance(p):
            best = 9.0
            for a, b in room_walls_xz:
                t = np.clip((p - a) @ (b - a) / max((b - a) @ (b - a), 1e-9), 0, 1)
                best = min(best, float(np.linalg.norm(p - (a + t * (b - a)))))
            return best
        clear = min(wall_distance(p) for p in pts)
        # Only below the ceiling and not far below: higher blobs are recesses or skylight wells, much lower ones are cupboard tops.
        drop = ceiling_height - region['height']
        if 0.05 < drop < 0.7 and clear > 0.3 and size.max() < 1.3 and size.max() / max(size.min(), 0.05) < 2.2:
            centre = pts.mean(axis=0)
            # A fan's blades and its hub can show as two blobs at slightly different heights: one fixture.
            if any(np.hypot(i['x'] - centre[0], i['z'] - centre[1]) < 0.5 for i in items):
                continue
            items.append({'id': str(uuid.uuid4()), 'kind': 'other_electrical', 'x': round(float(centre[0]), 3), 'z': round(float(centre[1]), 3),
                          'height': region['height'], 'width': round(float(max(size[0], 0.05)), 3), 'depth': round(float(max(size[1], 0.05)), 3), 'rotation': 0.0,
                          'evidence': 'estimated', 'review': True, 'note': 'Hanging fixture (ceiling fan or lamp) seen by depth; not part of the ceiling.'})
        else:
            kept.append(region)
    return kept, items


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

    points, side_points, frame_ids = ceiling_points(capture, raw, floor_y)
    a, b = to_room(points[:, 0], points[:, 2])
    ix, iz = ((a - origin[0]) / CELL).astype(int), ((b - origin[1]) / CELL).astype(int)
    ok = (ix >= 0) & (ix < cols) & (iz >= 0) & (iz < rows)
    bounded = ok.copy()
    ok[bounded] &= inside[iz[bounded], ix[bounded]]
    ix, iz, heights = ix[ok], iz[ok], (points[ok, 1] - floor_y).astype(np.float32)
    heights_grid, observed, repeat_spreads = balanced_cell_heights(iz * cols + ix, heights, frame_ids[ok], rows * cols)
    seen = inside & (heights_grid.reshape(rows, cols) > 0)
    height = np.where(seen, heights_grid.reshape(rows, cols), 0).astype(np.float32)
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

    # Vertical-face points on the fine grid, to place the edges of regions hanging below the ceiling.
    sa, sb = to_room(side_points[:, 0], side_points[:, 2])
    sx, sz = ((sa - origin[0]) / CELL * TRACE).astype(int), ((sb - origin[1]) / CELL * TRACE).astype(int)
    on_grid = (sx >= 0) & (sx < cols * TRACE) & (sz >= 0) & (sz < rows * TRACE)
    sx, sz, side_heights = sx[on_grid], sz[on_grid], side_points[on_grid, 1] - floor_y

    def reach_side_face(fine, low):
        """Push a lower region's edges out to its measured side face.

        Cells on a beam's edge can mix ceiling and beam observations, so the beam
        comes out several cm narrow. Look outward from each edge, along the grid, for the beam's side face (vertical
        points between the beam's underside and the ceiling) within MAX_GROW, and take everything up to where those
        points are densest. Edges with no measured face are left where they are."""
        band = (side_heights > low + 0.05) & (side_heights < levels[main] - 0.05)
        count = np.zeros(fine.shape, np.float32)
        np.add.at(count, (sz[band], sx[band]), 1)
        grown = fine.astype(bool).copy()
        steps = int(round(MAX_GROW / CELL * TRACE))
        span = int(round(FACE_SPAN / CELL * TRACE)) | 1
        for axis in (0, 1):
            # Pool face points along the edge, so a hanging lamp or a stray reading cannot pass for the face.
            pooled = cv2.blur(count, (span, 1) if axis == 0 else (1, span))  # kernel is (width, height): along the edge
            for direction in (1, -1):
                shift = lambda grid, n: np.roll(grid, -direction * n, axis=axis)
                edge = fine.astype(bool) & ~shift(fine, 1).astype(bool)
                # The face is where the pooled count peaks within reach of the edge.
                profile = np.stack([shift(pooled, n) for n in range(1, steps + 1)])
                best, strength = profile.argmax(axis=0) + 1, profile.max(axis=0)
                hit = edge & (strength >= FACE_MIN)
                for n in range(1, steps + 1):
                    grown |= np.roll(hit & (best >= n), direction * n, axis=axis)
        fine_inside = cv2.resize(inside.astype(np.uint8), None, fx=TRACE, fy=TRACE, interpolation=cv2.INTER_NEAREST).astype(bool)
        return (grown.astype(bool) & fine_inside).astype(np.uint8)

    regions = []
    for index in range(len(levels)):
        if index == main:
            continue
        # A 3x3 opening removes specks up to 10 cm wide without shifting the mask (an even kernel would).
        mask = cv2.morphologyEx((filled == index).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        fine = cv2.resize(mask, None, fx=TRACE, fy=TRACE, interpolation=cv2.INTER_NEAREST)
        if levels[index] < levels[main]:
            fine = reach_side_face(fine, levels[index])
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
    room_walls_xz = [(np.array([w['start']['x'], w['start']['z']]), np.array([w['end']['x'], w['end']['z']])) for w in room['walls']]
    regions, fixtures = hanging_fixtures(regions, room_walls_xz, levels[main])
    beams, beam_notes = wall_beams(capture, raw, floor_y, levels[main], {w['id'] for w in room['walls']})
    if beams:
        strips = [[[p['x'], p['z']] for p in beam['points']] for beam in beams]
        lower = [r for r in regions if r['height'] < levels[main]]
        polys = [[[p['x'], p['z']] for p in r['points']] for r in lower]
        everything = np.array([p for poly in strips + polys for p in poly])
        low = everything.min(axis=0) - 0.2
        size = tuple((((everything.max(axis=0) + 0.2 - low) * 50).astype(int) + 1)[::-1])
        # Grid pieces drawn from the beams give way to the measured strips. The strips are taken together, so an L of
        # beams round a corner replaces the one L-shaped grid piece that neither strip covers enough of alone. A beam
        # that a wider grid shape holds whole (a bulkhead it runs into) is left out and that shape kept; this is settled
        # before anything is removed, so no grid piece is dropped for a beam that is then not drawn.
        masks = [polygon_mask([poly], low, size) for poly in polys]
        strip_masks = [polygon_mask([strip], low, size) for strip in strips]
        accepted = list(range(len(beams)))
        for _ in range(len(beams) + 1):
            union = np.logical_or.reduce([strip_masks[i] for i in accepted]) if accepted else np.zeros(size, bool)
            near_beams = cv2.dilate(union.astype(np.uint8), np.ones((11, 11), np.uint8)).astype(bool)
            replaced = [bool(accepted) and (m & near_beams).sum() >= 0.6 * max(m.sum(), 1) for m in masks]
            kept_masks = [m for m, gone in zip(masks, replaced) if not gone]
            still = []
            for i in accepted:
                touching = [m for m in kept_masks if (m & strip_masks[i]).any()]
                covered = np.logical_or.reduce(touching) if touching else np.zeros(size, bool)
                if not ((strip_masks[i] & covered).sum() >= 0.8 * strip_masks[i].sum() and covered.sum() >= 2 * strip_masks[i].sum()):
                    still.append(i)
            if still == accepted:
                break
            accepted = still
        gone = {id(r) for r, out_ in zip(lower, replaced) if out_}
        regions = [r for r in regions if id(r) not in gone]
        for i in accepted:
            regions.append(beams[i])
            print('   ' + beam_notes[i])
    regions = sorted(regions, key=lambda r: r['evidence'] != 'measured')[:64]  # measured beams first if there are too many

    coverage = round(float(seen.sum() / inside.sum()), 2)
    room['ceiling'] = {'height': round(levels[main], 3), 'coverage': coverage, 'regions': regions}
    room['ceiling']['measurementDiagnostics'] = {
        'method': 'frame-balanced-median-v2', 'heightDatum': 'RoomPlan floor (not independently validated)',
        'repeatCoverage': float(observed.sum() / max(1, seen.sum())),
        'repeatSpreadP90Meters': float(np.quantile(repeat_spreads, .9, method='lower')) if repeat_spreads else 0.,
        'absoluteAccuracyVerified': False}
    if fixtures:
        room['ceiling']['items'] = fixtures
        print(f'   {len(fixtures)} hanging fixture(s) (fan or lamp) kept as ceiling items to check, not as lower ceiling')
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
