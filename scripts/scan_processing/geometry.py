"""Offline, fail-closed geometry refinement. No network or AI services.

Usage: geometry.py capture.json raw-dir room.json out.json [--report report.json]
Targets/tape measurements are deliberately not accepted by this estimator.
"""
import argparse
import copy
import json
from pathlib import Path
import cv2
import numpy as np
from elevation import Wall, depth_points
from quality import robust_plane
from registration import register
from refit_walls import fit_wall, cross, MAX_CORNER_MOVE

VERSION = 'geometry-qa-v1'


def tracking_summary(raw):
    """Separate startup from capture failures and mapping from tracking state."""
    telemetry, index = raw / 'tracking.jsonl', raw / 'frames.jsonl'
    if not telemetry.exists() or not index.exists():
        return {'available': False}
    records = [json.loads(line) for line in telemetry.read_text().splitlines() if line.strip()]
    frames = [json.loads(line) for line in index.read_text().splitlines() if line.strip()]
    if not frames:
        return {'available': True, 'samples': 0, 'emptyCapture': True}
    start, end = frames[0]['timestamp'], frames[-1]['timestamp']
    active = [r for r in records if start <= r['timestamp'] <= end]
    def weak_mapping(r):
        return r.get('mappingState') in ('limited', 'unavailable') or r.get('worldMapping') in (
            'ARWorldMappingStatus(rawValue: 0)', 'ARWorldMappingStatus(rawValue: 1)')
    return {'available': True, 'samples': len(active),
            'startupLimitedSamples': sum(r['timestamp'] < start and r.get('tracking') != 'normal' for r in records),
            'limitedOrGapSamples': sum(r.get('tracking') != 'normal' or r.get('sampleGapSeconds', 0) > 2 for r in active),
            'recordedFrames': len(frames), 'weakMappingFrames': sum(weak_mapping(r) for r in frames),
            'recordingLimitReached': any(r.get('recordingLimitReached', False) for r in records),
            'unrecordedTailSeconds': max(0., records[-1]['timestamp'] - end) if records else 0.}


def inside(points, outline):
    # Raster mask avoids a Python loop over millions of depth samples.
    origin = outline.min(axis=0) - .1
    size = np.ceil((outline.max(axis=0) - origin + .1) / .05).astype(int)
    mask = np.zeros((size[1], size[0]), np.uint8)
    cv2.fillPoly(mask, [np.round((outline - origin) / .05).astype(np.int32)], 1)
    ij = np.floor((points[:, [0, 2]] - origin) / .05).astype(int)
    valid = (ij[:, 0] >= 0) & (ij[:, 1] >= 0) & (ij[:, 0] < size[0]) & (ij[:, 1] < size[1])
    valid[valid] &= mask[ij[valid, 1], ij[valid, 0]].astype(bool)
    return valid


def surface(points, normals, frames, outline, height, upward):
    # Broad window is intentional: do not hide alternate depth layers before QA.
    near = (abs(points[:, 1] - height) < .20) & inside(points, outline)
    near &= normals[:, 1] > .95 if upward else normals[:, 1] < -.95
    fit = robust_plane(points[near], frames[near], horizontal=True)
    if 'coefficients' not in fit:
        return fit
    a, b, c = fit['coefficients']
    centre = np.mean(outline, axis=0)
    fit['heightAtCentre'] = float(np.dot([a, b], centre - fit['origin']) + c)
    # A single horizontal level cannot represent appreciable slope or a step.
    heights = (outline - fit['origin']) @ np.array([a, b]) + c
    fit['levelVariation'] = float(np.ptp(heights))
    span = np.ptp(outline, axis=0)
    coverage = np.minimum(1, np.array(fit['span']) / np.maximum(span, .01))
    fit['spanCoverage'] = coverage.tolist()
    if min(coverage) < .5 or fit['levelVariation'] > .02:
        fit.update(passed=False, reason='insufficient coverage or surface is not level within 2 cm')
    return fit


