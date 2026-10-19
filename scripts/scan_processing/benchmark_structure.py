"""Score previously written structural observations; never runs an estimator.

Usage: python benchmark_structure.py manifest.json output-directory
The interchange format is benchmark-only; it is not the future room schema.
"""
import argparse
import copy
import csv
import hashlib
import json
import platform
import subprocess
from pathlib import Path
import numpy as np
from metrics import dimension_score, error_summary, finite, match_instances, rigid_alignment


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_observation(document, reference=False):
    if document.get('version') != 1 or document.get('units') != 'meters':
        raise ValueError('Expected benchmark observation v1 in meters')
    elements = document.get('elements')
    if not isinstance(elements, list) or len(elements) > 256:
        raise ValueError('Expected at most 256 elements')
    ids = set()
    for element in elements:
        if not isinstance(element.get('id'), str) or not element['id'] or element['id'] in ids:
            raise ValueError('Element IDs must be unique nonempty strings')
        ids.add(element['id'])
        if element.get('kind') not in ('beam', 'column', 'soffit', 'unknown-structural'):
            raise ValueError('Invalid structural kind')
        if len(element.get('center', [])) != 3:
            raise ValueError('Expected 3D center')
        for v in element['center']:
            finite(v)
        if not isinstance(element.get('dimensions'), dict):
            raise ValueError('Expected named dimensions')
        for value in element['dimensions'].values():
            if finite(value) <= 0:
                raise ValueError('Dimensions must be positive')
        if reference:
            if not isinstance(element.get('observable'), bool):
                raise ValueError('References require observable flag')
            if finite(element.get('uncertaintyMeters', 0)) < 0:
                raise ValueError('Uncertainty cannot be negative')
    return document


def score_case(prediction, reference, config):
    prediction = copy.deepcopy(validate_observation(prediction))
    validate_observation(reference, reference=True)
    tolerance = finite(config['toleranceMeters'])
    if tolerance <= 0:
        raise ValueError('Tolerance must be positive')
    alignment = {'mode': 'shared-frame', 'controlMaxResidualMeters': None}
    controls = config.get('controls')
    if controls:
        rotation, translation, residual = rigid_alignment(controls['prediction'], controls['reference'])
        if residual > finite(controls['maxResidualMeters']):
            raise ValueError('Control alignment exceeds the frozen residual limit')
        for p in prediction['elements']:
            p['center'] = (rotation @ p['center'] + translation).tolist()
        alignment = {'mode': 'rigid-controls-no-scale', 'rotation': rotation.tolist(),
                     'translation': translation.tolist(), 'controlMaxResidualMeters': residual}
    elif config.get('sharedFrame') is not True:
        raise ValueError('Declare a shared frame or provide independent controls')
    refs = [r for r in reference['elements'] if r['observable']]
    preds = prediction['elements']
    pairs, ambiguity = match_instances(preds, refs, config['matchDistanceMeters'])
    rows, dimension_scores, position_scores = [], [], []
    paired_refs = {j: (i, distance) for i, j, distance in pairs}
    for j, ref in enumerate(refs):
        pair = paired_refs.get(j)
        pred = preds[pair[0]] if pair else None
        dimensions = {name: dimension_score(pred['dimensions'].get(name) if pred else None,
                                             target, ref.get('uncertaintyMeters', 0), tolerance)
                      for name, target in ref['dimensions'].items()}
        dimension_scores.extend(dimensions.values())
        position = dimension_score(pair[1] if pair else None, 0,
                                   ref.get('uncertaintyMeters', 0), tolerance)
        position_scores.append(position)
        rows.append({'referenceId': ref['id'], 'predictionId': pred['id'] if pred else None,
                     'referenceKind': ref['kind'], 'predictionKind': pred['kind'] if pred else None,
                     'classCorrect': bool(pred and pred['kind'] == ref['kind']),
                     'position': position, 'dimensions': dimensions})
    used = {i for i, _, _ in pairs}
    extras = [p['id'] for i, p in enumerate(preds) if i not in used]
    correct = sum(row['classCorrect'] for row in rows)
    geometry = error_summary(dimension_scores)
    positions = error_summary(position_scores)
    return {'alignment': alignment, 'referenceCount': len(refs), 'predictionCount': len(preds),
            'excludedUnobservableReferences': [r['id'] for r in reference['elements'] if not r['observable']],
            'matched': len(pairs), 'missing': [r['referenceId'] for r in rows if r['predictionId'] is None],
            'extras': extras, 'classConfusions': [r['referenceId'] for r in rows
                                                if r['predictionId'] and not r['classCorrect']],
            'instanceRecall': len(pairs)/len(refs) if refs else None,
            'instancePrecision': len(pairs)/len(preds) if preds else None,
            'typedRecall': correct/len(refs) if refs else None,
            'associationAmbiguity': ambiguity, 'elements': rows,
            'dimensionSummary': geometry, 'positionSummary': positions,
            'basicChecksPassed': len(pairs) == len(refs) and not extras and correct == len(refs)
                and (geometry['allPassed'] if refs else True)
                and (positions['allPassed'] if refs else True)
                and not any(ambiguity.values()),
            'shapeStatus': 'not-scored', 'completenessStatus': 'not-scored',
            'releaseQualified': False}


