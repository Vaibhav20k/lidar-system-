"""Build a fronto-parallel photo ("elevation") of every wall in a Roomform scan.

Usage: elevation.py <capture.json> <raw-bundle-dir> <out-dir> [pixels-per-meter]

Each wall image is seen from inside the room, at a fixed metric scale, stitched from the
raw keyframes. Pixels where furniture stands in front of the wall are dimmed.
"""
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from observations import frame_observations, unproject, camera_normals

OCCLUSION_TOLERANCE = 0.12  # meters in front of the wall plane before a pixel counts as blocked
MIN_COS_INCIDENCE = 0.25    # ignore views more oblique than ~75 degrees
MIN_DISTANCE = 0.3
PLAN_SCALE = 4              # frame selection runs on a grid this many times coarser
GOOD_ENOUGH = 0.6           # a frame may cover a pixel if it scores this fraction of the best view


def matrix(values):
    return np.array(values, dtype=np.float64).reshape(4, 4).T  # stored column-major


MAX_STILL_WIDTH = 3024     # full-resolution wall stills are scaled to this width to bound memory
ALIGN_VERSION = 3          # bump when the alignment changes, so cached results are recomputed
ALIGN_CELL = 0.03          # top-down map cell for aligning stills to the scan
ALIGN_TIE = 0.95           # turns matching within this share of the best are a tie, broken by the phone's estimate
MIN_ALIGN_MATCH = 0.6      # stills that match the scan worse than this are not used


def raw_still_pose(record, key='cameraToWorld'):
    """A still's pose in the photo session's own ARKit coordinates. The phone may have applied its own estimate of the
    tie to the scan (`toScan`); it is undone here, because the Mac's alignment is more reliable and expects gravity up."""
    pose = matrix(record.get(key) or record['cameraToWorld'])
    return np.linalg.inv(matrix(record['toScan'])) @ pose if record.get('toScan') else pose


def still_points(raw: Path, stills):
    """LiDAR points seen by the stills, in the stills' own coordinates, with normals facing the camera."""
    points, normals = [], []
    for s in stills:
        depth_file = raw / 'stills' / f"{s['frame']}.depth.bin"
        if not s.get('depth') or not depth_file.exists():
            continue
        dw, dh = s['depthSize']
        depth = np.fromfile(depth_file, dtype='<f4').reshape(dh, dw)
        k = np.array(s['intrinsics'], dtype=np.float64).reshape(3, 3).T
        scale = dw / s['imageWidth']
        u, v = np.meshgrid(np.arange(dw) + 0.5, np.arange(dh) + 0.5)
        camera = np.stack([(u - k[0, 2] * scale) / (k[0, 0] * scale) * depth, -(v - k[1, 2] * scale) / (k[1, 1] * scale) * depth, -depth], axis=-1)
        normal = np.cross(np.gradient(camera, axis=1), np.gradient(camera, axis=0))
        normal /= np.maximum(np.linalg.norm(normal, axis=-1, keepdims=True), 1e-9)
        normal[np.sum(normal * camera, axis=-1) > 0] *= -1
        # The depth was taken a moment before the still; newer scans save where the camera was for it.
        pose = raw_still_pose(s, 'depthCameraToWorld')
        keep = (depth > 0.3) & (depth < 4.5)
        points.append(camera[keep] @ pose[:3, :3].T + pose[:3, 3])
        normals.append(normal[keep] @ pose[:3, :3].T)
    if not points:
        return np.zeros((0, 3)), np.zeros((0, 3))
    return np.concatenate(points), np.concatenate(normals)