def shift_datum(room, delta):
    for element in room.get('structures',[]):
        element['origin']['y']-=delta
        element['quality']={'absoluteAccuracyVerified':False,'status':'unverified'}
    for wall in room['walls']:
        wall['height'] -= delta
    for opening in room.get('openings', []):
        top = opening['sill'] + opening['height'] - delta
        opening['sill'] = max(0., opening['sill'] - delta)
        opening['height'] = max(.1, top - opening['sill'])
    for fixture in room.get('fixtures', []):
        fixture['sill'] = max(0., fixture['sill'] - delta)
    for obj in room.get('objects', []):
        obj['position']['y'] -= delta
    ceiling = room.get('ceiling')
    if ceiling:
        ceiling['height'] -= delta
        for region in ceiling.get('regions', []):
            region['height'] -= delta
        for item in ceiling.get('items', []):
            item['height'] -= delta


def refine(capture, raw, source):
    room = copy.deepcopy(source)
    walls = {w['id']: Wall(w) for w in capture['walls']}
    floor_y = min(w.origin[1] - w.height / 2 for w in walls.values())
    outline = np.array([[p['x'], p['z']] for p in room['floor']])
    if len(outline) < 3:
        raise ValueError('Floor outline is required')
    # Refuse edited/rescaled/previously-refined input rather than applying twice.
    for w in room['walls']:
        raw_wall = walls[w['id']]
        start = raw_wall.origin - raw_wall.axis_x * raw_wall.width / 2
        end = raw_wall.origin + raw_wall.axis_x * raw_wall.width / 2
        for name, point in [('start', start), ('end', end)]:
            if np.linalg.norm(np.array([w[name]['x'], w[name]['z']]) - point[[0, 2]]) > .002:
                raise ValueError('Use the original unedited room for refinement')
        if abs(w.get('baseY', 0)) > .0001 or abs(w['height'] - raw_wall.height) > .002:
            raise ValueError('Use the original height datum for refinement')
    points, normals, frames = depth_points(raw, with_frames=True, min_confidence=2)
    report = {'version': VERSION, 'targetMeters': .02, 'inputPoints': len(points), 'walls': {}, 'applied': []}
    points, normals, report['registration'] = register(points, normals, frames, walls, floor_y)
    lines = {}
    for w in room['walls']:
        fitted, reason = fit_wall(walls[w['id']], points, normals, frames, floor_y,
                                 [o for o in room['openings'] if o['wallId'] == w['id']])
        report['walls'][w['id']] = {'passed': fitted is not None, 'reason': reason, 'width': walls[w['id']].width}
        if fitted:
            lines[w['id']] = fitted[:2]
            report['walls'][w['id']].update(fitted[2])
    # Atomic footprint update: never intersect one trusted plane with an untrusted
    # one, or slide disconnected endpoints independently.
    ends = {}
    for w in room['walls']:
        for which in ('start', 'end'):
            ends[(w['id'], which)] = np.array([w[which]['x'], w[which]['z']])
    revised = {}
    for key, old in ends.items():
        partners = [k for k, p in ends.items() if k[0] != key[0] and np.linalg.norm(p - old) < .03]
        if len(partners) != 1 or key[0] not in lines or partners[0][0] not in lines:
            continue
        corner = cross(*lines[key[0]], *lines[partners[0][0]])
        if corner is not None and np.linalg.norm(corner - old) <= MAX_CORNER_MOVE:
            revised[key] = corner
    if len(revised) == len(ends):
        original = copy.deepcopy(room['walls'])
        for w in room['walls']:
            for which in ('start', 'end'):
                x, z = revised[(w['id'], which)]
                w[which] = {'x': float(x), 'z': float(z)}
        for point in room['floor']:
            xy = np.array([point['x'], point['z']])
            key = min(ends, key=lambda k: np.linalg.norm(ends[k] - xy))
            if np.linalg.norm(ends[key] - xy) < .03:
                point.update(x=float(revised[key][0]), z=float(revised[key][1]))
        for item in room['openings'] + room.get('fixtures', []):
            before = next(w for w in original if w['id'] == item['wallId'])
            s, e = ends[(before['id'], 'start')], ends[(before['id'], 'end')]
            anchor = s + (e-s) / np.linalg.norm(e-s) * item['offset']
            s, e = revised[(before['id'], 'start')], revised[(before['id'], 'end')]
            length = float(np.linalg.norm(e-s))
            item['width'] = min(item['width'], length)
            item['offset'] = float(np.clip((anchor-s) @ ((e-s)/length), 0, length-item['width']))
        report['applied'].append('walls')
    floor = surface(points, normals, frames, outline, floor_y, True)
    report['floor'] = floor
    ceiling = room.get('ceiling')
    upper = surface(points, normals, frames, outline, floor_y + ceiling['height'], False) if ceiling else {'passed': False, 'reason': 'no ceiling reference'}
    report['ceiling'] = upper
    # Joint datum update only: a trustworthy floor cannot make an unsupported
    # ceiling accurate. Keep original heights if either surface is ambiguous.
    if floor['passed'] and upper['passed']:
        delta = floor['heightAtCentre'] - floor_y
        shift_datum(room, delta)
        room['ceiling']['height'] = upper['heightAtCentre'] - floor['heightAtCentre']
        report['applied'].append('floor-ceiling')
    failed_walls = sum(not w['passed'] for w in report['walls'].values())
    reasons = []
    if 'walls' not in report['applied']:
        reasons.append(f'{failed_walls} wall fits unsupported; footprint correction withheld')
    for name in ['floor', 'ceiling']:
        if not report[name]['passed']:
            reasons.append(f"{name}: {report[name]['reason']}")
    report['tracking'] = tracking_summary(raw)
    tracking = report['tracking']
    if not tracking['available']:
        reasons.append('Tracking history unavailable in this capture')
    else:
        if not tracking['samples'] or tracking['limitedOrGapSamples']:
            reasons.append('Tracking interruptions/gaps require review')
        if tracking.get('weakMappingFrames', 0):
            reasons.append(f"Weak mapping in {tracking['weakMappingFrames']}/{tracking['recordedFrames']} recorded frames; normal tracking does not certify metric accuracy")
        if tracking.get('recordingLimitReached') and tracking.get('unrecordedTailSeconds', 0) > 2:
            reasons.append('Raw depth cap reached before scan end; final revisit coverage may be incomplete')
    reasons.append('Beam levels and absolute accuracy require independent measurement')
    room['measurementQuality'] = {'version': VERSION, 'status': 'needs-rescan' if len(reasons) > 1 else 'consistent',
                                  'targetMeters': .02, 'reasons': reasons, 'applied': report['applied']}
    report['quality'] = room['measurementQuality']
    from structural_candidates import columns,beams,VERSION as STRUCTURE_VERSION
    local_points=points.copy()
    datum=floor['heightAtCentre'] if 'floor-ceiling' in report['applied'] else floor_y
    local_points[:,1]-=datum
    # Keep segmentation bounded to this room; use original observations for
    # profile proposals and retain their frame membership.
    selected=inside(local_points,outline)
    candidates=columns(local_points[selected],normals[selected],frames[selected],room['walls'],room.get('captureId'))
    candidates+=beams(local_points[selected],normals[selected],frames[selected],(room.get('ceiling') or {}).get('height'),room.get('captureId'))
    retained=[e for e in room.get('structures',[]) if e['provenance']['processingVersion']!=STRUCTURE_VERSION or e['review']!='candidate']
    existing={e['id'] for e in retained}
    candidates=[e for e in candidates if e['id'] not in existing]
    room['structures']=(retained+candidates)[:256]
    report['structures']={'candidates':len(candidates),'retained':len(retained),'omittedByLimit':max(0,len(retained)+len(candidates)-256),
                          'complete':False,'absoluteAccuracyVerified':False}
    room['schemaVersion']=2
    room['warnings'] = [s for s in room.get('warnings', []) if not s.startswith('2 cm check:')][:49] + [
        '2 cm check: accuracy is not verified. ' + '; '.join(reasons)[:420]]
    return room, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['capture', 'raw', 'room', 'output']:
        parser.add_argument(name, type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    room, report = refine(json.loads(args.capture.read_text()), args.raw, json.loads(args.room.read_text()))
    args.output.write_text(json.dumps(room, indent=2, allow_nan=False))
    if args.report:
        args.report.write_text(json.dumps(report, indent=2, allow_nan=False))
    print(json.dumps(room['measurementQuality']))


if __name__ == '__main__':
    main()
