"""Find electrical plates, lights, holes and AC units in a Roomform scan.

Usage: detect.py <capture.json> <raw-bundle-dir> <out-dir> [--room room.json] [--dry-run]

Walls are always searched. With --room pointing at a room file that already has a measured `ceiling`
(see ceiling.py), the ceiling and its lower regions are searched too.

Every item needs two kinds of evidence before it is kept:
  1. A vision model (OpenRouter) proposes it on a stitched, straightened photo of the surface.
  2. The same model confirms it on seam-free close-ups rendered from single camera frames.
AC units go the other way round: LiDAR depth proposes a box standing proud of the wall and the model
confirms it on photos. Positions always come from geometry, never from the model's sense of space.
Items confirmed from one camera frame only, or with a weak answer, are kept but marked for review.

Results land in <out-dir>/fixtures.json. Needs OPENROUTER_API_KEY in the environment, in a .env file
next to this script, or in the repo's .env. Running it sends photos from the scan to OpenRouter and
the model provider; --dry-run only writes the tiles it would send.
"""
import base64
import hashlib
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from refit_walls import fit_wall
from elevation import Wall, best_frames, build_wall, ceiling_surfaces, depth_beyond, depth_points, load_scan, matrix, natural_crop, render_patch

HERE = Path(__file__).resolve().parent
DEFAULT_MODEL = 'google/gemini-3.8-flash'
PPM = 400            # wall elevation scale, pixels per meter
CEILING_PPM = 300    # ceilings are seen from further away and at a slant
TILE = 1.5           # stage 1 tile edge in meters
OVERLAP = 0.55       # must exceed the largest small plate so each is whole in some tile
PATCH = 0.7          # stage 2 close-up edge in meters for small items
MIN_SIDE = 0.03
BEHIND_WALL = 0.25   # depth this far past a wall plane means the view goes through an opening
REVIEW_BELOW = 0.75  # confidence under this is flagged for a person to check

PLATES = ['switch_board', 'socket', 'switch', 'regulator', 'distribution_box']
KINDS = PLATES + ['light', 'hole', 'ac_unit', 'junction_box', 'other_electrical']
# Largest plausible side in meters per kind; anything bigger is a misread.
MAX_SIDE = {'light': 1.8, 'hole': 0.8, 'ac_unit': 1.5}
DEFAULT_MAX_SIDE = 0.5

STITCH_NOTE = 'It is stitched from several camera frames, so it may contain seams, ghosting or duplicated edges. Dimmed grey regions are things standing in front of the surface: ignore anything inside them.'
REPLY_FORMAT = f'Reply with JSON only: {{"items": [{{"kind": one of {KINDS}, "box": [ymin, xmin, ymax, xmax], "confidence": 0 to 1}}]}}. Box values are integers from 0 to 1000 as fractions of image height and width, tight around the whole item. If there are none reply {{"items": []}}.'

WALL_PROMPT = f"""This image is a straightened photo of part of an interior wall in an Indian home or office. {STITCH_NOTE}

Find every one of these that is fixed to or cut into the wall:
- electrical plates: switch boards (a plate carrying switches, often with sockets or a fan regulator), single switches, power sockets, distribution or MCB boxes, and empty electrical boxes with no plate fitted (report those as "other_electrical");
- lights: tube lights, wall lamps, bulb holders, spotlights;
- holes: pipe or core-cut holes, cut-outs left for fittings, exhaust openings. Not windows, doors, ventilator grills, niches or shelves;
- junction boxes and ceiling roses.
Do not report picture frames, mirrors, door handles, tiles, TVs, cables, curtain rods or stains.

{REPLY_FORMAT}"""

CEILING_PROMPT = f"""This image is a straightened photo of part of a ceiling (or the underside of a beam or dropped ceiling) seen from below, in an Indian home or office. {STITCH_NOTE}

Find every one of these that is fixed to or cut into this surface:
- lights: surface-mounted cylinder or spot lights, recessed downlights, tube lights, bulb holders, panel lights, the ceiling mount of a hanging light;
- holes: cut-outs left for lights or fittings, pipe holes, open access hatches;
- junction boxes, ceiling roses and fan hooks (report as "junction_box");
- cassette AC units (report as "ac_unit").
Do not report conduit pipes, cables, wires, beams, mouldings, cornices, stains or reflections and glare from lamps.

{REPLY_FORMAT}"""