def align_stills(raw: Path, stills):
    """The transform that puts the wall stills into the scan's coordinates, and how well they match (0-1).

    To take full-resolution stills the phone switches the camera format, and on some phones ARKit then restarts its
    coordinates: the stills come out shifted and turned against the scan. Gravity is kept, so the fix is a turn about
    the vertical plus a shift. The turn and the sideways shift come from matching top-down maps of the walls (from
    the stills' own depth and from the scan); the height shift from the floor and the ceiling. Cached in
    stills_align.json next to the stills.
    """
    cache = raw / 'stills_align.json'
    # Keyed on what the alignment reads, so a partly copied bundle or a code change is never stuck with an old answer.
    depth_files = sorted((raw / 'stills').glob('*.depth.bin'))
    stamp = hashlib.sha1((raw / 'stills.jsonl').read_bytes() + repr([(f.name, f.stat().st_size) for f in depth_files]).encode()
                         + str(ALIGN_VERSION).encode()).hexdigest()
    if cache.exists():
        saved = json.loads(cache.read_text())
        if saved.get('stamp') == stamp:
            return np.array(saved['transform']), saved['match']
    scan_p, scan_n = depth_points(raw)
    still_p, still_n = still_points(raw, stills)
    transform, match = np.eye(4), 0.0
    walls_scan = scan_p[np.abs(scan_n[:, 1]) < 0.3][:, [0, 2]] if len(scan_p) else np.zeros((0, 2))
    walls_still = still_p[np.abs(still_n[:, 1]) < 0.3][:, [0, 2]] if len(still_p) else np.zeros((0, 2))
    if len(walls_still) > 2000 and len(walls_scan) > 2000:
        centre = walls_still.mean(axis=0)

        def matcher(cell, blur):
            low = walls_scan.min(axis=0) - 3
            shape = tuple(((((walls_scan.max(axis=0) + 3) - low) / cell).astype(int) + 1)[::-1])

            def raster(xz):
                image = np.zeros(shape, np.float32)
                ij = ((xz - low) / cell).astype(int)
                ok = (ij[:, 0] >= 0) & (ij[:, 0] < shape[1]) & (ij[:, 1] >= 0) & (ij[:, 1] < shape[0])
                np.add.at(image, (ij[ok, 1], ij[ok, 0]), 1)
                return cv2.GaussianBlur(np.minimum(image, 20), (0, 0), blur)
            target = raster(walls_scan)

            def overlap(source):
                return float((source * target).sum() / np.sqrt((source ** 2).sum() * (target ** 2).sum() + 1e-9))

            def score(angle, anchor):
                # Rotate about the stills' centre, place it at `anchor`, then let phase correlation find the rest of the shift.
                r = np.array([[np.cos(angle), np.sin(angle)], [-np.sin(angle), np.cos(angle)]])
                source = raster((walls_still - centre) @ r.T + anchor)
                (dx, dy), _ = cv2.phaseCorrelate(target, source)
                moved = cv2.warpAffine(source, np.float32([[1, 0, -dx], [0, 1, -dy]]), shape[::-1])
                return overlap(moved), r, anchor - np.array([dx, dy]) * cell - r @ centre, angle
            return score, lambda: overlap(raster(walls_still))

        coarse, as_taken = matcher(ALIGN_CELL, 1.5)
        middle = walls_scan.mean(axis=0)
        # A rectangular room matches almost as well turned 180 degrees; search near the phone's own estimate of the
        # turn (its `toScan`, undone above), or near no turn when it has none.
        ties = [matrix(s['toScan']) for s in stills if s.get('toScan')]
        expected = float(np.degrees(np.arctan2(ties[0][0, 2], ties[0][0, 0]))) if ties else 0.0
        # Search every turn, but among turns that match almost as well as the best (a 180 degree twin), take the one
        # nearest the phone's estimate: that estimate can be several degrees off, so it only breaks near-ties.
        candidates = [coarse(np.radians(a), middle) for a in np.arange(-180, 180, 2.0)]
        top = max(c[0] for c in candidates)
        near_best = [c for c in candidates if c[0] >= ALIGN_TIE * top]
        best = min(near_best, key=lambda c: abs((np.degrees(c[3]) - expected + 180) % 360 - 180))
        anchor = best[1] @ centre + best[2]
        best = max((coarse(a, anchor) for a in np.arange(best[3] - np.radians(2), best[3] + np.radians(2.01), np.radians(0.2))), key=lambda x: x[0])
        match, r, shift, _ = best
        # Stills usually need no correction (the coordinates were kept). Keep them as taken unless moving them clearly helps.
        unmoved = as_taken()
        as_is = unmoved >= 0.97 * match
        if as_is:
            match, r, shift = unmoved, np.eye(2), np.zeros(2)

        def level(points, normals, up):
            # The floor is the lowest broad upward surface (beds and tables face up too, but stand on it); the ceiling
            # the highest broad downward one.
            chosen = points[normals[:, 1] > 0.9, 1] if up else points[normals[:, 1] < -0.9, 1]
            if len(chosen) < 200:
                return None
            hist, edges = np.histogram(chosen, bins=np.arange(-6, 6, 0.01))
            hist = np.convolve(hist, np.ones(3), 'same')
            broad = np.nonzero(hist >= 0.25 * hist.max())[0]
            index = broad[0] if up else broad[-1]
            index = max(range(max(0, index - 3), min(len(hist), index + 4)), key=lambda i: hist[i])
            return float(edges[index] + 0.005)
        shifts = [level(scan_p, scan_n, up) - level(still_p, still_n, up) for up in (True, False)
                  if level(scan_p, scan_n, up) is not None and level(still_p, still_n, up) is not None]
        if as_is:
            pass  # in the scan's coordinates already: nothing to move
        elif len(shifts) < 2 or abs(shifts[0] - shifts[1]) > 0.08:
            match = 0.0  # floor and ceiling not both seen, or disagreeing: a bed or counter top could pass for the floor
        else:
            transform = np.eye(4)
            transform[0, 0], transform[0, 2], transform[2, 0], transform[2, 2] = r[0, 0], r[0, 1], r[1, 0], r[1, 1]
            transform[0, 3], transform[2, 3], transform[1, 3] = shift[0], shift[1], float(np.mean(shifts))
    cache.write_text(json.dumps({'stamp': stamp, 'transform': transform.tolist(), 'match': round(match, 3)}))
    return transform, match


