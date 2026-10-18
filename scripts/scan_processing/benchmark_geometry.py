"""Replay local scans; tape targets are used ONLY after estimation, for scoring.
Usage: benchmark_geometry.py scan-directory output-directory
"""
import csv
import json
import sys
from pathlib import Path
import numpy as np
from geometry import refine

CASES = [
    ('E5E54DAD-2A4D-4718-ADCB-8C4A7A22A6F7', '15:39', 'same-room', (6.423, 4.570, 3.226)),
    ('045BFE29-E2F5-47E0-B0AE-81CCF669CC5E', '16:48', 'same-room', (6.423, 4.570, 3.226)),
    ('03D3CDA8-BB44-4C2F-8778-C031E0D5590B', '16:50', 'same-room', (6.423, 4.570, 3.226)),
    ('80F17D3B-8897-4652-9969-9C85580A612B', '15:28', 'other-room', (4.939, 4.295, 3.230)),
]


def dimensions(room):
    w = max(room['walls'], key=lambda w: np.hypot(w['end']['x']-w['start']['x'], w['end']['z']-w['start']['z']))
    axis = np.array([w['end']['x']-w['start']['x'], w['end']['z']-w['start']['z']]); axis /= np.linalg.norm(axis)
    outline = np.array([[p['x'], p['z']] for p in room['floor']])
    return np.array([np.ptp(outline @ axis), np.ptp(outline @ np.array([-axis[1], axis[0]])), room['ceiling']['height']])


def main():
    source, output = map(Path, sys.argv[1:3]); output.mkdir(parents=True, exist_ok=True)
    reports, rows = [], []
    for scan_id, time, group, target in CASES:
        before = json.loads((source / f'{scan_id}.room.json').read_text())
        after, qa = refine(json.loads((source / f'{scan_id}.json').read_text()), source / f'{scan_id}-raw', before)
        (output / f'{scan_id}.room.json').write_text(json.dumps(after, indent=2, allow_nan=False))
        (output / f'{scan_id}.quality.json').write_text(json.dumps(qa, indent=2, allow_nan=False))
        b, a = dimensions(before), dimensions(after)
        report = {'id': scan_id, 'time': time, 'group': group, 'dimensions': {}, 'quality': after['measurementQuality']}
        for name, bm, am, tm in zip(['length', 'width', 'height'], b, a, target):
            report['dimensions'][name] = {'target_m': tm, 'before_m': bm, 'after_m': am,
                'before_error_cm': (bm-tm)*100, 'after_error_cm': (am-tm)*100, 'within_2cm': bool(abs(am-tm) <= .02)}
            rows.append({'scan': time, 'group': group, 'dimension': name, **report['dimensions'][name]})
        reports.append(report)
        print(time, json.dumps(report['dimensions']), flush=True)
    summary = {'scans': reports, 'accepted_scans': sum(s['quality']['status'] == 'consistent' for s in reports),
        'note': 'Consistent does not certify absolute accuracy. Rejected corrections leave original dimensions unchanged. Beam reference 3.235 m excluded because its datum is unresolved.',
        'repeat_spread_cm': {name: float(np.ptp([s['dimensions'][name]['after_m'] for s in reports if s['group']=='same-room'])*100) for name in ['length','width','height']}}
    (output / 'before-after.json').write_text(json.dumps(summary, indent=2, allow_nan=False))
    with (output / 'before-after.csv').open('w') as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)


if __name__ == '__main__':
    main()