CONFIRM_PROMPT = """These images show the same spot on {surface} in an Indian home or office. A detector thinks there is a "{kind}" there.

The FIRST image is a straightened view of a {size:.0f} cm square of the surface with that spot at the centre. Straightening stretches anything that hangs down from or sticks out of the surface, so a short cylinder light can look like a long tube or a streak. The OTHER images are ordinary camera photos of the same spot (centred, somewhat wider) from different frames, with no stretching. Decide what the item is from the ordinary photos; use the first image only to mark where it is.

Reply with JSON only:
{{"present": true or false, "kind": one of {kinds}, "box": [ymin, xmin, ymax, xmax], "switches": integer or null, "sockets": integer or null, "confidence": 0 to 1, "note": "short reason"}}
Box values are integers from 0 to 1000 as fractions of the FIRST image's height and width, around the part of the item that touches the surface. "switches" and "sockets" apply to electrical plates only; use null when they do not apply or cannot be read. A lit lamp with glare around it is still a light; so is a spotlight, a short black or white cylinder with a bright round lens at one end, even when it looks like a rod or pipe. A "hole" is a real opening or cut-out, not a stain or shadow. The detector's position can be a few cm off, so the item may sit a little away from the centre: judge the middle half of the image. If nothing of these kinds is there, set present to false."""

AC_PROMPT = """Each image is a straightened close-up of the same {width:.1f} m wide part of an interior wall, taken from different camera frames. A depth sensor found a box about {box_w:.0f} cm wide and {box_h:.0f} cm tall standing {proud:.0f} cm out from the wall near the centre, {bottom:.1f} m above the floor.

Is that box the indoor unit of a split air conditioner? Judge from all images, then answer for the FIRST image. Reply with JSON only:
{{"present": true or false, "what": "ac_unit" or "cabinet" or "shelf" or "light" or "other", "box": [ymin, xmin, ymax, xmax], "confidence": 0 to 1, "note": "short reason"}}
Set present to true only for an air conditioner. Box values are integers from 0 to 1000 as fractions of the first image's height and width, tight around the whole unit."""


def api_key():
    key = os.environ.get('OPENROUTER_API_KEY')
    for env in (HERE / '.env', HERE.parents[1] / '.env'):
        if key or not env.exists():
            continue
        for line in env.read_text().splitlines():
            if line.strip().startswith('OPENROUTER_API_KEY='):
                key = line.split('=', 1)[1].strip().strip('"\'')
    return key


ATTEMPTS = 3        # tries per model request before giving up
OUTLINE_MARGIN = 0.05   # metres a ceiling candidate may sit outside its region's outline (edge fittings)
SAME_SPOT = 0.2         # ceiling items closer than this are one item seen twice
LOCAL = 0.3             # the wall around an item is measured this far either side of it
LOCAL_POINTS = 150      # LiDAR points needed there to trust the measurement
SWEEP_VIEWS, SWEEP_SIZE, SWEEP_PPM = 5, 0.45, 300   # photo agreement check: frames, patch edge (m), pixels per metre
SWEEP_REACH = 0.15      # how far in front of or behind the measured surface it looks
SWEEP_GAIN = 0.85       # the best depth must cut the disagreement by this much to be used
CLOSE_UP_VIEWS = 5      # camera frames looked at for each item
PHOTOS_AGREE = 0.03     # close-ups agreeing within this show the item in the same place


class Model:
    def __init__(self, cache_dir: Path):
        self.key, self.model = api_key(), os.environ.get('OPENROUTER_MODEL', DEFAULT_MODEL)
        self.cache_dir, self.calls, self.cost, self.tokens = cache_dir, 0, 0.0, 0
        cache_dir.mkdir(parents=True, exist_ok=True)

    def ask(self, prompt, images):
        """Send a prompt with images and return the parsed JSON reply. Replies are cached on disk by request."""
        encoded = [base64.b64encode(cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()).decode() for image in images]
        digest = hashlib.sha256(json.dumps([self.model, prompt, encoded]).encode()).hexdigest()[:24]
        cached = self.cache_dir / f'{digest}.json'
        if cached.exists():
            return json.loads(cached.read_text())
        body = {
            'model': self.model, 'temperature': 0, 'usage': {'include': True},
            'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': prompt}] + [{'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b}'}} for b in encoded]}],
        }
        request = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', data=json.dumps(body).encode(), headers={'Authorization': f'Bearer {self.key}', 'Content-Type': 'application/json', 'X-Title': 'Roomform fixture detection'})
        # A slow or dropped reply, or a busy provider, is worth another try; one failure must not lose the room.
        for attempt in range(ATTEMPTS):
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    reply = json.loads(response.read())
                break
            except urllib.error.HTTPError as error:
                if error.code not in (408, 429, 500, 502, 503, 504) or attempt == ATTEMPTS - 1:
                    raise SystemExit(f'OpenRouter returned {error.code}: {error.read().decode()[:400]}')
            except (socket.timeout, TimeoutError, urllib.error.URLError, ConnectionError) as error:
                if attempt == ATTEMPTS - 1:
                    raise SystemExit(f'OpenRouter did not answer after {ATTEMPTS} tries: {error}')
            time.sleep(5 * (attempt + 1))
        if 'error' in reply:
            raise SystemExit(f"OpenRouter error: {json.dumps(reply['error'])[:400]}")
        usage = reply.get('usage', {})
        self.calls, self.cost, self.tokens = self.calls + 1, self.cost + float(usage.get('cost') or 0), self.tokens + int(usage.get('total_tokens') or 0)
        parsed = parse_json(reply['choices'][0]['message']['content'] or '')
        if parsed:  # an unreadable reply must not be remembered as "nothing here"
            cached.write_text(json.dumps(parsed))
        return parsed