def load_frames(raw: Path):
    """Video keyframes plus, when the scan has them, the full-resolution wall stills (stills.jsonl)."""
    manifest = json.loads((raw / 'manifest.json').read_text())
    depth_w, depth_h = manifest['depthSize']
    frames = []
    sources = [(raw / 'frames.jsonl', raw / 'frames', '{}.jpg', raw / 'depth', '{}.bin', False)]
    to_scan = np.eye(4)
    if (raw / 'stills.jsonl').exists() and (raw / 'stills.jsonl').stat().st_size > 0:
        records = [json.loads(line) for line in (raw / 'stills.jsonl').read_text().splitlines() if line.strip()]
        to_scan, match = align_stills(raw, records)
        if match >= MIN_ALIGN_MATCH:
            sources.append((raw / 'stills.jsonl', raw / 'stills', '{}.jpg', raw / 'stills', '{}.depth.bin', True))
    for index_file, image_dir, image_name, depth_dir, depth_name, still in sources:
        for line in index_file.read_text().splitlines():
            if not line.strip():
                continue
            f = json.loads(line)
            image = cv2.imread(str(image_dir / image_name.format(f['frame'])))
            if image is None:
                continue
            k = np.array(f['intrinsics'], dtype=np.float64).reshape(3, 3).T
            scale = min(1.0, MAX_STILL_WIDTH / image.shape[1])
            if scale < 1.0:
                image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                k[:2] *= scale
            depth = None
            depth_file = depth_dir / depth_name.format(f['frame'])
            size = f.get('depthSize') or [depth_w, depth_h]
            if f.get('depth') and depth_file.exists():
                depth = np.fromfile(depth_file, dtype='<f4').reshape(size[1], size[0])
            small = cv2.cvtColor(cv2.resize(image, (480, 360)), cv2.COLOR_BGR2GRAY)
            frames.append({
                'name': f['frame'], 'image': image, 'depth': depth, 'pose': (to_scan @ raw_still_pose(f)) if still else matrix(f['cameraToWorld']),
                'fx': k[0, 0], 'fy': k[1, 1], 'cx': k[0, 2], 'cy': k[1, 2], 'still': still,
                'sharpness': float(cv2.Laplacian(small, cv2.CV_64F).var()),
            })
    # A still carries more pixels per metre of wall than a video frame from the same spot, in proportion to its
    # focal length in pixels. Video frames all count as 1, so scans without stills rank exactly as before.
    video_fx = float(np.median([f['fx'] for f in frames if not f['still']] or [1.0]))
    for f in frames:
        f['detail'] = f['fx'] / video_fx if f['still'] else 1.0
    # Rank sharpness so one very crisp frame does not drown out the rest.
    order = np.argsort([f['sharpness'] for f in frames])
    for rank, index in enumerate(order):
        frames[index]['sharp_rank'] = rank / max(1, len(frames) - 1)
    return frames


