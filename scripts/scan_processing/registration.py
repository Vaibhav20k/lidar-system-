"""Conservative rotation/translation segment registration against shared static patches.

Scale is never changed. All segments must connect to the reference,
three independent plane normals must constrain each edge, and entire spatial
patches are held out. Failed registration leaves raw points untouched.
"""
import numpy as np


def solve_edge(normals, deltas, patch_ids):
    normals, deltas, patch_ids = map(np.asarray, (normals, deltas, patch_ids))
    train = patch_ids % 3 != 0
    if train.sum() < 12 or (~train).sum() < 6:
        return {'passed': False, 'reason': 'insufficient shared patches'}
    singular = np.linalg.svd(normals[train], compute_uv=False)
    if len(singular) < 3 or singular[-1] < .2 * singular[0]:
        return {'passed': False, 'reason': 'translation is underconstrained'}
    weights = np.ones(train.sum())
    for _ in range(8):
        shift = np.linalg.lstsq(normals[train]*weights[:,None], deltas[train]*weights, rcond=None)[0]
        error = deltas[train] - normals[train] @ shift
        weights = np.sqrt(np.minimum(1., .01/np.maximum(abs(error), 1e-9)))
    held_before = float(np.percentile(abs(deltas[~train]),90))
    held_after = float(np.percentile(abs(deltas[~train]-normals[~train]@shift),90))
    passed = np.linalg.norm(shift) <= .20 and held_after <= .01 and (held_before <= .01 or held_after <= .5 * held_before)
    return {'passed': bool(passed), 'reason': 'consistent' if passed else 'held-out registration failed',
            'translation': shift.tolist(), 'heldoutBefore': held_before, 'heldoutAfter': held_after}


def solve_rigid_edge(source, target, normals, patch_ids):
    """Fit a small rigid motion B -> A using point-to-plane patch constraints.

    Patches, not raw points, are independent votes. One third of spatial
    patches never enters fitting. Rank checks prohibit a blank single plane
    from inventing tangential movement. No tape dimensions enter this fit.
    """
    import cv2
    source, target, normals = [np.asarray(v, float) for v in (source, target, normals)]
    train = np.asarray(patch_ids) % 3 != 0
    result = {'passed': False, 'sharedPatches': len(source), 'trainPatches': int(train.sum()),
              'heldoutPatches': int((~train).sum()), 'model': 'rigid-6dof'}
    if train.sum() < 18 or (~train).sum() < 9:
        return {**result, 'reason': 'insufficient shared patches'}
    centre = np.mean(source[train], axis=0)
    s, t = source - centre, target - centre
    transform = np.eye(4)
    for _ in range(8):
        moved = s @ transform[:3, :3].T + transform[:3, 3]
        residual = np.sum((moved - t) * normals, axis=1)
        jacobian = np.column_stack([normals, np.cross(moved, normals)])
        # Normalize columns for conditioning; a large room is not better
        # constrained merely because its rotational columns have larger units.
        scale = np.linalg.norm(jacobian[train], axis=0)
        if np.any(scale < 1e-8):
            return {**result, 'reason': 'rigid motion is underconstrained'}
        singular = np.linalg.svd(jacobian[train] / scale, compute_uv=False)
        result['observabilityRatio'] = float(singular[-1] / singular[0])
        if singular[-1] < .02 * singular[0]:
            return {**result, 'reason': 'rigid motion is underconstrained'}
        weights = np.sqrt(np.minimum(1., .01 / np.maximum(abs(residual[train]), 1e-9)))
        step = np.linalg.lstsq(jacobian[train] * weights[:, None], -residual[train] * weights, rcond=None)[0]
        update = np.eye(4); update[:3, :3] = cv2.Rodrigues(step[3:])[0]; update[:3, 3] = step[:3]
        transform = update @ transform
        if np.linalg.norm(step) < 1e-8:
            break
    after = s @ transform[:3, :3].T + transform[:3, 3]
    before_error = abs(np.sum((s - t) * normals, axis=1))
    after_error = abs(np.sum((after - t) * normals, axis=1))
    before, error = [float(np.percentile(x[~train], 90)) for x in (before_error, after_error)]
    angle = float(np.linalg.norm(cv2.Rodrigues(transform[:3, :3])[0]))
    displacement = float(np.max(np.linalg.norm(after - s, axis=1)))
    passed = displacement <= .20 and angle <= np.radians(3) and error <= .01 and (before <= .01 or error <= .5 * before)
    transform[:3, 3] += centre - transform[:3, :3] @ centre
    return {**result, 'passed': bool(passed), 'reason': 'consistent' if passed else 'held-out rigid registration failed',
            'transform': transform.tolist(), 'rotationDegrees': float(np.degrees(angle)),
            'maxDisplacement': displacement, 'heldoutBefore': before, 'heldoutAfter': error}


