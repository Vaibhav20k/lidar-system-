"""Find switch boards and sockets on the walls of a Roomform scan with a vision model on OpenRouter.

Usage: detect.py <capture.json> <raw-bundle-dir> <out-dir> [--dry-run]

Stage 1 looks at overlapping tiles of each stitched wall elevation and proposes candidates.
Stage 2 re-renders every candidate from single frames (no stitching seams) and asks the model
to confirm it, classify it and tighten its box. Results land in <out-dir>/fixtures.json in the
same wall-relative terms the room model uses for doors and windows.

Needs OPENROUTER_API_KEY in the environment, in a .env file next to this script, or in the repo's .env.
Running it sends wall photos from the scan to OpenRouter and the model provider.
OPENROUTER_MODEL overrides the model.
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

from elevation import best_frames, build_wall, depth_beyond, load_scan, render_patch

HERE = Path(__file__).resolve().parent
DEFAULT_MODEL = 'google/gemini-3.8-flash'
PPM = 400            # stage 1 elevation scale, pixels per meter
TILE = 1.5           # stage 1 tile edge in meters
OVERLAP = 0.55       # must exceed MAX_SIDE so every board is whole in some tile
PATCH = 0.7          # stage 2 close-up edge in meters
PATCH_PPM = 1200
MIN_SIDE, MAX_SIDE = 0.04, 0.5  # plausible plate size in meters
BEHIND_WALL = 0.25     # depth this far past the wall plane means the view goes through an opening

KINDS = ['switch_board', 'socket', 'switch', 'regulator', 'distribution_box', 'other_electrical']

STAGE1_PROMPT = f"""This image is a straightened photo of part of an interior wall in an Indian home, stitched from several camera frames, so it may contain seams, ghosting or duplicated edges. Dimmed grey regions are furniture standing in front of the wall: ignore anything inside them.

Find every wall-mounted electrical plate: switch boards (a plate carrying switches, often with sockets or a fan regulator), single switches, power sockets, and distribution or MCB boxes. Do not report lights, fans, picture frames, mirrors, door handles, tiles, TVs, cables or stains.

Reply with JSON only: {{"items": [{{"kind": one of {KINDS}, "box": [ymin, xmin, ymax, xmax], "confidence": 0 to 1}}]}}. Box values are integers from 0 to 1000 as fractions of image height and width, tight around the whole plate. If there are none reply {{"items": []}}."""

STAGE2_PROMPT = f"""Each image is a straightened close-up of the same {PATCH * 100:.0f} cm square of an interior wall in an Indian home, taken from different camera frames. A detector thinks there is a wall-mounted electrical plate (switch board, switch, socket, regulator, MCB box) near the centre.

Judge from all images, then answer for the FIRST image. Reply with JSON only:
{{"present": true or false, "kind": one of {KINDS}, "box": [ymin, xmin, ymax, xmax], "switches": integer or null, "sockets": integer or null, "regulators": integer or null, "confidence": 0 to 1, "note": "short reason"}}
Box values are integers from 0 to 1000 as fractions of the first image's height and width, tight around the whole plate nearest the centre. Use null for a count you cannot read. If it is not an electrical plate, set present to false."""


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
        request = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', data=json.dumps(body).encode(), headers={'Authorization': f'Bearer {self.key}', 'Content-Type': 'application/json', 'X-Title': 'Roomform switch detection'})
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


def tile_starts(length):
    if length <= TILE:
        return [0.0]
    count = int(np.ceil((length - OVERLAP) / (TILE - OVERLAP)))
    return [i * (length - TILE) / (count - 1) for i in range(count)]


def propose(model, wall, image, out, number):
    """Stage 1: candidates as (u, v, width, height, kind, confidence) in wall view meters, centre origin."""
    candidates = []
    for x0 in tile_starts(wall.width):
        for y0 in tile_starts(wall.height):
            w, h = min(TILE, wall.width), min(TILE, wall.height)
            crop = image[int(y0 * PPM):int((y0 + h) * PPM), int(x0 * PPM):int((x0 + w) * PPM)]
            if (crop.reshape(-1, 3).std(axis=0).mean() < 4):
                continue  # featureless or fully unseen tile
            crop = cv2.resize(crop, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)
            cv2.imwrite(str(out / 'tiles' / f'wall{number}_{x0:.2f}_{y0:.2f}.jpg'), crop)
            if model is None:
                continue
            for item in model.ask(STAGE1_PROMPT, [crop]).get('items') or []:
                box = valid_box(item.get('box')) if isinstance(item, dict) else None
                if not box:
                    continue
                ymin, xmin, ymax, xmax = box
                width, height = (xmax - xmin) * w, (ymax - ymin) * h
                if not (MIN_SIDE <= max(width, height) <= MAX_SIDE * 1.5):
                    continue
                u = x0 + (xmin + xmax) / 2 * w - wall.width / 2
                v = wall.height / 2 - (y0 + (ymin + ymax) / 2 * h)
                candidates.append({'u': u, 'v': v, 'width': width, 'height': height, 'kind': item.get('kind'), 'confidence': confidence_of(item.get('confidence'))})
    return merge(candidates)


def merge(items):
    """Collapse detections of the same plate seen in overlapping tiles, keeping the most confident."""
    def same(a, b):
        # Same plate if the boxes mostly coincide. Neighbouring plates touch or sit side by side, so centre distance alone would swallow them.
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