def parse_json(text):
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end < start:
        return {}
    try:
        value = json.loads(text[start:end + 1])
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def confidence_of(value):
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def valid_box(box):
    if not isinstance(box, list) or len(box) != 4 or not all(isinstance(v, (int, float)) for v in box):
        return None
    ymin, xmin, ymax, xmax = (min(1000.0, max(0.0, float(v))) / 1000 for v in box)
    return (ymin, xmin, ymax, xmax) if ymax > ymin and xmax > xmin else None


def max_side(kind):
    return MAX_SIDE.get(kind, DEFAULT_MAX_SIDE)


def tile_starts(length):
    if length <= TILE:
        return [0.0]
    count = int(np.ceil((length - OVERLAP) / (TILE - OVERLAP)))
    return [i * (length - TILE) / (count - 1) for i in range(count)]


def propose(model, surface, image, ppm, prompt, out, tag):
    """Stage 1: candidates as (u, v, width, height, kind, confidence) in surface view meters, centre origin."""
    candidates = []
    for x0 in tile_starts(surface.width):
        for y0 in tile_starts(surface.height):
            w, h = min(TILE, surface.width), min(TILE, surface.height)
            crop = image[int(y0 * ppm):int((y0 + h) * ppm), int(x0 * ppm):int((x0 + w) * ppm)]
            if crop.size == 0 or crop.reshape(-1, 3).std(axis=0).mean() < 4:
                continue  # featureless or fully unseen tile
            crop = cv2.resize(crop, None, fx=640 * 1.6 / (TILE * ppm), fy=640 * 1.6 / (TILE * ppm), interpolation=cv2.INTER_CUBIC)
            cv2.imwrite(str(out / 'tiles' / f'{tag}_{x0:.2f}_{y0:.2f}.jpg'), crop)
            if model is None:
                continue
            for item in model.ask(prompt, [crop]).get('items') or []:
                box = valid_box(item.get('box')) if isinstance(item, dict) else None
                kind = item.get('kind') if isinstance(item, dict) and item.get('kind') in KINDS else None
                if not box or not kind:
                    continue
                ymin, xmin, ymax, xmax = box
                width, height = (xmax - xmin) * w, (ymax - ymin) * h
                if not (MIN_SIDE <= max(width, height) <= max_side(kind) * 1.5):
                    continue
                u = x0 + (xmin + xmax) / 2 * w - surface.width / 2
                v = surface.height / 2 - (y0 + (ymin + ymax) / 2 * h)
                candidates.append({'u': u, 'v': v, 'width': width, 'height': height, 'kind': kind, 'confidence': confidence_of(item.get('confidence'))})
    return merge(candidates)


def merge(items):
    """Collapse detections of the same item seen in overlapping tiles, keeping the most confident."""
    def same(a, b):
        # A light and the box it is mounted on overlap but are two things; plates of any kind on one spot are one plate.
        if (a['kind'] == 'light') != (b['kind'] == 'light'):
            return False
        # Same item if the boxes mostly coincide. Neighbouring plates touch or sit side by side, so centre distance alone would swallow them.
        overlap_u = min(a['u'] + a['width'] / 2, b['u'] + b['width'] / 2) - max(a['u'] - a['width'] / 2, b['u'] - b['width'] / 2)
        overlap_v = min(a['v'] + a['height'] / 2, b['v'] + b['height'] / 2) - max(a['v'] - a['height'] / 2, b['v'] - b['height'] / 2)
        if overlap_u <= 0 or overlap_v <= 0:
            return False
        return overlap_u * overlap_v > 0.4 * min(a['width'] * a['height'], b['width'] * b['height'])

    kept = []
    for item in sorted(items, key=lambda i: -i['confidence']):
        if not any(same(item, k) for k in kept):
            kept.append(item)
    return kept


