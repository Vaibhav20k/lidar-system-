"""Keep every supported run. Gaps and abrupt profile changes split instances.

No automatic extension to a hidden corner; that requires inference metadata.
"""


def supported_runs(found, minimum_samples=4, change_limit=.08):
    result, start = [], None
    for i in range(len(found)+1):
        current = found[i] if i < len(found) else None
        previous = found[i-1] if i else None
        split = current is None or (previous is not None and
            (abs(current[0]-previous[0]) > change_limit or
             abs((current[2]-current[1])-(previous[2]-previous[1])) > change_limit))
        if split and start is not None:
            if i-start >= minimum_samples:
                result.append((start, i))
            start = None
        if current is not None and start is None:
            start = i
    return result
