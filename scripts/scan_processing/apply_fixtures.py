"""Add detected electrical fixtures to a Roomform room file.

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
    detected = json.loads(Path(sys.argv[2]).read_text())['fixtures']
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
        width, height = min(1.0, item['width']), min(1.0, item['height'])
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
        fixtures.append(fixture)
    room['fixtures'] = fixtures
    room['updatedAt'] = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    Path(sys.argv[3]).write_text(json.dumps(room, indent=2))
    print(f"{room['name']}: {len(fixtures)} fixtures written to {sys.argv[3]}")


if __name__ == '__main__':
    main()
