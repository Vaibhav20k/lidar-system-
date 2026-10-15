"""Measure every piece of furniture twice: the box RoomPlan reports, and the extent of the LiDAR points around it.

Usage: measure_objects.py <capture.json> <cloud.bin> [name]

For each RoomPlan object the scanned points inside its box, grown by 12 cm, are collected; points on wall
planes, on the floor and on the ceiling are dropped. The LiDAR size is the spread of what is left along the
box's own width and depth axes (1st to 99th percentile, so a few stray points do not stretch it), and its
height is the 99th percentile above the floor. Objects that touch another object are flagged: the grown box
then also catches the neighbour's points, so that LiDAR size is not trustworthy.
"""
import json
import sys
from pathlib import Path

import numpy as np

from elevation import Wall, matrix

PAD = 0.12          # how far beyond RoomPlan's box to look for the object's points
ON_WALL = 0.035     # points this close to a wall plane are the wall
MIN_POINTS = 150


def load_cloud(path: Path):
    data = path.read_bytes()
    count = int.from_bytes(data[8:12], 'little')
    positions = np.frombuffer(data, '<f4', count * 3, 16).reshape(-1, 3)
    groups = np.frombuffer(data, np.uint8, count, 16 + count * 15)
    return positions, groups


def main():
    capture = json.loads(Path(sys.argv[1]).read_text())
    positions, groups = load_cloud(Path(sys.argv[2]))
    name = sys.argv[3] if len(sys.argv) > 3 else Path(sys.argv[1]).stem
    walls = [Wall(w) for w in capture['walls']]
    floor_y = min(w.origin[1] - w.height / 2 for w in walls)
    world = positions + np.array([0, floor_y, 0])

    # Points that are the walls themselves, so furniture standing against a wall does not take the wall with it.
    on_wall = np.zeros(len(world), bool)
    for wall in walls:
        rel = world - wall.origin
        on_wall |= (np.abs(rel @ wall.inward) < ON_WALL) & (np.abs(rel @ wall.axis_x) < wall.width / 2 + 0.05)
    usable = ~on_wall & (groups != 255) & (positions[:, 1] > 0.03)

    boxes = []
    for o in capture['objects']:
        m = matrix(o['transform'])
        center = m[:3, 3] - np.array([0, floor_y, 0])
        boxes.append((o, m[:3, 0], m[:3, 2], center))

    rows = []
    for index, (o, axis_x, axis_z, center) in enumerate(boxes):
        rel = positions - center
        a, b = rel @ axis_x, rel @ axis_z
        top = center[1] + o['height'] / 2
        near = usable & (np.abs(a) < o['width'] / 2 + PAD) & (np.abs(b) < max(o['depth'], 0.02) / 2 + PAD) & (positions[:, 1] < top + 0.15)
        # Does any other box reach into the region we searched?
        touching = []
        for other_index, (other, ox, oz, oc) in enumerate(boxes):
            if other_index == index:
                continue
            # Sample the other box's footprint and check whether any of it falls inside our grown region.
            gx, gz = np.meshgrid(np.linspace(-0.5, 0.5, 7) * other['width'], np.linspace(-0.5, 0.5, 7) * max(other['depth'], 0.02))
            footprint = oc + gx.reshape(-1, 1) * ox + gz.reshape(-1, 1) * oz
            fr = footprint - center
            if np.any((np.abs(fr @ axis_x) < o['width'] / 2 + PAD) & (np.abs(fr @ axis_z) < max(o['depth'], 0.02) / 2 + PAD)):
                touching.append(other['category'])
        row = {'n': index, 'category': o['category'], 'confidence': o['confidence'],
               'roomplan': (o['width'], max(o['depth'], 0.0), o['height']), 'touching': touching, 'points': int(near.sum())}
        if near.sum() >= MIN_POINTS:
            pa, pb, py = a[near], b[near], positions[near, 1]
            width = np.percentile(pa, 99) - np.percentile(pa, 1)
            depth = np.percentile(pb, 99) - np.percentile(pb, 1)
            height = np.percentile(py, 99) - (0.0 if center[1] - o['height'] / 2 < 0.15 else np.percentile(py, 1))
            row['lidar'] = (float(width), float(depth), float(height))
        rows.append(row)

    print(f'\n== {name}: {len(rows)} objects  (sizes in cm: width x depth x height; difference = LiDAR minus RoomPlan)')
    for row in rows:
        rw, rd, rh = (v * 100 for v in row['roomplan'])
        head = f"  #{row['n']:<2} {row['category']:<12} [{row['confidence']:<6}] RoomPlan {rw:5.0f} x {rd:4.0f} x {rh:4.0f}"
        if 'lidar' not in row:
            print(f"{head} | LiDAR: too few points ({row['points']})")
            continue
        lw, ld, lh = (v * 100 for v in row['lidar'])
        flag = f"  ! touches {', '.join(sorted(set(row['touching'])))}" if row['touching'] else ''
        print(f"{head} | LiDAR {lw:5.0f} x {ld:4.0f} x {lh:4.0f} | diff {lw - rw:+5.0f} {ld - rd:+5.0f} {lh - rh:+5.0f}{flag}")
    out = Path(sys.argv[2]).with_suffix('.objects.json')
    out.write_text(json.dumps(rows, indent=1))


if __name__ == '__main__':
    main()