def register(points, normals, frames, walls, floor_y):
    if not len(frames):
        return points, normals, {'passed': False, 'reason': 'no frames'}
    segment = frames // 12
    segments = np.unique(segment)
    if len(segments) < 2:
        return points, normals, {'passed': False, 'reason': 'too few scan segments'}
    patches = []
    # Use raw per-frame medians, never a selected depth mode.
    for wall in walls.values():
        rel = points-wall.origin
        along = rel @ wall.axis_x + wall.width/2
        height = points[:,1]-floor_y
        offset = rel @ wall.inward
        use = (abs(offset)<.30)&(along>0)&(along<wall.width)&(height>.3)&(height<1.8)&(normals@wall.inward>.95)
        collect(patches, np.column_stack([along[use],height[use]]), offset[use], frames[use], wall.inward, points[use])
    use = (abs(points[:,1]-floor_y)<.20)&(normals[:,1]>.95)
    collect(patches, points[use][:,[0,2]], points[use,1]-floor_y, frames[use], np.array([0.,1.,0.]), points[use])
    edges = []
    for i, a in enumerate(segments):
        for b in segments[i+1:]:
            shared = [(pid,n,obs[b],obs[a]) for pid,(n,obs) in enumerate(patches) if a in obs and b in obs]
            if not shared:
                continue
            pid, n, source, target = zip(*shared)
            check = solve_rigid_edge(source, target, n, pid)
            edges.append({'from': int(a),'to':int(b), **check})
    # All-pairs validation; edges that disagree cannot be bypassed by a convenient path.
    good = [e for e in edges if e['passed']]
    reached = {int(segments[0])}; transforms = {int(segments[0]): np.eye(4)}
    for _ in segments:
        for edge in good:
            a, b = edge['from'], edge['to']; move = np.array(edge['transform'])
            if a in reached and b not in reached:
                reached.add(b); transforms[b] = transforms[a] @ move
            elif b in reached and a not in reached:
                reached.add(a); transforms[a] = transforms[b] @ np.linalg.inv(move)
    result = {'passed': False, 'model': 'rigid-6dof', 'segments': len(segments), 'connected': len(reached), 'edges': edges}
    if len(reached) != len(segments):
        result['reason'] = 'not all segments have observable rigid overlap; correction withheld'
        return points, normals, result
    # Test closure as displacement at the room's bounding corners, so rotation
    # cannot pass just because translations happen to agree at the origin.
    low, high = np.percentile(points, [1, 99], axis=0)
    probes = np.array([[x,y,z,1.] for x in [low[0],high[0]] for y in [low[1],high[1]] for z in [low[2],high[2]]])
    closure = max((np.max(np.linalg.norm((probes @ (transforms[e['to']] - transforms[e['from']] @ np.array(e['transform'])).T)[:,:3], axis=1)) for e in good), default=0.)
    accumulated = max(np.max(np.linalg.norm((probes @ tr.T)[:,:3] - probes[:,:3], axis=1)) for tr in transforms.values())
    if closure > .01 or accumulated > .20 or any(not e['passed'] and 'transform' in e for e in edges):
        result['reason'] = 'loop closure or held-out edges disagree; correction withheld'
        return points, normals, result
    corrected, rotated = points.copy(), normals.copy()
    for seg, tr in transforms.items():
        use = segment == seg
        corrected[use] = points[use] @ tr[:3,:3].T + tr[:3,3]
        rotated[use] = normals[use] @ tr[:3,:3].T
    result.update(passed=True, reason='rigid registration validated', loopClosure=float(closure),
                  transforms={str(k):v.tolist() for k,v in transforms.items()})
    return corrected, rotated, result


def collect(patches, coordinates, offsets, frames, normal, positions):
    if not len(frames):
        return
    keys = np.floor(coordinates/.5).astype(int)
    _, inverse = np.unique(keys,axis=0,return_inverse=True)
    order=np.argsort(inverse)
    for _,start,count in zip(*np.unique(inverse[order],return_index=True,return_counts=True)):
        idx=order[start:start+count]
        values,ids=offsets[idx],frames[idx]
        observations={int(f):float(np.median(values[ids==f])) for f in np.unique(ids) if np.sum(ids==f)>=20}
        segment_obs={}
        for seg in {f//12 for f in observations}:
            v=[v for f,v in observations.items() if f//12==seg]
            if len(v)>=3 and np.ptp(v)<=.02:
                segment_obs[seg] = np.median([np.median(positions[idx[ids == f]], axis=0)
                                             for f in observations if f//12 == seg], axis=0)
        if len(segment_obs)>=2:
            patches.append((normal,segment_obs))
