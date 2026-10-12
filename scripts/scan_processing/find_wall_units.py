"""Experimental: find wall-mounted boxes such as split AC units from LiDAR depth alone (no vision model).

Usage: find_wall_units.py <scan-dir> <scan-id> <out-dir> <tag>

For every wall it looks for flat faces 10-45 cm in front of the wall plane and reports each as a box.
A box is called AC-like when it is 0.6-1.3 m wide, 0.2-0.45 m tall, 12-35 cm proud of the wall and
at least 1.7 m above the floor. It judges shape only: a small high cabinet would match too.
Results are printed and a protrusion map per wall is written to <out-dir>; nothing is added to a room file yet.
"""
import json, sys
from pathlib import Path
import numpy as np, cv2
from elevation import matrix, Wall
D = Path(sys.argv[1]); sid = sys.argv[2]; out = Path(sys.argv[3]); tag = sys.argv[4]
capture = json.loads((D / f'{sid}.json').read_text()); raw = D / f'{sid}-raw'
walls = [Wall(w) for w in capture['walls']]
floor_y = min(w.origin[1] - w.height / 2 for w in walls)
man = json.loads((raw / 'manifest.json').read_text()); dw, dh = man['depthSize']
u, v = np.meshgrid(np.arange(dw) + 0.5, np.arange(dh) + 0.5)
P, Nrm = [], []
for line in (raw / 'frames.jsonl').read_text().splitlines():
    f = json.loads(line)
    depth = np.fromfile(raw / 'depth' / f"{f['frame']}.bin", dtype='<f4').reshape(dh, dw)
    conf = np.fromfile(raw / 'confidence' / f"{f['frame']}.bin", dtype=np.uint8).reshape(dh, dw)
    k = np.array(f['intrinsics']).reshape(3, 3).T; s = dw / f['imageWidth']
    cam = np.stack([(u - k[0, 2] * s) / (k[0, 0] * s) * depth, -(v - k[1, 2] * s) / (k[1, 1] * s) * depth, -depth], -1)
    n = np.cross(np.gradient(cam, axis=1), np.gradient(cam, axis=0)); n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-9)
    n[np.sum(n * cam, -1) > 0] *= -1
    pose = matrix(f['cameraToWorld']); ok = (conf >= 1) & (depth > 0.3) & (depth < 4.5)
    P.append((cam @ pose[:3, :3].T + pose[:3, 3])[ok]); Nrm.append((n @ pose[:3, :3].T)[ok])
P = np.concatenate(P); Nrm = np.concatenate(Nrm)
CELL = 0.025
for wi, wall in enumerate(walls, 1):
    if wall.width < 0.6: continue
    rel = P - wall.origin
    a, b, n = rel @ wall.axis_x, rel @ wall.axis_y, rel @ wall.inward
    facing = Nrm @ wall.inward
    # front faces of things mounted on this wall: parallel to it, 10-45 cm into the room
    ok = (np.abs(a) < wall.width / 2) & (np.abs(b) < wall.height / 2) & (n > 0.10) & (n < 0.45) & (facing > 0.8)
    cols, rows = int(wall.width / CELL), int(wall.height / CELL)
    ix = ((a[ok] + wall.width / 2) / CELL).astype(int).clip(0, cols - 1); iy = ((wall.height / 2 - b[ok]) / CELL).astype(int).clip(0, rows - 1)
    cnt = np.zeros((rows, cols), np.int32); tot = np.zeros((rows, cols)); np.add.at(cnt, (iy, ix), 1); np.add.at(tot, (iy, ix), n[ok])
    mask = (cnt >= 3).astype(np.uint8); prot = np.where(cnt > 0, tot / np.maximum(cnt, 1), 0)
    mask = cv2.morphologyEx(cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    num, lab, stats, _ = cv2.connectedComponentsWithStats(mask)
    img = cv2.applyColorMap((np.clip(prot / 0.45, 0, 1) * 255).astype(np.uint8), cv2.COLORMAP_TURBO); img[cnt == 0] = 0
    for i in range(1, num):
        x, y, w, h, area = stats[i]
        W, H = w * CELL, h * CELL; bottom = wall.height - (y + h) * CELL; top = wall.height - y * CELL
        d = float(np.median(prot[(lab == i) & (cnt > 0)])); fill = area / (w * h)
        if W < 0.3 or H < 0.12: continue
        verdict = 'AC-LIKE' if (0.6 <= W <= 1.3 and 0.2 <= H <= 0.45 and 0.12 <= d <= 0.35 and bottom >= 1.7 and fill > 0.6) else 'other'
        print(f'wall {wi} ({wall.width:.2f} m): box {W:.2f} x {H:.2f} m, sticks out {d:.2f} m, bottom {bottom:.2f} m, top {top:.2f} m, offset {x*CELL:.2f} m, fill {fill:.0%} -> {verdict}')
        cv2.rectangle(img, (x, y), (x + w, y + h), (255, 255, 255) if verdict == 'AC-LIKE' else (120, 120, 120), 2 if verdict == 'AC-LIKE' else 1)
    cv2.imwrite(str(out / f'ac_{tag}_wall{wi}.png'), cv2.resize(img, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST))