def same_place(items):
    """One ceiling item per spot and kind: a lamp's body and head, or one lamp seen on two surfaces, become one entry."""
    kept = []
    for item in sorted(items, key=lambda i: -i['confidence']):
        # Only the same kind: a spotlight and the conduit box it is mounted on sit a hand's width apart and are both real.
        if not any(item['kind'] == k['kind'] and np.hypot(item['x'] - k['x'], item['z'] - k['z']) < SAME_SPOT for k in kept):
            kept.append(item)
    return kept


def local_surface(surface, cloud, center):
    """The surface right around one spot, from the LiDAR: the plane moved to where the wall around the item really
    is, and whether there is solid wall there at all.

    A wall is not one flat plane: a strip beside a glass partition, a pier or a patch of cladding can sit several
    cm off the rest. Projecting a close-up onto the wrong depth slides the item sideways, by more the more slanted
    the photo, and an opening check made at the wrong depth sees "through" solid wall.
    """
    if cloud is None:
        return surface, False
    points, normals = cloud
    rel = points - (surface.origin + surface.axis_x * center[0] + surface.axis_y * center[1])
    a, b, n = rel @ surface.axis_x, rel @ surface.axis_y, rel @ surface.inward
    near = (np.abs(a) < LOCAL) & (np.abs(b) < LOCAL) & (np.abs(n) < 0.6) & (normals @ surface.inward > 0.8)
    if near.sum() < LOCAL_POINTS:
        return surface, False
    hist, edges = np.histogram(n[near], bins=np.arange(-0.6, 0.61, 0.01))
    support = np.convolve(hist, np.ones(3), 'same')
    layer = edges[support.argmax()] + 0.005
    on = near & (np.abs(n - layer) < 0.02)
    if on.sum() < LOCAL_POINTS:
        return surface, False
    offset = float(np.median(n[on]))
    moved = Wall.plane(surface.id, surface.origin + surface.inward * offset, surface.axis_x, surface.inward, surface.width, surface.height)
    moved.axis_y = surface.axis_y.copy()
    return moved, True


def agreeing_photos(patches, ppm):
    """The largest group of close-ups that show the spot in the same place, best-ranked first.

    Each close-up is one camera frame projected onto the surface. Lined up against each other, the frames that saw
    it correctly agree within a few cm; a frame taken at a steep angle beside glass, or whose pose slipped, lands
    10-20 cm away. The model must place the item on an agreeing frame, never on the odd one out.
    Returns (indices into patches, the largest distance between them in metres).
    """
    def edges(image):
        gray = cv2.GaussianBlur(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32), (5, 5), 0)
        magnitude = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1))
        return magnitude * cv2.createHanningWindow(magnitude.shape[::-1], cv2.CV_32F)
    shapes = {p.shape[:2] for p in patches}
    if len(patches) < 2 or len(shapes) > 1:
        return list(range(len(patches))), None
    lined = [edges(p) for p in patches]
    apart = np.array([[np.hypot(*cv2.phaseCorrelate(a, b)[0]) / ppm for b in lined] for a in lined])
    groups = [[j for j in range(len(patches)) if apart[i, j] <= PHOTOS_AGREE] for i in range(len(patches))]
    best = max(range(len(patches)), key=lambda i: (len(groups[i]), -i))  # ties go to the better-ranked frame
    group = groups[best]
    group = [best] + [j for j in group if j != best]
    return group, float(apart[np.ix_(group, group)].max())


