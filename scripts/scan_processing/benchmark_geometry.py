"""Replay local scans; tape targets are used ONLY after estimation, for scoring.
Usage: benchmark_geometry.py scan-directory output-directory
"""
import argparse
import csv
import hashlib
import json
import platform
import subprocess
from pathlib import Path
import numpy as np
from geometry import refine

LEGACY = Path(__file__).resolve().parents[2] / 'verification/benchmark/legacy-geometry.json'


def hash_inputs(source, scan_id):
    """Hash all source bytes without loading large bundles into memory."""
    files = [source / f'{scan_id}.room.json', source / f'{scan_id}.json']
    files += sorted(p for p in (source / f'{scan_id}-raw').rglob('*') if p.is_file())
    result = hashlib.sha256()
    for path in files:
        result.update(path.relative_to(source).as_posix().encode()); result.update(b'\0')
        file_hash = hashlib.sha256()
        with path.open('rb') as handle:
            for block in iter(lambda: handle.read(1024*1024), b''):
                file_hash.update(block)
        result.update(file_hash.digest())
    return result.hexdigest()


def dimensions(room):
    w = max(room['walls'], key=lambda w: np.hypot(w['end']['x']-w['start']['x'], w['end']['z']-w['start']['z']))
    axis = np.array([w['end']['x']-w['start']['x'], w['end']['z']-w['start']['z']], dtype=float)
    if np.linalg.norm(axis) == 0:
        raise ValueError('Cannot score a zero-length reference axis')
    axis /= np.linalg.norm(axis)
    outline = np.array([[p['x'], p['z']] for p in room['floor']])
    return np.array([np.ptp(outline @ axis), np.ptp(outline @ np.array([-axis[1], axis[0]])), room['ceiling']['height']])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path); parser.add_argument('output', type=Path)
    parser.add_argument('--manifest', type=Path, default=LEGACY)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    if manifest.get('version') != 1 or not manifest.get('cases'):
        raise ValueError('Expected nonempty geometry manifest v1')
    cases = manifest['cases']
    ids = [c['id'] for c in cases]
    if len(set(ids)) != len(ids) or any(not s or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for ch in s) for s in ids):
        raise ValueError('Invalid or duplicate scan IDs')
    if output == source or source in output.parents or output in source.parents:
        raise ValueError('Output must be separate from the scan source')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a fresh output directory')
    output.mkdir(parents=True, exist_ok=True)
    reports, rows = [], []
    for case in cases:
        scan_id, time, group = case['id'], case['label'], case['group']
        input_hash = hash_inputs(source, scan_id)
        before = json.loads((source / f'{scan_id}.room.json').read_text())
        after, qa = refine(json.loads((source / f'{scan_id}.json').read_text()), source / f'{scan_id}-raw', before)
        (output / f'{scan_id}.room.json').write_text(json.dumps(after, indent=2, allow_nan=False))
        (output / f'{scan_id}.quality.json').write_text(json.dumps(qa, indent=2, allow_nan=False))
        # Reference targets are loaded only after the estimator writes its result.
        reference = json.loads((args.manifest.parent / case['reference']).read_text(encoding='utf-8'))
        target = [reference[name] for name in ('length', 'width', 'height')]
        b, a = dimensions(before), dimensions(after)
        report = {'id': scan_id, 'time': time, 'group': group, 'inputSha256': input_hash,
                  'referenceSha256': hashlib.sha256((args.manifest.parent / case['reference']).read_bytes()).hexdigest(),
                  'dimensions': {}, 'quality': after['measurementQuality']}
        for name, bm, am, tm in zip(['length', 'width', 'height'], b, a, target):
            report['dimensions'][name] = {'target_m': tm, 'before_m': bm, 'after_m': am,
                'before_error_cm': (bm-tm)*100, 'after_error_cm': (am-tm)*100, 'within_2cm': bool(abs(am-tm) <= .02)}
            rows.append({'scan': time, 'group': group, 'dimension': name, **report['dimensions'][name]})
        reports.append(report)
        print(time, json.dumps(report['dimensions']), flush=True)
    groups = sorted({s['group'] for s in reports})
    repeats = {group: {name: float(np.ptp([s['dimensions'][name]['after_m'] for s in reports if s['group']==group])*100)
                      if sum(s['group']==group for s in reports) >= 2 else None
                      for name in ['length','width','height']} for group in groups}
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=Path(__file__).parent, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    summary = {'scans': reports, 'accepted_scans': sum(s['quality']['status'] == 'consistent' for s in reports),
        'commit': commit, 'runtime': {'python': platform.python_version(), 'numpy': np.__version__},
        'manifestSha256': hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        'note': 'Consistent does not certify absolute accuracy. Rejected corrections leave original dimensions unchanged. Beam reference 3.235 m excluded because its datum is unresolved.',
        'repeat_spread_cm': repeats.get('same-room'), 'repeat_spread_by_group_cm': repeats}
    (output / 'before-after.json').write_text(json.dumps(summary, indent=2, allow_nan=False))
    with (output / 'before-after.csv').open('w') as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)


if __name__ == '__main__':
    main()
