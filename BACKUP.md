# Independent project backup

This repository contains the complete Git-managed project source and all uncommitted source changes copied from `Lidar_tech`, including the latest accuracy investigation, iOS build-4 source, local processing scripts and regression tests. Existing project Git history is retained. The source repository and its remotes were not modified.

Accuracy results and evidence are in `verification/2026-10-17/` and `scripts/scan_processing/ACCURACY.md`. The 2 cm target is **not achieved**. Build 4 compiled locally but was not installed because the iPhone disconnected. The last validation was 20 Python tests and 26 application tests passing.

Raw iPhone scans, local databases, `.env` credentials, dependency folders, cached Xcode packages and generated app bundles are not included. They remain in their original local locations. Existing `.gitignore` rules are retained. Use `.env.example` for configuration; do not commit real credentials.

To restore the web project: `npm ci`, `npm test`, then `npm run build`. Native iOS also requires Xcode/signing and `npx cap sync ios`. The validated native build used `CURRENT_PROJECT_VERSION=4 SWIFT_OPTIMIZATION_LEVEL=-O`; an ordinary unoptimized Debug build can make ceiling processing much slower. No AI API calls were used to create this backup.
