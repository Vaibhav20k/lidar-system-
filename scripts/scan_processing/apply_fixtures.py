"""Add detected wall fixtures and ceiling items to a Roomform room file.

Usage: apply_fixtures.py <room.json> <fixtures.json> <out-room.json>
"""
import json
import sys
import uuid
from datetime import datetime, timezone
from math import hypot
from pathlib import Path


def main():
    room = json.loads(Path(sys.argv[1]).read_text())
    results = json.loads(Path(sys.argv[2]).read_text())
    detected = results['fixtures']
    walls = {w['id']: w for w in room['walls']}
    fixtures = []
    for item in detected:
        wall = walls.get(item['wallId'])
        if not wall:
            print(f"skipped {item['kind']}: wall {item['wallId'][:8]} is not in this room")
            continue
        length = hypot(wall['end']['x'] - wall['start']['x'], wall['end']['z'] - wall['start']['z'])
        # Detections are measured on the scanned wall. If the room was rescaled or its corners moved since, they no longer apply.
        if abs(item.get('wallWidth', length) - length) > 0.02 or abs(item.get('wallHeight', wall['height']) - wall['height']) > 0.02:
            raise SystemExit(f"Wall {wall['id'][:8]} in this room no longer matches the scan the detections came from. Apply fixtures to the unedited room.")
        width, height = min(2.0, item['width']), min(1.0, item['height'])
        if item['offset'] + width / 2 < 0 or item['offset'] + width / 2 > length or item['sill'] + height / 2 > wall['height']:
            print(f"skipped {item['kind']}: its centre is off wall {wall['id'][:8]}")
            continue
        fixture = {
            'id': str(uuid.uuid4()), 'wallId': wall['id'], 'kind': item['kind'],
            # Keep the plate on its wall even if the detected box overshoots an edge slightly.
            'offset': round(max(0.0, min(item['offset'], length - width)), 3), 'sill': round(max(0.0, min(item['sill'], wall['height'] - height)), 3),
            'width': round(width, 3), 'height': round(height, 3), 'evidence': 'estimated', 'confidence': min(1.0, max(0.0, float(item.get('confidence') or 0))),
        }
        for key in ('switches', 'sockets'):
            if isinstance(item.get(key), int):
                fixture[key] = item[key]
        if item.get('note'):
            fixture['note'] = item['note'][:200]
        if item.get('review'):
            fixture['review'] = True
        if isinstance(item.get('depth'), (int, float)):
            fixture['depth'] = round(min(1.0, item['depth']), 3)
        fixtures.append(fixture)
    room['fixtures'] = fixtures
    overhead = results.get('ceilingItems') or []
    if overhead:
        if not room.get('ceiling'):
            raise SystemExit('Ceiling items were detected but this room has no measured ceiling. Run ceiling.py on it first.')
        items = []
        for item in overhead:
            # A ceiling item's box is width along the room's long axis by height across it.
            entry = {
                'id': str(uuid.uuid4()), 'kind': item['kind'], 'x': item['x'], 'z': item['z'], 'height': item['ceilingHeight'],
                'width': round(min(3.0, item['width']), 3), 'depth': round(min(3.0, item['height']), 3), 'rotation': item['rotation'],
                'evidence': 'estimated', 'confidence': min(1.0, max(0.0, float(item.get('confidence') or 0))),
            }
            if item.get('note'):
                entry['note'] = item['note'][:200]
            if item.get('review'):
                entry['review'] = True
            items.append(entry)
        # Hanging fixtures found by depth in ceiling.py stay; detected items are added to them.
        kept = [i for i in room['ceiling'].get('items') or [] if str(i.get('note', '')).startswith('Hanging fixture')]
        room['ceiling']['items'] = kept + items
    room['updatedAt'] = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    Path(sys.argv[3]).write_text(json.dumps(room, indent=2))
    print(f"{room['name']}: {len(fixtures)} wall fixtures and {len(overhead)} ceiling items written to {sys.argv[3]}")


if __name__ == '__main__':
    main()
