# Phase delivery workflow

The user designated `https://github.com/Vaibhav20k/lidar-system-.git` as the delivery repository and authorized pushing updated code after every completed phase.

After completing and validating each phase:

1. Update the phase status with changes, test results and remaining limitations.
2. Commit the phase's source, tests and documentation.
3. Push to the delivery repository's `main` branch. Inspect remote changes first and integrate them safely; do not force-push over remote work.
4. Report the phase, commit and validation results to the user.

Keep private scan bundles, photos, tokens, local environments and generated test outputs out of commits. The original repository remains available as the `upstream` remote.