def confirm(model, wall, frames, candidate, out, tag):
    """Stage 2: verify one candidate on seam-free single-frame close-ups and refine its box."""
    center = (candidate['u'], candidate['v'])
    views = best_frames(wall, frames, center)
    beyond = [d for d in (depth_beyond(wall, frames[i], center) for i in views) if d is not None]
    if beyond and float(np.median(beyond)) > BEHIND_WALL:
        print(f"  {tag}: skipped, seen {np.median(beyond):.2f} m behind the wall through an opening")
        return None
    patches = [p for p in (render_patch(wall, frames[i], center, (PATCH, PATCH), PATCH_PPM) for i in views) if p is not None]
    if not patches:
        return None
    for index, patch in enumerate(patches):
        cv2.imwrite(str(out / 'candidates' / f'{tag}_{index}.jpg'), patch)
    answer = model.ask(STAGE2_PROMPT, patches)
    box = valid_box(answer.get('box'))
    if not answer.get('present') or not box:
        return None
    ymin, xmin, ymax, xmax = box
    width, height = (xmax - xmin) * PATCH, (ymax - ymin) * PATCH
    if not (MIN_SIDE <= min(width, height) and max(width, height) <= MAX_SIDE):
        return None
    count = lambda key: answer.get(key) if isinstance(answer.get(key), int) else None
    u, v = center[0] - PATCH / 2 + (xmin + xmax) / 2 * PATCH, center[1] + PATCH / 2 - (ymin + ymax) / 2 * PATCH
    if abs(u) > wall.width / 2 or abs(v) > wall.height / 2:
        return None  # the close-up reaches past the wall's edge; a plate centred out there belongs to another surface
    return {
        'u': u, 'v': v,
        'width': width, 'height': height, 'kind': answer.get('kind') if answer.get('kind') in KINDS else 'other_electrical',
        'switches': count('switches'), 'sockets': count('sockets'), 'regulators': count('regulators'),
        'confidence': confidence_of(answer.get('confidence')), 'note': str(answer.get('note') or '')[:200], 'views': len(patches),
    }


def to_fixture(wall, item, number):
    """Express a confirmed plate like a door or window: offset along the wall from its start, sill above its base."""
    u_local = -item['u'] if wall.flipped else item['u']
    return {
        'wallId': wall.id, 'wall': number, 'wallWidth': round(wall.width, 3), 'wallHeight': round(wall.height, 3), 'kind': item['kind'],
        'offset': round(u_local + wall.width / 2 - item['width'] / 2, 3), 'sill': round(item['v'] + wall.height / 2 - item['height'] / 2, 3),
        'width': round(item['width'], 3), 'height': round(item['height'], 3),
        'centerHeight': round(item['v'] + wall.height / 2, 3),
        'switches': item['switches'], 'sockets': item['sockets'], 'regulators': item['regulators'],
        'confidence': round(item['confidence'], 2), 'views': item['views'], 'note': item['note'], 'evidence': 'estimated',
    }


def annotate(image, wall, items):
    marked = image.copy()
    for item in items:
        x0, x1 = (item['u'] - item['width'] / 2 + wall.width / 2) * PPM, (item['u'] + item['width'] / 2 + wall.width / 2) * PPM
        y0, y1 = (wall.height / 2 - item['v'] - item['height'] / 2) * PPM, (wall.height / 2 - item['v'] + item['height'] / 2) * PPM
        cv2.rectangle(marked, (int(x0), int(y0)), (int(x1), int(y1)), (0, 0, 255), 3)
        label = f"{item['kind']} {item['confidence']:.2f}"
        cv2.putText(marked, label, (int(x0), max(18, int(y0) - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    return marked


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    dry_run = '--dry-run' in sys.argv
    capture_path, raw, out = Path(args[0]), Path(args[1]), Path(args[2])
    for folder in ('tiles', 'candidates'):
        (out / folder).mkdir(parents=True, exist_ok=True)
    model = None if dry_run else Model(out / 'cache')
    if model is not None and not model.key:
        raise SystemExit('OPENROUTER_API_KEY is not set. Put it in a .env file or run with --dry-run to only write the tiles.')
    _, walls, frames = load_scan(capture_path, raw)
    fixtures = []
    for number, wall in enumerate(walls, start=1):
        image, _, _ = build_wall(wall, frames, PPM)
        cv2.imwrite(str(out / f'wall{number}.png'), image)
        candidates = propose(model, wall, image, out, number)
        confirmed = []
        for index, candidate in enumerate(candidates):
            result = confirm(model, wall, frames, candidate, out, f'wall{number}_{index}')
            if result:
                confirmed.append(result)
        confirmed = merge(confirmed)
        cv2.imwrite(str(out / f'wall{number}_detected.jpg'), annotate(image, wall, confirmed))
        fixtures += [to_fixture(wall, item, number) for item in confirmed]
        print(f'wall {number}: {len(candidates)} candidates, {len(confirmed)} confirmed')
    if model is None:
        print(f"dry run: tiles written to {out / 'tiles'}; nothing was sent")
        return
    (out / 'fixtures.json').write_text(json.dumps({'scan': capture_path.stem, 'model': model.model, 'fixtures': fixtures}, indent=2))
    print(f'{len(fixtures)} fixtures; {model.calls} new API calls, {model.tokens} tokens, ${model.cost:.4f}')


if __name__ == '__main__':
    main()
