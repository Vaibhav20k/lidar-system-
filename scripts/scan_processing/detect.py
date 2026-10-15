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
import sys
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

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
Box values are integers from 0 to 1000 as fractions of the FIRST image's height and width, around the part of the item that touches the surface. "switches" and "sockets" apply to electrical plates only; use null when they do not apply or cannot be read. A lit lamp with glare around it is still a light. A "hole" is a real opening or cut-out, not a stain or shadow. If nothing of these kinds is at the centre, set present to false."""

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
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                reply = json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise SystemExit(f'OpenRouter returned {error.code}: {error.read().decode()[:400]}')
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


def confirm(model, surface, frames, candidate, out, tag, is_wall):
    """Stage 2: verify one candidate on seam-free single-frame close-ups and refine its box."""
    center = (candidate['u'], candidate['v'])
    views = best_frames(surface, frames, center)
    if is_wall:
        beyond = [d for d in (depth_beyond(surface, frames[i], center) for i in views) if d is not None]
        if beyond and float(np.median(beyond)) > BEHIND_WALL and candidate['kind'] != 'hole':
            print(f"  {tag}: skipped, seen {np.median(beyond):.2f} m behind the wall through an opening")
            return None
    # Big items need a wider close-up; keep the image near 850 px either way.
    patch = PATCH if max(candidate['width'], candidate['height']) < 0.4 else min(2.2, max(candidate['width'], candidate['height']) * 1.8)
    ppm = min(1200, 850 / patch)
    patches = [p for p in (render_patch(surface, frames[i], center, (patch, patch), ppm) for i in views) if p is not None]
    if not patches:
        return None
    # One straightened view to place the item, plus undistorted camera crops to recognise it.
    photos = [c for c in (natural_crop(surface, frames[i], center, patch) for i in views) if c is not None]
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
        confidence *= 0.7  # one camera frame cannot rule out glare, a shadow or a stitching accident
    return {
        'u': u, 'v': v, 'width': width, 'height': height, 'kind': kind, 'switches': count('switches'), 'sockets': count('sockets'),
        'confidence': confidence, 'note': str(answer.get('note') or '')[:200], 'views': len(patches),
    }


def wall_boxes(wall, points, normals, floor_y):
    """Depth-only: flat faces 10-45 cm in front of a wall, as boxes. This is how AC units are proposed."""
    cell = 0.025
    rel = points - wall.origin
    a, b, n = rel @ wall.axis_x, rel @ wall.axis_y, rel @ wall.inward
    ok = (np.abs(a) < wall.width / 2) & (np.abs(b) < wall.height / 2) & (n > 0.10) & (n < 0.45) & (normals @ wall.inward > 0.8)
    cols, rows = max(1, int(wall.width / cell)), max(1, int(wall.height / cell))
    ix = ((a[ok] + wall.width / 2) / cell).astype(int).clip(0, cols - 1)
    iy = ((wall.height / 2 - b[ok]) / cell).astype(int).clip(0, rows - 1)
    count, total = np.zeros((rows, cols), np.int32), np.zeros((rows, cols))
    np.add.at(count, (iy, ix), 1)
    np.add.at(total, (iy, ix), n[ok])
    mask = cv2.morphologyEx(cv2.morphologyEx((count >= 3).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    number, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    base = wall.origin[1] - wall.height / 2 - floor_y  # walls that start above the floor
    boxes = []
    for i in range(1, number):
        x, y, w, h, area = stats[i]
        proud = float(np.median((total / np.maximum(count, 1))[(labels == i) & (count > 0)]))
        boxes.append({
            'u': (x + w / 2) * cell - wall.width / 2, 'v': wall.height / 2 - (y + h / 2) * cell, 'width': w * cell, 'height': h * cell,
            'proud': proud, 'bottom': base + wall.height - (y + h) * cell, 'fill': area / (w * h),
        })
    return boxes


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


def search(model, surface, frames, ppm, prompt, out, tag, is_wall):
    image, _, _ = build_wall(surface, frames, ppm)
    cv2.imwrite(str(out / f'{tag}.png'), image)
    candidates = propose(model, surface, image, ppm, prompt, out, tag)
    confirmed = [r for r in (confirm(model, surface, frames, c, out, f'{tag}_{i}', is_wall) for i, c in enumerate(candidates)) if r] if model else []
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
    points, normals = depth_points(raw)

    fixtures, ceiling_items = [], []
    for number, wall in enumerate(walls, start=1):
        image, candidates, confirmed = search(model, wall, frames, PPM, WALL_PROMPT, out, f'wall{number}', True)
        boxes = [b for b in wall_boxes(wall, points, normals, floor_y) if ac_like(b)]
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

    if model is None:
        print(f"dry run: tiles written to {out / 'tiles'}; nothing was sent")
        return
    (out / 'fixtures.json').write_text(json.dumps({'scan': capture_path.stem, 'model': model.model, 'fixtures': fixtures, 'ceilingItems': ceiling_items}, indent=2))
    review = sum(1 for item in fixtures + ceiling_items if item['review'])
    print(f'{len(fixtures)} wall items, {len(ceiling_items)} ceiling items, {review} flagged for review; {model.calls} new API calls, {model.tokens} tokens, ${model.cost:.4f}')


if __name__ == '__main__':
    main()