def run(manifest_path, output):
    manifest_path, output = Path(manifest_path).resolve(), Path(output).resolve()
    manifest = read_json(manifest_path)
    if manifest.get('version') != 1 or not manifest.get('cases'):
        raise ValueError('Expected nonempty benchmark manifest v1')
    if not isinstance(manifest['cases'], list) or not isinstance(manifest.get('scoring'), dict):
        raise ValueError('Manifest needs cases and scoring configuration')
    for name in ('toleranceMeters', 'matchDistanceMeters'):
        if finite(manifest['scoring'].get(name)) <= 0:
            raise ValueError('Scoring thresholds must be positive')
    cases, ids, partitions, input_paths, reference_values = [], set(), {}, {manifest_path}, {}
    # Load and validate all inputs before creating output; never overwrite evidence.
    for case in manifest['cases']:
        for key in ('id', 'physicalRoomId', 'prediction', 'reference'):
            if not isinstance(case.get(key), str) or not case[key].strip():
                raise ValueError(f'Case requires nonempty {key}')
        if ('controls' in case) == ('sharedFrame' in case):
            raise ValueError('Choose exactly one of sharedFrame or controls')
        case_id = case['id']
        if case_id in ids:
            raise ValueError('Duplicate case ID')
        ids.add(case_id)
        if case['partition'] not in ('development', 'tuning', 'holdout'):
            raise ValueError('Unknown partition')
        room = case['physicalRoomId']
        if room in partitions and partitions[room] != case['partition']:
            raise ValueError('Physical room leaked across partitions')
        partitions[room] = case['partition']
        paths = {key: (manifest_path.parent / case[key]).resolve() for key in ('prediction', 'reference')}
        input_paths.update(paths.values())
        config = {**manifest['scoring'], **{k: case[k] for k in ('controls', 'sharedFrame') if k in case}}
        reference = read_json(paths['reference'])
        result = score_case(read_json(paths['prediction']), reference, config)
        for element in reference['elements']:
            key = (room, element['id'])
            signature = (element['kind'], element['dimensions'], element.get('uncertaintyMeters', 0), element['observable'])
            if key in reference_values and reference_values[key] != signature:
                raise ValueError('Repeated reference ID has inconsistent dimensions or visibility')
            reference_values[key] = signature
        cases.append({'id': case_id, 'physicalRoomId': room, 'partition': case['partition'],
                      'predictionSha256': digest(paths['prediction']), 'referenceSha256': digest(paths['reference']),
                      **result})
    output.mkdir(parents=True, exist_ok=True)
    destinations = [output / 'structure-results.json', output / 'structure-dimensions.csv']
    if any(path.exists() or path in input_paths for path in destinations):
        raise ValueError('Use a fresh output directory; benchmark results are immutable')
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=Path(__file__).parent,
                                         text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=Path(__file__).parent,
                                            text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    repeats = {}
    for case in cases:
        for element in case['elements']:
            for name, value in element['dimensions'].items():
                key = (case['physicalRoomId'], element['referenceId'], name)
                repeats.setdefault(key, []).append(value['actualMeters'])
    repeat_rows = []
    for (room, element, name), values in repeats.items():
        present = [v for v in values if v is not None]
        repeat_rows.append({'physicalRoomId': room, 'referenceId': element, 'dimension': name,
                            'scans': len(values), 'measuredScans': len(present),
                            'rangeMeters': max(present)-min(present) if len(present) >= 2 else None,
                            'passed': len(present) >= 3 and len(present) == len(values)
                                and max(present)-min(present) <= manifest['scoring']['toleranceMeters'] + 1e-12})
    report = {'version': 1, 'manifestSha256': digest(manifest_path), 'scoring': manifest['scoring'],
              'scorerSha256': {name: digest(Path(__file__).parent/name) for name in ('benchmark_structure.py', 'metrics.py')},
              'runtime': {'python': platform.python_version(), 'numpy': np.__version__, 'platform': platform.platform()},
              'commit': commit, 'workingTreeDirty': dirty, 'cases': cases, 'repeatability': repeat_rows,
              'limitations': ['Scoring only: no reconstruction was run.',
                             'Centre matching is a baseline; ambiguous associations need manual review.',
                             'Shape, critical edges, occlusion completeness and inferred faces are not scored yet.',
                             'This report cannot qualify a physical accuracy release.']}
    destinations[0].write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    with destinations[1].open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=['case', 'referenceId', 'dimension', 'referenceMeters',
                                                   'actualMeters', 'referenceUncertaintyMeters', 'errorMeters', 'passed'])
        writer.writeheader()
        for case in cases:
            for element in case['elements']:
                for name, value in element['dimensions'].items():
                    writer.writerow({'case': case['id'], 'referenceId': element['referenceId'], 'dimension': name, **value})
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path); parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = run(args.manifest, args.output)
    print(json.dumps({'cases': len(result['cases']), 'basicChecksPassed': sum(c['basicChecksPassed'] for c in result['cases']),
                      'releaseQualified': False}))
