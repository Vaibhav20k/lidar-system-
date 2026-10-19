"""Reference-only benchmark metrics. Never import this module in an estimator.

All geometry is metres. A missing measurement is a failure, not zero error.
"""
import math
import numpy as np


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Measurements must be finite numbers')
    return float(value)


def dimension_score(actual, expected, uncertainty=0., tolerance=.02):
    expected, uncertainty, tolerance = map(finite, (expected, uncertainty, tolerance))
    if uncertainty < 0 or tolerance <= 0:
        raise ValueError('Invalid uncertainty or tolerance')
    error = None if actual is None else finite(actual) - expected
    return {'referenceMeters': expected, 'actualMeters': actual,
            'referenceUncertaintyMeters': uncertainty, 'errorMeters': error,
            'passed': error is not None and abs(error) + uncertainty <= tolerance + 1e-12}


def error_summary(scores):
    errors = [s['errorMeters'] for s in scores if s['errorMeters'] is not None]
    absolute = np.abs(errors)
    return {'required': len(scores), 'measured': len(errors),
            'missing': len(scores) - len(errors), 'passed': sum(s['passed'] for s in scores),
            'maeMeters': float(np.mean(absolute)) if errors else None,
            'p95Meters': float(np.percentile(absolute, 95)) if errors else None,
            'worstMeters': float(np.max(absolute)) if errors else None,
            'biasMeters': float(np.mean(errors)) if errors else None,
            'allPassed': bool(scores) and all(s['passed'] for s in scores)}


def rigid_alignment(source, target):
    """Kabsch fit to independent corresponding control points, WITHOUT scale.

    Three non-collinear controls suffice. Controls must not be evaluated targets.
    """
    source, target = np.asarray(source, dtype=float), np.asarray(target, dtype=float)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3 or len(source) < 3:
        raise ValueError('At least three corresponding 3D controls are required')
    if not np.isfinite(source).all() or not np.isfinite(target).all():
        raise ValueError('Controls must be finite')
    a, b = source.mean(axis=0), target.mean(axis=0)
    if min(np.linalg.matrix_rank(source-a), np.linalg.matrix_rank(target-b)) < 2:
        raise ValueError('Controls must not be collinear')
    u, _, vt = np.linalg.svd((source-a).T @ (target-b))
    rotation = vt.T @ u.T
    if np.linalg.det(rotation) < 0:
        vt[-1] *= -1
        rotation = vt.T @ u.T
    translation = b - rotation @ a
    residual = np.linalg.norm(source @ rotation.T + translation - target, axis=1)
    return rotation, translation, float(residual.max())


def match_instances(predictions, references, max_distance):
    """Minimum-cost maximum-cardinality one-to-one centre matching.

    Type and ID do not influence matching. The frozen distance gate is an
    association rule, NOT a dimensional-accuracy threshold. Candidate ambiguity
    is surfaced for human correspondence review, never called a proven split.
    """
    if finite(max_distance) <= 0:
        raise ValueError('Matching distance must be positive')
    n, m = len(predictions), len(references)
    source, sink = n+m, n+m+1
    graph = [[] for _ in range(n+m+2)]

    def edge(a, b, cost):
        forward = [b, len(graph[b]), 1, cost]
        backward = [a, len(graph[a]), 0, -cost]
        graph[a].append(forward); graph[b].append(backward)
        return forward

    for i in range(n):
        edge(source, i, 0.)
    for j in range(m):
        edge(n+j, sink, 0.)
    candidates = []
    for i, p in enumerate(predictions):
        for j, r in enumerate(references):
            distance = float(np.linalg.norm(np.array(p['center']) - r['center']))
            if distance <= max_distance:
                candidates.append((i, j, distance, edge(i, n+j, distance)))
    while True:
        distances = [math.inf] * len(graph); distances[source] = 0
        previous = [None] * len(graph)
        # Reverse residual edges allow reassignment instead of greedy matches.
        for _ in range(len(graph)-1):
            changed = False
            for a, edges in enumerate(graph):
                for k, (b, _, capacity, cost) in enumerate(edges):
                    if capacity and distances[a] + cost < distances[b] - 1e-12:
                        distances[b] = distances[a] + cost
                        previous[b] = (a, k); changed = True
            if not changed:
                break
        if previous[sink] is None:
            break
        b = sink
        while b != source:
            a, k = previous[b]; e = graph[a][k]
            e[2] = 0; graph[b][e[1]][2] = 1; b = a
    pairs = [(i, j, distance) for i, j, distance, e in candidates if not e[2]]
    ambiguity = {'predictions': [predictions[i]['id'] for i in range(n)
                                if sum(a == i for a, _, _, _ in candidates) > 1],
                 'references': [references[j]['id'] for j in range(m)
                                if sum(b == j for _, b, _, _ in candidates) > 1]}
    return pairs, ambiguity
