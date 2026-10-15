"""Build a fronto-parallel photo ("elevation") of every wall in a Roomform scan.

Usage: elevation.py <capture.json> <raw-bundle-dir> <out-dir> [pixels-per-meter]

Each wall image is seen from inside the room, at a fixed metric scale, stitched from the
raw keyframes. Pixels where furniture stands in front of the wall are dimmed.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

OCCLUSION_TOLERANCE = 0.12  # meters in front of the wall plane before a pixel counts as blocked
MIN_COS_INCIDENCE = 0.25    # ignore views more oblique than ~75 degrees
MIN_DISTANCE = 0.3
PLAN_SCALE = 4              # frame selection runs on a grid this many times coarser
GOOD_ENOUGH = 0.6           # a frame may cover a pixel if it scores this fraction of the best view


def matrix(values):
    return np.array(values, dtype=np.float64).reshape(4, 4).T  # stored column-major


def load_frames(raw: Path):
    manifest = json.loads((raw / 'manifest.json').read_text())
    depth_w, depth_h = manifest['depthSize']
    frames = []
    for line in (raw / 'frames.jsonl').read_text().splitlines():
        if not line.strip():
            continue
        f = json.loads(line)
        image = cv2.imread(str(raw / 'frames' / f"{f['frame']}.jpg"))
        if image is None:
            continue
        depth = None
        depth_file = raw / 'depth' / f"{f['frame']}.bin"
        if f.get('depth') and depth_file.exists():
            depth = np.fromfile(depth_file, dtype='<f4').reshape(depth_h, depth_w)
        k = np.array(f['intrinsics'], dtype=np.float64).reshape(3, 3).T
        small = cv2.cvtColor(cv2.resize(image, (480, 360)), cv2.COLOR_BGR2GRAY)
        frames.append({
            'name': f['frame'], 'image': image, 'depth': depth, 'pose': matrix(f['cameraToWorld']),
            'fx': k[0, 0], 'fy': k[1, 1], 'cx': k[0, 2], 'cy': k[1, 2],
            'sharpness': float(cv2.Laplacian(small, cv2.CV_64F).var()),
        })
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
    # Wall-surface pixel density is proportional to cos(incidence) / distance.
    score = (cos_incidence / ray_length * (0.5 + 0.5 * frame['sharp_rank'])).astype(np.float32)
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


def render_patch(wall: Wall, frame, center, size, ppm):
    """A rectified close-up of part of the wall from one frame only, so it has no stitching seams."""
    points = wall.grid(ppm, center, size)
    result = project(wall, frame, points)
    if result is None:
        return None
    px, py, visible, _, _ = result
    image = cv2.remap(frame['image'], px, py, cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT)
    image[~visible] = (40, 40, 40)
    return image if visible.mean() > 0.5 else None


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


def depth_points(raw: Path, max_range=4.5, with_frames=False):
    """Every confident LiDAR depth sample of a scan as world-space points with surface normals facing the camera.

    With `with_frames`, also returns each point's frame number in capture order."""
    manifest = json.loads((raw / 'manifest.json').read_text())
    dw, dh = manifest['depthSize']
    u, v = np.meshgrid(np.arange(dw) + 0.5, np.arange(dh) + 0.5)
    points, normals, order = [], [], []
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
        normal[np.sum(normal * camera, axis=-1) > 0] *= -1
        pose = matrix(f['cameraToWorld'])
        keep = (confidence >= 1) & (depth > 0.3) & (depth < max_range)
        points.append((camera @ pose[:3, :3].T + pose[:3, 3])[keep])
        normals.append((normal @ pose[:3, :3].T)[keep])
        order.append(np.full(int(keep.sum()), len(order), np.int32))
    if with_frames:
        return np.concatenate(points), np.concatenate(normals), np.concatenate(order)
    return np.concatenate(points), np.concatenate(normals)


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
        return Wall.plane(name, center, axis_x, down, a.max() - a.min() + 0.1, b.max() - b.min() + 0.1), height

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
