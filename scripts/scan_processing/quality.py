"""Geometry quality checks. Consistency is evidence, not an accuracy certificate."""
import numpy as np

PLANE_BUDGET = 0.01  # two independently estimated planes consume a 2 cm dimension budget


def repeat_view_check(along, offsets, heights, frames, width, limit=PLANE_BUDGET):
    """Compare frame medians BEFORE selecting a fitted surface layer.

    A patch must be revisited in multiple frames in each temporal third. Missing
    evidence fails closed. Different layers/reflections are disagreements too;
    this check does not claim that every disagreement is tracking drift.
    """
    samples = []
    for x in np.arange(0, width, 0.5):
        for y in np.arange(0.3, 2.0, 0.5):
            mask = (along >= x) & (along < x + .5) & (heights >= y) & (heights < y + .5)
            values, ids = offsets[mask], frames[mask]
            observations = [(int(f), float(np.median(values[ids == f])))
                            for f in np.unique(ids) if np.sum(ids == f) >= 20]
            if len(observations) < 6:
                continue
            # Temporal boundaries use frame ids, not sample density or a selected mode.
            times = np.array([o[0] for o in observations])
            span = times[-1] - times[0]
            if span < 6:
                continue
            early = [v for f, v in observations if f <= times[0] + span / 3]
            late = [v for f, v in observations if f >= times[-1] - span / 3]
            if len(early) < 2 or len(late) < 2:
                continue
            medians = np.array([o[1] for o in observations])
            samples.append({"along": float(x), "height": float(y),
                            "frames": len(observations),
                            "shift": abs(float(np.median(late) - np.median(early))),
                            "spread": float(np.percentile(medians, 90) - np.percentile(medians, 10))})
    if len(samples) < 5:
        return {"passed": False, "reason": "insufficient repeat-view evidence", "patches": len(samples)}
    shift = float(np.percentile([s['shift'] for s in samples], 90))
    spread = float(np.percentile([s['spread'] for s in samples], 90))
    passed = shift <= limit and spread <= 2 * limit
    return {"passed": passed, "reason": "consistent" if passed else "repeat views disagree",
            "metric": "early-late surface offset", "poseDriftProven": False,
            "patches": len(samples), "shift": shift, "spread": spread,
            "limit": limit}


def robust_plane(points, frames, horizontal=False):
    """Fit z=f(x,y) (or y=f(x,z)) with equal spatial/frame representation.

    Returns a candidate even when QA fails. Callers must not apply failed fits.
    Observed residuals are NOT a calibrated confidence interval.
    """
    if len(points) < 200 or len(np.unique(frames)) < 6:
        return {"passed": False, "reason": "too few independent views"}
    axes, ordinate = ([0, 2], 1) if horizontal else ([0, 1], 2)
    xy, z = points[:, axes], points[:, ordinate]
    keys = np.column_stack([frames, np.floor(xy / .15).astype(int)])
    _, inverse = np.unique(keys, axis=0, return_inverse=True)
    order = np.argsort(inverse); sorted_keys = inverse[order]
    rows, ids = [], []
    for key, start, count in zip(*np.unique(sorted_keys, return_index=True, return_counts=True)):
        if count < 4:
            continue
        idx = order[start:start + count]
        rows.append(np.median(points[idx], axis=0)); ids.append(int(frames[idx[0]]))
    p = np.asarray(rows); ids = np.asarray(ids)
    if len(p) < 40 or len(np.unique(ids)) < 6:
        return {"passed": False, "reason": "too little surface coverage"}
    # Frames held out in their entirety; no point from them enters the fit.
    unique = np.unique(ids); held_ids = unique[::3]
    train = ~np.isin(ids, held_ids)
    origin = np.median(p[:, axes], axis=0)
    a = np.column_stack([p[:, axes] - origin, np.ones(len(p))]); b = p[:, ordinate]
    weights = np.ones(np.sum(train))
    beta = np.zeros(3)
    for _ in range(8):
        beta, _, rank, _ = np.linalg.lstsq(a[train] * weights[:, None], b[train] * weights, rcond=None)
        if rank < 3:
            return {"passed": False, "reason": "surface fit is underconstrained"}
        residual = b[train] - a[train] @ beta
        weights = np.sqrt(np.minimum(1., .01 / np.maximum(abs(residual), 1e-9)))
    residual = b - a @ beta
    frame_offsets = np.array([np.median(residual[ids == f]) for f in unique])
    held_error = float(np.percentile(abs(residual[~train]), 90))
    spread = float(np.percentile(frame_offsets, 90) - np.percentile(frame_offsets, 10))
    passed = held_error <= PLANE_BUDGET and spread <= 2 * PLANE_BUDGET
    return {"passed": passed, "reason": "consistent" if passed else "held-out views disagree",
            "origin": origin.tolist(), "coefficients": beta.tolist(),
            "heldoutP90": held_error, "frameSpread": spread, "frames": len(unique),
            "span": np.ptp(p[:, axes], axis=0).tolist(), "cells": len(p)}