class Wall:
    """A wall plane with (u, v) coordinates as seen from inside the room: u right, v up, origin at the wall centre."""

    def __init__(self, wall):
        m = matrix(wall['transform'])
        self.id, self.width, self.height = wall['id'], wall['width'], wall['height']
        self.axis_x, self.axis_y, self.inward, self.origin = m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3]
        # RoomPlan's surface +Z faces into the room (checked on every wall of the sample scans). Guessing the side
        # from the room's centre instead picks the back of short jog walls in L-shaped rooms.
        self.flipped = False

    @classmethod
    def plane(cls, name, origin, axis_x, inward, width, height):
        """Any flat rectangle, e.g. a ceiling seen from below. axis_y is chosen so the view from the inward side is not mirrored."""
        surface = cls.__new__(cls)
        surface.id, surface.width, surface.height, surface.flipped = name, width, height, False
        surface.origin, surface.axis_x, surface.inward = np.asarray(origin, float), np.asarray(axis_x, float), np.asarray(inward, float)
        surface.axis_y = np.cross(surface.inward, surface.axis_x)
        return surface

    def points(self, u_view, v):
        """World positions for a grid of view coordinates."""
        uu, vv = np.meshgrid(-u_view if self.flipped else u_view, v)
        return self.origin + uu[..., None] * self.axis_x + vv[..., None] * self.axis_y

    def grid(self, ppm, center=(0.0, 0.0), size=None):
        width, height = size or (self.width, self.height)
        cols, rows = max(8, int(round(width * ppm))), max(8, int(round(height * ppm)))
        u = center[0] - width / 2 + (np.arange(cols) + 0.5) / ppm
        v = center[1] + height / 2 - (np.arange(rows) + 0.5) / ppm
        return self.points(u, v)


