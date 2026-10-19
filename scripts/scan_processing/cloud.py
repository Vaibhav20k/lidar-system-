"""Turn a scan's raw LiDAR depth into a coloured point cloud the editor can show as "the room as scanned".

Usage: cloud.py <capture.json> <raw-bundle-dir> <room.json> <out.bin> [voxel-meters]

Every confident depth pixel becomes a 3D point, coloured from the photo taken at the same moment. Points
are merged on a voxel grid (1.5 cm by default) so a room fits in a few MB. Nothing is recognised or named:
beams, columns and odd corners appear because that is where the surfaces were.

Each point also gets a group so the viewer can fade or hide parts like it does for the drawn model:
0 = anything else, 1..250 = near the plane of wall N (1-based, matching the room's walls), 255 = ceiling.

Binary layout (little-endian): b"RFPC", uint32 version (1), uint32 count, float32 voxel size,
then count x 3 float32 positions (room coordinates: metres, Y up, floor at 0), count x 3 uint8 colours,
count x 1 uint8 groups.
"""
import json
import struct
import sys
from pathlib import Path

import cv2
import numpy as np
from observations import unproject, camera_normals

from elevation import Wall, matrix

NEAR_WALL = 0.2      # points this close to a wall plane belong to that wall
MAX_RANGE = 4.0      # LiDAR readings get noisy beyond this


def main():
    capture = json.loads(Path(sys.argv[1]).read_text())
    raw, room_path, out = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    voxel = float(sys.argv[5]) if len(sys.argv) > 5 else 0.015
    room = json.loads(room_path.read_text())
    walls = [Wall(w) for w in capture['walls']]
    floor_y = min(w.origin[1] - w.height / 2 for w in walls)
    xs = [p for w in room['walls'] for p in (w['start']['x'], w['end']['x'])]
    zs = [p for w in room['walls'] for p in (w['start']['z'], w['end']['z'])]
    low, high = np.array([min(xs) - 0.3, -0.3, min(zs) - 0.3]), np.array([max(xs) + 0.3, 6.0, max(zs) + 0.3])

    manifest = json.loads((raw / 'manifest.json').read_text())
    dw, dh = manifest['depthSize']
    u, v = np.meshgrid(np.arange(dw) + 0.5, np.arange(dh) + 0.5)
    sums: dict = {}
    keys_all, pos_all, col_all, down_all, frame_all = [], [], [], [], []
    for frame_id,line in enumerate((raw / 'frames.jsonl').read_text().splitlines()):
        if not line.strip():
            continue
        f = json.loads(line)
        depth_file, image_file = raw / 'depth' / f"{f['frame']}.bin", raw / 'frames' / f"{f['frame']}.jpg"
        if not f.get('depth') or not depth_file.exists() or not image_file.exists():
            continue
        depth = np.fromfile(depth_file, dtype='<f4').reshape(dh, dw)
        confidence = np.fromfile(raw / 'confidence' / f"{f['frame']}.bin", dtype=np.uint8).reshape(dh, dw)
        image = cv2.imread(str(image_file))
        k = np.array(f['intrinsics'], dtype=np.float64).reshape(3, 3).T
        scale = dw / f['imageWidth']
        camera = unproject(depth, f)
        normal = camera_normals(camera)
        # Only high-confidence readings: medium ones smear edges and add floating specks.
        keep = (confidence >= 2) & (depth > 0.3) & (depth < MAX_RANGE)
        pose = matrix(f['cameraToWorld'])
        world = camera[keep] @ pose[:3, :3].T + pose[:3, 3]
        facing_down = (normal[keep] @ pose[:3, :3].T)[:, 1] < -0.8
        # Colour from the photo pixel the depth pixel sits on (the photo is the depth map's 7.5x enlargement).
        h, w = image.shape[:2]
        px = np.clip((u[keep] / scale).astype(int), 0, w - 1); py = np.clip((v[keep] / (dh / f['imageHeight'])).astype(int), 0, h - 1)
        colour = image[py, px][:, ::-1]  # BGR to RGB
        world[:, 1] -= floor_y
        inside = np.all((world >= low) & (world <= high), axis=1)
        world, colour, facing_down = world[inside], colour[inside], facing_down[inside]
        cells = np.floor(world / voxel).astype(np.int64)
        # Exact voxel coordinates avoid hash collisions between unrelated faces.
        keys_all.append(cells)
        frame_all.append(np.full(len(cells),frame_id))
        pos_all.append(world); col_all.append(colour.astype(np.float64)); down_all.append(facing_down)
    if not keys_all:
        raise SystemExit('No usable depth frames in this bundle.')
    keys, pos, col, down = np.concatenate(keys_all), np.concatenate(pos_all), np.concatenate(col_all), np.concatenate(down_all)
    # Average everything that landed in the same voxel; a cell seen by only one reading is likely a speck.
    unique, index, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    position = np.zeros((len(unique), 3)); colour = np.zeros((len(unique), 3)); down_votes = np.zeros(len(unique))
    np.add.at(position, index, pos); np.add.at(colour, index, col); np.add.at(down_votes, index, down)
    position /= counts[:, None]; colour /= counts[:, None]; down_share = down_votes / counts
    support=np.unique(np.column_stack([index,np.concatenate(frame_all)]),axis=0)
    solid = np.bincount(support[:,0],minlength=len(unique)) >= 2
    position, colour, down_share = position[solid], colour[solid], down_share[solid]

    groups = np.zeros(len(position), np.uint8)
    top = (room.get('ceiling') or {}).get('height') or max(w['height'] for w in room['walls'])
    groups[(down_share > 0.5) & (position[:, 1] > 1.8)] = 255
    # Wall membership, in the room's wall order (room walls carry the capture's wall ids).
    order = {w['id']: i + 1 for i, w in enumerate(room['walls'])}
    best = np.full(len(position), NEAR_WALL)
    for wall in walls:
        number = order.get(wall.id)
        if not number:
            continue
        world = position.copy(); world[:, 1] += floor_y
        rel = world - wall.origin
        across, along, up = np.abs(rel @ wall.inward), np.abs(rel @ wall.axis_x), rel @ wall.axis_y
        mine = (across < best) & (along < wall.width / 2 + 0.1) & (np.abs(up) < wall.height / 2 + 0.1) & (groups != 255)
        groups[mine] = number; best[mine] = across[mine]

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'wb') as file:
        file.write(b'RFPC' + struct.pack('<IIf', 1, len(position), voxel))
        file.write(position.astype('<f4').tobytes()); file.write(np.clip(colour, 0, 255).astype(np.uint8).tobytes()); file.write(groups.tobytes())
    print(f"{room['name']}: {len(position):,} points at {voxel * 100:.1f} cm, {out.stat().st_size / 1e6:.1f} MB "
          f"({(groups == 255).sum():,} ceiling, {((groups > 0) & (groups < 255)).sum():,} on walls), ceiling at {top:.2f} m")


if __name__ == '__main__':
    main()