def agreeing_depth(surface, frames, center):
    """Fine-tune a wall item's depth from the photos alone: try planes a centimetre apart and keep the one where the
    camera frames agree best on what is there. On the right plane every photo puts the item in the same place; on a
    wrong one each slides by its own angle. This settles items next to glass or mirrors, whose LiDAR depth is
    unreliable. Returns the surface moved to that plane, or the surface unchanged when the photos cannot tell."""
    views = best_frames(surface, frames, center, limit=SWEEP_VIEWS)
    if len(views) < 3:
        return surface
    def moved(offset):
        plane = Wall.plane(surface.id, surface.origin + surface.inward * offset, surface.axis_x, surface.inward, surface.width, surface.height)
        plane.axis_y = surface.axis_y.copy()
        return plane
    def disagreement(plane):
        images = [render_patch(plane, frames[i], center, (SWEEP_SIZE, SWEEP_SIZE), SWEEP_PPM) for i in views]
        images = [cv2.GaussianBlur(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY).astype(np.float32), (5, 5), 0) for im in images if im is not None]
        if len(images) < 3:
            return None
        stack = np.stack(images)
        valid = np.all(stack > 0, axis=0)
        if valid.mean() < 0.6:
            return None
        # Compare edges, not brightness: frames differ in exposure.
        edges = np.stack([np.abs(cv2.Sobel(im, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(im, cv2.CV_32F, 0, 1)) for im in images])
        return float(np.mean(np.std(edges, axis=0)[valid]) / (np.mean(edges[:, valid]) + 1e-6))
    scores = [(score, offset) for offset in np.arange(-SWEEP_REACH, SWEEP_REACH + 1e-9, 0.01) if (score := disagreement(moved(offset))) is not None]
    if len(scores) < 5:
        return surface
    best, offset = min(scores)
    at_zero = next((s for s, o in scores if abs(o) < 1e-9), None)
    # Only move when the photos clearly agree better somewhere else; a plain wall with nothing to line up says nothing.
    if at_zero is None or best > SWEEP_GAIN * at_zero:
        return surface
    return moved(offset)


def confirm(model, surface, frames, candidate, out, tag, is_wall, cloud=None):
    """Stage 2: verify one candidate on seam-free single-frame close-ups and refine its box."""
    center = (candidate['u'], candidate['v'])
    if is_wall:
        surface, solid = local_surface(surface, cloud, center)
        surface = agreeing_depth(surface, frames, center)
    views = best_frames(surface, frames, center, limit=CLOSE_UP_VIEWS)
    if is_wall and not solid:  # with solid wall measured all round the item, it is not seen through an opening
        beyond = [d for d in (depth_beyond(surface, frames[i], center) for i in views) if d is not None]
        if beyond and float(np.median(beyond)) > BEHIND_WALL and candidate['kind'] != 'hole':
            print(f"  {tag}: skipped, seen {np.median(beyond):.2f} m behind the wall through an opening")
            return None
    # Big items need a wider close-up; keep the image near 850 px either way.
    patch = PATCH if max(candidate['width'], candidate['height']) < 0.4 else min(2.2, max(candidate['width'], candidate['height']) * 1.8)
    ppm = min(1200, 850 / patch)
    rendered = [(i, p) for i, p in ((i, render_patch(surface, frames[i], center, (patch, patch), ppm)) for i in views) if p is not None]
    if not rendered:
        return None
    seen = len(rendered)
    group, spread = agreeing_photos([p for _, p in rendered], ppm)
    views, patches = [rendered[g][0] for g in group], [rendered[g][1] for g in group]
    # One straightened view to place the item, plus undistorted camera crops to recognise it, all from frames that agree.
    photos = [c for c in (natural_crop(surface, frames[i], center, patch) for i in views[:3]) if c is not None]
    images = patches[:1] + photos
    for index, image in enumerate(images):
        cv2.imwrite(str(out / 'candidates' / f'{tag}_{index}.jpg'), image)
    prompt = CONFIRM_PROMPT.format(size=patch * 100, surface='an interior wall' if is_wall else 'a ceiling seen from below', kind=candidate['kind'], kinds=KINDS)
    answer = model.ask(prompt, images)
    box = valid_box(answer.get('box'))
    kind = answer.get('kind') if answer.get('kind') in KINDS else None
    if not answer.get('present') or not box or not kind:
        return None
    ymin, xmin, ymax, xmax = box
    width, height = (xmax - xmin) * patch, (ymax - ymin) * patch
    if not (MIN_SIDE <= min(width, height) and max(width, height) <= max_side(kind)):
        return None
    u, v = center[0] - patch / 2 + (xmin + xmax) / 2 * patch, center[1] + patch / 2 - (ymin + ymax) / 2 * patch
    if abs(u) > surface.width / 2 or abs(v) > surface.height / 2:
        return None  # the close-up reaches past the surface's edge; an item centred out there belongs elsewhere
    count = lambda key: answer.get(key) if isinstance(answer.get(key), int) and kind in PLATES else None
    confidence = confidence_of(answer.get('confidence'))
    if len(patches) < 2:
        confidence *= 0.7  # one camera frame (or frames that disagree) cannot rule out glare, a shadow or a bad pose
    return {
        'u': u, 'v': v, 'width': width, 'height': height, 'kind': kind, 'switches': count('switches'), 'sockets': count('sockets'),
        'confidence': confidence, 'note': str(answer.get('note') or '')[:200], 'views': len(patches),
        'placedBy': f'{len(group)} of {seen} photos agree within {spread * 100:.1f} cm' if spread is not None else f'{seen} photo(s)',
    }


def real_surface(wall, points, normals, frames, floor_y):
    """The wall as the LiDAR saw it, and how far that surface is in front of RoomPlan's plane at each position.

    Photos must be projected onto the real surface: on a plane a few cm off, a plate seen at an angle lands to
    the side of where it is, and two photos taken from different angles put it in two different places. A wall
    too covered to fit keeps RoomPlan's plane.
    """
    fitted, _ = fit_wall(wall, points, normals, frames, floor_y, [])
    if not fitted:
        return wall, lambda u: 0.0
    offset, slope = fitted[2]['offset'], fitted[2]['slope']
    in_front = lambda u: offset + slope * (u + wall.width / 2)
    axis_x = wall.axis_x + slope * wall.inward
    inward = wall.inward - slope * wall.axis_x
    surface = Wall.plane(wall.id, wall.origin + wall.inward * in_front(0.0), axis_x / np.linalg.norm(axis_x), inward / np.linalg.norm(inward), wall.width, wall.height)
    surface.axis_y = wall.axis_y.copy()
    return surface, in_front


def wall_boxes(wall, points, normals, floor_y):
    """Depth-only: flat faces standing 10-45 cm out from a wall, as boxes. This is how AC units are proposed."""
    cell = 0.025
    rel = points - wall.origin
    a, b, n = rel @ wall.axis_x, rel @ wall.axis_y, rel @ wall.inward
    facing = (np.abs(a) < wall.width / 2) & (np.abs(b) < wall.height / 2) & (normals @ wall.inward > 0.8)
    proud_all = n
    ok = facing & (proud_all > 0.07) & (proud_all < 0.48)
    cols, rows = max(1, int(wall.width / cell)), max(1, int(wall.height / cell))
    ix = ((a[ok] + wall.width / 2) / cell).astype(int).clip(0, cols - 1)
    iy = ((wall.height / 2 - b[ok]) / cell).astype(int).clip(0, rows - 1)
    count, total = np.zeros((rows, cols), np.int32), np.zeros((rows, cols))
    np.add.at(count, (iy, ix), 1)
    np.add.at(total, (iy, ix), proud_all[ok])
    depth = total / np.maximum(count, 1)
    base = wall.origin[1] - wall.height / 2 - floor_y  # walls that start above the floor
    boxes = []
    # The whole 10-45 cm band at once finds a unit with a rounded front (its depth varies by several cm), and one
    # depth layer at a time separates a unit from a bulkhead edge or shelf right next to it. Both are tried.
    bands = [(0.275, 0.175)] + [(layer, 0.035) for layer in np.arange(0.10, 0.42, 0.02)]
    for layer, reach in bands:
        solid = ((count >= 3) & (np.abs(depth - layer) <= reach)).astype(np.uint8)
        mask = cv2.morphologyEx(cv2.morphologyEx(solid, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        number, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        for i in range(1, number):
            x, y, w, h, area = stats[i]
            inside = (labels == i) & (count > 0)
            boxes.append({
                'u': (x + w / 2) * cell - wall.width / 2, 'v': wall.height / 2 - (y + h / 2) * cell, 'width': w * cell, 'height': h * cell,
                'proud': float(np.median(depth[inside])), 'bottom': base + wall.height - (y + h) * cell, 'fill': area / (w * h),
            })
    # The same face found in several bands: keep one box per face, preferring an AC-shaped one, then the largest
    # (a small fragment is always "full" and must not push out the whole unit).
    kept = []
    for box in sorted(boxes, key=lambda b: (not ac_like(b), -b['width'] * b['height'])):
        # Boxes that cover the same part of the wall are one unit seen in several bands (its top, its rounded
        # front, its louvres), whatever their depth.
        def overlaps(k):
            du = min(box['u'] + box['width'] / 2, k['u'] + k['width'] / 2) - max(box['u'] - box['width'] / 2, k['u'] - k['width'] / 2)
            dv = min(box['v'] + box['height'] / 2, k['v'] + k['height'] / 2) - max(box['v'] - box['height'] / 2, k['v'] - k['height'] / 2)
            return du > 0 and dv > 0 and du * dv > 0.3 * min(box['width'] * box['height'], k['width'] * k['height'])
        if not any(overlaps(k) for k in kept):
            kept.append(box)
    return kept


def ac_like(box):
    return 0.55 <= box['width'] <= 1.4 and 0.18 <= box['height'] <= 0.5 and 0.10 <= box['proud'] <= 0.40 and box['bottom'] >= 1.6 and box['fill'] > 0.55


def confirm_ac(model, wall, frames, box, out, tag):
    """Check a depth-proposed box on photos, rendered on the plane of the box's front face so it is not distorted."""
    face = Wall.plane(wall.id, wall.origin + wall.inward * box['proud'], wall.axis_x, wall.inward, wall.width, wall.height)
    center, width = (box['u'], box['v']), max(1.4, box['width'] * 1.7)
    patches = [p for p in (render_patch(face, frames[i], center, (width, width * 0.7), 850 / width) for i in best_frames(face, frames, center)) if p is not None]
    if not patches:
        return None
    for index, image in enumerate(patches):
        cv2.imwrite(str(out / 'candidates' / f'{tag}_{index}.jpg'), image)
    answer = model.ask(AC_PROMPT.format(width=width, box_w=box['width'] * 100, box_h=box['height'] * 100, proud=box['proud'] * 100, bottom=box['bottom']), patches)
    if not answer.get('present') or answer.get('what') != 'ac_unit':
        print(f"  {tag}: depth box is not an AC ({answer.get('what')}: {str(answer.get('note') or '')[:80]})")
        return None
    refined = valid_box(answer.get('box'))
    item = {'u': box['u'], 'v': box['v'], 'width': box['width'], 'height': box['height']}
    if refined:  # the photo outlines the unit better than the coarse depth blob
        ymin, xmin, ymax, xmax = refined
        w, h = (xmax - xmin) * width, (ymax - ymin) * width * 0.7
        if 0.5 <= w <= max_side('ac_unit') and 0.15 <= h <= 0.6:
            item = {'u': center[0] - width / 2 + (xmin + xmax) / 2 * width, 'v': center[1] + width * 0.35 - (ymin + ymax) / 2 * width * 0.7, 'width': w, 'height': h}
    confidence = confidence_of(answer.get('confidence')) * (1.0 if len(patches) >= 2 else 0.7)
    return {**item, 'kind': 'ac_unit', 'proud': box['proud'], 'switches': None, 'sockets': None, 'confidence': confidence, 'note': str(answer.get('note') or '')[:200], 'views': len(patches)}


def common(item):
    return {
        'kind': item['kind'], 'width': round(item['width'], 3), 'height': round(item['height'], 3),
        'confidence': round(item['confidence'], 2), 'review': bool(item['confidence'] < REVIEW_BELOW), 'views': item['views'], 'note': item['note'], 'evidence': 'estimated',
        **({'placedBy': item['placedBy']} if item.get('placedBy') else {}),
    }


def to_fixture(wall, item, number):
    """Express a confirmed wall item like a door or window: offset along the wall from its start, sill above its base."""
    u_local = -item['u'] if wall.flipped else item['u']
    fixture = {
        'wallId': wall.id, 'wall': number, 'wallWidth': round(wall.width, 3), 'wallHeight': round(wall.height, 3),
        'offset': round(u_local + wall.width / 2 - item['width'] / 2, 3), 'sill': round(item['v'] + wall.height / 2 - item['height'] / 2, 3),
        'centerHeight': round(item['v'] + wall.height / 2, 3), 'switches': item['switches'], 'sockets': item['sockets'], **common(item),
    }
    if 'proud' in item:
        fixture['depth'] = round(item['proud'], 3)
    return fixture


def to_ceiling_item(surface, height, item):
    """A confirmed ceiling item in world X/Z, at the height of the surface it sits on."""
    x, _, z = surface.origin + surface.axis_x * item['u'] + surface.axis_y * item['v']
    return {
        'surface': surface.id, 'x': round(float(x), 3), 'z': round(float(z), 3), 'ceilingHeight': round(height, 3),
        # width runs along the room's long axis; rotation turns that axis into world X/Z for drawing.
        'rotation': round(float(np.arctan2(surface.axis_x[2], surface.axis_x[0])), 4), **common(item),
    }


def annotate(image, surface, ppm, items):
    marked = image.copy()
    for item in items:
        x0, x1 = (item['u'] - item['width'] / 2 + surface.width / 2) * ppm, (item['u'] + item['width'] / 2 + surface.width / 2) * ppm
        y0, y1 = (surface.height / 2 - item['v'] - item['height'] / 2) * ppm, (surface.height / 2 - item['v'] + item['height'] / 2) * ppm
        color = (0, 0, 255) if item['confidence'] >= REVIEW_BELOW else (0, 165, 255)  # orange = needs a person to check
        cv2.rectangle(marked, (int(x0), int(y0)), (int(x1), int(y1)), color, 3)
        cv2.putText(marked, f"{item['kind']} {item['confidence']:.2f}", (int(x0), max(18, int(y0) - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    return marked


def on_outline(surface, item):
    """Whether a ceiling candidate lies on its surface's outline (always true for walls and plain planes)."""
    outline = getattr(surface, 'outline', None)
    if outline is None:
        return True
    x, _, z = surface.origin + surface.axis_x * item['u'] + surface.axis_y * item['v']
    return cv2.pointPolygonTest(outline, (float(x), float(z)), True) >= -OUTLINE_MARGIN


def search(model, surface, frames, ppm, prompt, out, tag, is_wall, cloud=None):
    image, _, _ = build_wall(surface, frames, ppm)
    cv2.imwrite(str(out / f'{tag}.png'), image)
    # A lower region is photographed over its bounding box, which also shows the ceiling around it: what is seen
    # there belongs to the main ceiling and is found on it already.
    candidates = [c for c in propose(model, surface, image, ppm, prompt, out, tag) if on_outline(surface, c)]
    confirmed = [r for r in (confirm(model, surface, frames, c, out, f'{tag}_{i}', is_wall, cloud) for i, c in enumerate(candidates)) if r] if model else []
    return image, candidates, merge(confirmed)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    dry_run = '--dry-run' in sys.argv
    room_path = None
    if '--room' in sys.argv:
        room_path = Path(sys.argv[sys.argv.index('--room') + 1])
        args.remove(str(room_path))
    capture_path, raw, out = Path(args[0]), Path(args[1]), Path(args[2])
    for folder in ('tiles', 'candidates'):
        (out / folder).mkdir(parents=True, exist_ok=True)
    model = None if dry_run else Model(out / 'cache')
    if model is not None and not model.key:
        raise SystemExit('OPENROUTER_API_KEY is not set. Put it in a .env file or run with --dry-run to only write the tiles.')
    capture, walls, frames = load_scan(capture_path, raw)
    floor_y = min(matrix(w['transform'])[1, 3] - w['height'] / 2 for w in capture['walls'])
    points, normals, seen_at = depth_points(raw, with_frames=True)

    fixtures, ceiling_items = [], []
    for number, scanned in enumerate(walls, start=1):
        wall, in_front = real_surface(scanned, points, normals, seen_at, floor_y)
        image, candidates, confirmed = search(model, wall, frames, PPM, WALL_PROMPT, out, f'wall{number}', True, (points, normals))
        # Depth boxes are found against RoomPlan's plane (their limits were set that way), then measured from the real wall.
        boxes = [dict(b, proud=b['proud'] - in_front(b['u'])) for b in wall_boxes(scanned, points, normals, floor_y) if ac_like(b)]
        units = [r for r in (confirm_ac(model, wall, frames, b, out, f'wall{number}_ac{i}') for i, b in enumerate(boxes)) if r] if model else []
        # Photo-only "AC" guesses have no depth behind them, and anything overlapping a confirmed unit is part of it.
        confirmed = merge(units + [c for c in confirmed if c['kind'] != 'ac_unit'])
        cv2.imwrite(str(out / f'wall{number}_detected.jpg'), annotate(image, wall, PPM, confirmed))
        fixtures += [to_fixture(wall, item, number) for item in confirmed]
        print(f'wall {number}: {len(candidates)} photo candidates, {len(boxes)} AC-shaped depth boxes, {len(confirmed)} confirmed')

    if room_path:
        room = json.loads(room_path.read_text())
        if not room.get('ceiling'):
            raise SystemExit('The room file has no measured ceiling. Run ceiling.py on it first.')
        for surface, height in ceiling_surfaces(capture, room):
            image, candidates, confirmed = search(model, surface, frames, CEILING_PPM, CEILING_PROMPT, out, surface.id, False)
            cv2.imwrite(str(out / f'{surface.id}_detected.jpg'), annotate(image, surface, CEILING_PPM, confirmed))
            ceiling_items += [to_ceiling_item(surface, height, item) for item in confirmed]
            print(f'{surface.id} at {height:.2f} m: {len(candidates)} candidates, {len(confirmed)} confirmed')
        before = len(ceiling_items)
        ceiling_items = same_place(ceiling_items)
        if before > len(ceiling_items):
            print(f'ceiling: {before - len(ceiling_items)} repeated sightings of the same item dropped')

    if model is None:
        print(f"dry run: tiles written to {out / 'tiles'}; nothing was sent")
        return
    (out / 'fixtures.json').write_text(json.dumps({'scan': capture_path.stem, 'model': model.model, 'fixtures': fixtures, 'ceilingItems': ceiling_items}, indent=2))
    review = sum(1 for item in fixtures + ceiling_items if item['review'])
    print(f'{len(fixtures)} wall items, {len(ceiling_items)} ceiling items, {review} flagged for review; {model.calls} new API calls, {model.tokens} tokens, ${model.cost:.4f}')


if __name__ == '__main__':
    main()