def project(wall: Wall, frame, points):
    """Where each wall point lands in a frame, whether it is in view, whether something blocks it, and a quality score."""
    world_to_cam = np.linalg.inv(frame['pose'])
    cam_position = frame['pose'][:3, 3]
    if np.dot(cam_position - wall.origin, wall.inward) < MIN_DISTANCE:
        return None
    pc = points @ world_to_cam[:3, :3].T + world_to_cam[:3, 3]
    distance = -pc[..., 2]  # ARKit cameras look down -Z
    safe = np.where(distance > MIN_DISTANCE, distance, np.inf)
    px = (frame['cx'] + frame['fx'] * pc[..., 0] / safe).astype(np.float32)
    py = (frame['cy'] - frame['fy'] * pc[..., 1] / safe).astype(np.float32)
    h, w = frame['image'].shape[:2]
    visible = (distance > MIN_DISTANCE) & (px >= 1) & (px < w - 1) & (py >= 1) & (py < h - 1)
    if not visible.any():
        return None
    ray = points - cam_position
    ray_length = np.maximum(np.linalg.norm(ray, axis=-1), 1e-6)
    cos_incidence = np.abs(ray @ wall.inward) / ray_length
    visible &= cos_incidence > MIN_COS_INCIDENCE
    # Wall-surface pixel density is proportional to cos(incidence) / distance, times the image's detail.
    score = (frame.get('detail', 1.0) * cos_incidence / ray_length * (0.5 + 0.5 * frame['sharp_rank'])).astype(np.float32)
    score[~visible] = 0
    blocked = np.zeros_like(visible)
    if frame['depth'] is not None:
        dh, dw = frame['depth'].shape
        measured = cv2.remap(frame['depth'], px * dw / w, py * dh / h, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        blocked = visible & (measured > 0) & (measured < distance - OCCLUSION_TOLERANCE)
    return px, py, visible, blocked, score


def plan_frames(wall: Wall, frames, ppm):
    """Choose which frame paints each pixel, preferring a few large patches so small objects are not cut by seams."""
    points = wall.grid(ppm / PLAN_SCALE)
    scores = np.zeros((len(frames),) + points.shape[:2], dtype=np.float32)
    for index, frame in enumerate(frames):
        result = project(wall, frame, points)
        if result is not None:
            _, _, visible, blocked, score = result
            scores[index] = np.where(visible & ~blocked, score, 0)
    best = scores.max(axis=0)
    good = (scores >= GOOD_ENOUGH * best) & (best > 0)
    labels = np.full(best.shape, -1, dtype=np.int16)
    uncovered = best > 0
    while uncovered.any():
        gains = (good & uncovered).reshape(len(frames), -1).sum(axis=1)
        pick = int(gains.argmax())
        if gains[pick] == 0:
            break
        take = good[pick] & uncovered
        labels[take] = pick
        uncovered &= ~take
    return labels


def build_wall(wall: Wall, frames, ppm):
    points = wall.grid(ppm)
    rows, cols = points.shape[:2]
    labels = cv2.resize(plan_frames(wall, frames, ppm), (cols, rows), interpolation=cv2.INTER_NEAREST)
    filled = np.zeros((rows, cols), dtype=bool)
    best_clear = np.zeros((rows, cols), dtype=np.float32)
    best_any = np.zeros((rows, cols), dtype=np.float32)
    color = np.zeros((rows, cols, 3), dtype=np.uint8)
    clear_color = np.zeros((rows, cols, 3), dtype=np.uint8)
    blocked_color = np.zeros((rows, cols, 3), dtype=np.uint8)
    source = np.full((rows, cols), -1, dtype=np.int16)
    clear_source = np.full((rows, cols), -1, dtype=np.int16)

    for index, frame in enumerate(frames):
        result = project(wall, frame, points)
        if result is None:
            continue
        px, py, visible, blocked, score = result
        sampled = cv2.remap(frame['image'], px, py, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        clear = visible & ~blocked
        planned = clear & (labels == index)
        color[planned], source[planned], filled[planned] = sampled[planned], index, True
        # Fallbacks for pixels the coarse plan missed: best clear view, then best blocked view.
        better = clear & (score > best_clear)
        clear_color[better], best_clear[better], clear_source[better] = sampled[better], score[better], index
        fallback = visible & (score > best_any)
        blocked_color[fallback], best_any[fallback] = sampled[fallback], score[fallback]

    patch = ~filled & (best_clear > 0)
    color[patch], source[patch] = clear_color[patch], clear_source[patch]
    seen = filled | patch
    # Where only blocked views exist, show them dimmed so furniture is recognisable but clearly not wall.
    hidden = ~seen & (best_any > 0)
    color[hidden] = (blocked_color[hidden] * 0.35 + 80).astype(np.uint8)
    color[~seen & ~hidden] = (40, 40, 40)
    return color, source, {
        'id': wall.id, 'width': wall.width, 'height': wall.height, 'flipped': wall.flipped, 'pixelsPerMeter': ppm,
        'coverage': round(float(seen.mean()), 3), 'blocked': round(float(hidden.mean()), 3),
    }


def best_frames(wall: Wall, frames, center, limit=3):
    """Frames with the clearest single view of one wall point, best first."""
    point = wall.grid(100, center, (0.02, 0.02))[:1, :1]
    ranked = []
    for index, frame in enumerate(frames):
        result = project(wall, frame, point)
        if result is not None and result[2][0, 0] and not result[3][0, 0]:
            ranked.append((float(result[4][0, 0]), index))
    return [index for _, index in sorted(ranked, reverse=True)[:limit]]


def depth_beyond(wall: Wall, frame, center):
    """How far past the wall plane the LiDAR saw at one wall point, in meters; None without depth.

    A large positive value means the camera was looking through a doorway or window at something behind the wall.
    """
    if frame['depth'] is None:
        return None
    point = wall.grid(100, center, (0.02, 0.02))[:1, :1]
    result = project(wall, frame, point)
    if result is None or not result[2][0, 0]:
        return None
    h, w = frame['image'].shape[:2]
    dh, dw = frame['depth'].shape
    x, y = int(result[0][0, 0] * dw / w), int(result[1][0, 0] * dh / h)
    window = frame['depth'][max(0, y - 2):y + 3, max(0, x - 2):x + 3]
    window = window[window > 0]
    if not window.size:
        return None
    pc = np.linalg.inv(frame['pose']) @ np.append(point[0, 0], 1.0)
    return float(np.median(window) + pc[2])  # camera looks down -Z, so expected depth is -pc[2]


def render_patch(wall: Wall, frame, center, size, ppm, with_mask=False):
    """A rectified close-up of part of the wall from one frame only, so it has no stitching seams.
    Pixels the frame did not see are filled grey; with `with_mask`, returns (image, seen) so callers can skip them."""
    points = wall.grid(ppm, center, size)
    result = project(wall, frame, points)
    if result is None:
        return None
    px, py, visible, _, _ = result
    image = cv2.remap(frame['image'], px, py, cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT)
    image[~visible] = (40, 40, 40)
    if visible.mean() <= 0.5:
        return None
    return (image, visible) if with_mask else image


def natural_crop(surface: Wall, frame, center, size, pixels=700):
    """The untouched camera view around one surface point, turned so the surface's up direction is up.

    Straightening a photo onto a plane stretches anything that hangs down or sticks out of it; this view does not.
    """
    world = surface.grid(100, center, (0.02, 0.02))[0, 0]
    ahead = surface.grid(100, (center[0], center[1] + 0.1), (0.02, 0.02))[0, 0]
    world_to_cam = np.linalg.inv(frame['pose'])

    def pixel(point):
        pc = world_to_cam[:3, :3] @ point + world_to_cam[:3, 3]
        return frame['cx'] + frame['fx'] * pc[0] / -pc[2], frame['cy'] - frame['fy'] * pc[1] / -pc[2], -pc[2]

    x0, y0, distance = pixel(world)
    x1, y1, _ = pixel(ahead)
    h, w = frame['image'].shape[:2]
    if distance < MIN_DISTANCE or not (0 <= x0 < w and 0 <= y0 < h):
        return None
    radius = size / 2 * frame['fx'] / distance * 1.3  # a little wider than the straightened close-up
    turn = 90 - np.degrees(np.arctan2(-(y1 - y0), x1 - x0))
    transform = cv2.getRotationMatrix2D((float(x0), float(y0)), float(turn), pixels / (2 * radius))
    transform[0, 2] += pixels / 2 - x0
    transform[1, 2] += pixels / 2 - y0
    return cv2.warpAffine(frame['image'], transform, (pixels, pixels), flags=cv2.INTER_CUBIC, borderValue=(40, 40, 40))


def depth_points(raw: Path, max_range=4.5, with_frames=False, min_confidence=1):
    """Every confident LiDAR depth sample of a scan as world-space points with surface normals facing the camera.

    With `with_frames`, also returns each point's frame number in capture order."""
    points, normals, ids = [], [], []
    for frame in frame_observations(raw, min_confidence, max_range):
        points.append(frame['points']); normals.append(frame['normals'])
        ids.append(np.full(len(frame['points']),frame['frameId'],np.int32))
    p = np.concatenate(points) if points else np.empty((0,3))
    n = np.concatenate(normals) if normals else np.empty((0,3))
    f = np.concatenate(ids) if ids else np.empty(0,np.int32)
    return (p,n,f) if with_frames else (p,n)


def ceiling_surfaces(capture, room, min_area=0.4):
    """Planes for a measured ceiling, seen from below: the main ceiling over the whole room, then each lower region.

    Needs the `ceiling` that ceiling.py writes into the room. Returns (surface, height above floor) pairs.
    """
    walls = [(matrix(w['transform']), w) for w in capture['walls']]
    floor_y = min(m[1, 3] - w['height'] / 2 for m, w in walls)
    longest = max(walls, key=lambda mw: mw[1]['width'])[0]
    axis_x = np.array([longest[0, 0], 0.0, longest[2, 0]])
    axis_x /= np.linalg.norm(axis_x)
    down = np.array([0.0, -1.0, 0.0])
    axis_y = np.cross(down, axis_x)

    def surface(name, outline, height):
        corners = np.array([[p['x'], 0.0, p['z']] for p in outline])
        a, b = corners @ axis_x, corners @ axis_y
        center = axis_x * (a.min() + a.max()) / 2 + axis_y * (b.min() + b.max()) / 2
        center[1] = floor_y + height
        plane = Wall.plane(name, center, axis_x, down, a.max() - a.min() + 0.1, b.max() - b.min() + 0.1)
        # The plane is the outline's bounding box; an L-shaped bulkhead's box spans most of the room. Keep the
        # outline so searches can stay on the surface itself.
        plane.outline = np.array([[p['x'], p['z']] for p in outline], np.float32)
        return plane, height

    ceiling = room['ceiling']
    result = [surface('ceiling', room['floor'], ceiling['height'])]
    for index, region in enumerate(ceiling.get('regions', []), start=1):
        xs, zs = [p['x'] for p in region['points']], [p['z'] for p in region['points']]
        area = abs(sum(xs[i] * zs[(i + 1) % len(xs)] - xs[(i + 1) % len(xs)] * zs[i] for i in range(len(xs)))) / 2
        if area >= min_area and region['height'] < ceiling['height'] - 0.05:
            result.append(surface(f'ceiling-region-{index}', region['points'], region['height']))
    return result


def load_scan(capture_path: Path, raw: Path):
    capture = json.loads(capture_path.read_text())
    return capture, [Wall(w) for w in capture['walls']], load_frames(raw)


def main():
    capture_path, raw, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    ppm = float(sys.argv[4]) if len(sys.argv) > 4 else 400.0
    _, walls, frames = load_scan(capture_path, raw)
    out.mkdir(parents=True, exist_ok=True)
    metas = []
    for number, wall in enumerate(walls, start=1):
        image, source, meta = build_wall(wall, frames, ppm)
        meta['image'] = f'wall{number}.png'
        meta['frames'] = [frames[i]['name'] for i in sorted(set(source[source >= 0].tolist()))]
        cv2.imwrite(str(out / meta['image']), image)
        metas.append(meta)
        print(f"wall {number}: {meta['width']:.2f} x {meta['height']:.2f} m, seen {meta['coverage']:.0%}, blocked {meta['blocked']:.0%}, {len(meta['frames'])} frames")
    (out / 'walls.json').write_text(json.dumps({'scan': capture_path.stem, 'walls': metas}, indent=2))


if __name__ == '__main__':
    main()
