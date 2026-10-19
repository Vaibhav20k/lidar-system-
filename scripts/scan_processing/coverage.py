"""Conservative observation coverage. Repeated frames are not new viewpoints.

Coverage describes supplied observed patches; no claim about unseen room space.
"""
import numpy as np


class CaptureSamplingPolicy:
    def __init__(self, primary=360, reserve=120):
        self.primary, self.maximum = primary, primary+reserve
        self.count, self.views = 0, {}

    def select(self, position, forward, timestamp):
        if self.count >= self.maximum:
            return False
        key = tuple(np.floor(np.r_[np.asarray(position)/.3, np.asarray(forward)/.25]).astype(int))
        count, last = self.views.get(key, (0, -np.inf))
        revisit = timestamp-last >= 20 and count > 0
        if count >= 3 and not revisit:
            return False
        if self.count >= self.primary and count > 0 and not revisit:
            return False
        self.views[key] = (count+1, timestamp)
        self.count += 1
        return True


def patch_coverage(points, frame_ids, camera_positions, cell=.1):
    points, frame_ids = np.asarray(points), np.asarray(frame_ids)
    if len(points) != len(frame_ids) or cell <= 0:
        raise ValueError('Invalid coverage input')
    groups = {}
    for point, frame in zip(points, frame_ids):
        key = tuple(np.floor(point/cell).astype(int))
        groups.setdefault(key, set()).add(int(frame))
    records = []
    for key, ids in groups.items():
        center = (np.array(key)+.5)*cell
        selected = []
        for frame in sorted(ids):
            if frame not in camera_positions:
                continue
            origin = np.asarray(camera_positions[frame], dtype=float)
            direction = center-origin
            if np.linalg.norm(direction) < .01:
                continue
            direction /= np.linalg.norm(direction)
            if all(np.linalg.norm(origin-o) >= .2 and np.dot(direction,d) <= np.cos(np.radians(15)) for o,d in selected):
                selected.append((origin, direction))
        records.append({'center':center.tolist(),'frames':sorted(ids),
                        'distinctViews':len(selected),'status':'repeat-observed' if len(selected)>=2 else 'partial'})
    return {'version':1,'cellMeters':cell,'patches':records,'unseenSpace':'unknown',
            'complete':False,'note':'View diversity is not a metric accuracy certificate.'}
