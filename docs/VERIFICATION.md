# Verification record — September 30, 2026

This is a prototype delivery, with automated software checks and an Android emulator smoke test. It is not a physical measurement validation or a production release certification.

| Check | Observed result |
| --- | --- |
| TypeScript and Vite production build | Passed; lazy-loaded 3D renderer and export bundle |
| Geometry/API tests | 14 tests passed in 2 suites |
| Chrome end-to-end tests | 2 passed, covering desktop flow and 393×852 mobile layout |
| Android Gradle build | Debug APK successfully compiled and packaged with Java 21, SDK 36 and ARCore 1.54.0 |
| Android lint | Passed with 0 errors and 25 non-blocking warnings after the Android 16 back-navigation fix |
| Xcode project structure | Parsed successfully; `RoomScanner.swift` included once in the Sources phase |
| iOS compile/sign/install | Not run: the execution host is Windows; macOS/Xcode and signing remain required |
| Android emulator install/launch | APK installed successfully; main activity and Capacitor RoomScanner plugin loaded |
| Android native bridge | Returned platform `android`, supported `false`, mode `manual`, with an ARCore-unavailable explanation on this emulator |
| Android WebView workflow | Manual room created, saved, floorplan inspected and persisted through reload |
| Dependency audit | npm audit reported 0 vulnerabilities at verification time |
| Physical LiDAR/ARCore capture | Not run; no physical Android device connected and iPhone install/signing not available on this host |

## What the automated tests establish

- Floor validation preserves concavity and rejects crossing/repeated/tiny outlines.
- Openings cannot extend outside their walls or overlap another opening. Export meshes have actual holes at openings.
- Scale calibration transforms floor, walls, openings and furniture consistently.
- Apple column-major transforms normalize correctly on synthetic fixtures; incomplete wall loops remain explicitly incomplete.
- Guided Android corners produce the intended floor. Synthetic convex depth clouds fit a perimeter; insufficient coverage fails explicitly.
- The API validates geometry, requires the configured bearer token, rejects unauthorized origins, preserves SQLite data across restart and rejects stale revisions.
- UI changes survive saving/reload. JSON round-trips, SVG contains expected dimensions, and GLB downloads have valid version/length headers. Browser tests do not assert glTF conformance against every external design package.
- Sync conflict recovery preserves local changes in a separate copy and resumes revision-aware sync. Repeated test runs use unique room names.
- Browser scan controls state that an installed app is needed and offer manual creation; no sample room is passed off as a sensor capture.

## Runtime and environment notes

The Android emulator was launched headlessly with `-read-only -no-snapshot` and shut down after testing. Its saved AVD data was not modified. Cold boot during the concurrent Gradle workload produced a **System UI not responding** dialog; the app's native bridge and subsequent WebView workflow completed. This smoke test does not establish physical-device performance, camera rendering or ARCore scan reliability. No ARCore-supported sensor capture occurred in the emulator.

The native activity was updated to AndroidX back callbacks after lint identified Android 16's predictive-back incompatibility. Android file I/O avoids APIs introduced after the app's minimum SDK 24. On this Windows host, Java 25 could not run Gradle 8.14.3; a project-local Java 21 runtime and temporary non-OneDrive Gradle caches were used.

Three.js remains a roughly 617 kB minified chunk (~155 kB gzip); Vite reports a size warning. Rendering and export modules load separately. Node 24 reports its SQLite API as experimental. App-store icons, localization and remaining template/style lint warnings belong to the production-hardening milestone.

The workflow in `.github/workflows/checks.yml` is provided for manual execution; it was not run on GitHub. No public deployment, store upload, external sharing of room data or signed iOS distribution was performed.

## Evidence and next acceptance gate

Browser screenshots are in `test-results/editor-desktop.png` and `test-results/editor-mobile.png`; an emulator WebView capture is `test-results/android-webview.png`. These are development artifacts ignored by Git. Android CDP full-page capture showed repeated viewport content and was taken without waiting for a completed 3D frame; it is not visual proof of native 3D rendering quality. Native 3D performance and camera scanning still require device review.

Follow `DEVICE_TESTING.md` on the iPhone 16 Pro and representative Android phones. The release gate is signed/device builds plus measured capture success, wall/diagonal/height/opening errors, interruption recovery, performance and export compatibility. Hardware accuracy remains unknown until that gate is run.
