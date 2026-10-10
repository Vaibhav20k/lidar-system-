# Working session summary — 10 October 2026

This records what was researched, decided and changed in one working session, so the work can be picked up on another machine or by another person. All code from the session is on the branch `ios-scanner-registration-raw-capture`; `main` was not changed and no pull request was opened.

## Goal

An iPhone app where you open the camera, scan a room with the LiDAR sensor, and get a 3D model of it. An LLM layer for "smart" features is planned later. iOS only for now.

## Where things stand

- The iOS code compiles in CI (two successful runs of the `ios` job).
- **The app has still never been run on an iPhone.** Everything about how scanning behaves on a device is unverified.
- The blocker is local tooling: the Mac used had macOS 15.7.3 and no Xcode. The current App Store Xcode needs macOS 26.6 or later, so the Mac is being upgraded to macOS Tahoe 26.7.1 first.

## Research findings

The full report is in [research/iphone-lidar-room-scanning.md](research/iphone-lidar-room-scanning.md). The points that drove decisions:

- **Both reference videos show Apple RoomPlan.** They show an untextured white "dollhouse" model and then a dimensioned 2D floor plan, with multi-room scanning.
  - https://youtu.be/bEiJrvvE5zk is Cyncly's "Measure Mobile" flooring app. Cyncly does not name RoomPlan; this is inferred from the on-screen UI.
  - https://youtube.com/shorts/2UTADUQvKVs is magicplan. Its help page confirms the Auto-Scan feature uses RoomPlan.
  - Neither video shows photoreal textures, file export or AR placement.
- **This repo already uses RoomPlan.** It is a Capacitor app: a React + three.js web UI in a WebView, plus a native Swift plugin that runs the scan.
- **A web page cannot reach the LiDAR sensor.** Scanning must be native. Deploying the web build (for example to Vercel) gives the editor and viewer only, with scanning disabled.
- **Apple offers three different tools**, and no single one gives a photoreal whole-room model on the phone:
  - RoomPlan: clean, measured, parametric room (walls, doors, windows, furniture boxes). No textures.
  - ARKit scene reconstruction: rough triangle mesh, no colour.
  - Object Capture: photoreal, but for objects and small areas.
- **`awesome-lidar` is mostly automotive and robotics.** The parts that help are general libraries (Open3D, PCL, ICP libraries), and they belong on a server. The most useful references are outside that list: Apple's RoomPlan and scene-reconstruction samples, the RTAB-Map iOS app, Stray Scanner, and `expo-roomplan`.
- **Licence risks for a commercial app:** CloudCompare and MeshLab are GPL, OpenSplat is AGPL, and the original Inria Gaussian-splatting code is non-commercial.
- **Accuracy:** independent studies report roughly 1 to 1.5 cm error, on 2020 to 2022 hardware. No controlled accuracy study of RoomPlan itself was found. Fitted kitchens or wardrobes would still need a laser-measure check.

Things the research could not confirm: any RoomPlan or ARKit changes in iOS 26 and 27, the LiDAR specification of the iPhone 18 Pro, and several market figures that came from search summaries.

## Decisions

1. **Ship the parametric room model first** (RoomPlan), because that is what both videos show and what the repo already does. A photoreal textured model is a different pipeline that needs a server GPU or a Mac, and is deferred.
2. **Keep the Capacitor shell.** RoomPlan works well behind a native scan screen, and the existing viewer, editor and exporters stay usable. Moving to a fully native Swift app only makes sense if live mesh or texture overlays become a requirement.
3. **Record raw sensor data during every scan.** This keeps the LLM and photoreal options open, because old scans cannot be re-captured later.

## What changed in the code

Two commits on `ios-scanner-registration-raw-capture`:

**`c273fcb` Register iOS scanner plugin and record raw keyframes during scans**

- `ios/App/App/SceneDelegate.swift` installed a plain `CAPBridgeViewController`, so the subclass that registers the scanner plugin was never used and the scan button would have stayed disabled. It now installs `RoomformViewController`.
- `ios/App/App/RoomScanner.swift` gained `RawCaptureRecorder`. While RoomPlan scans, it saves a keyframe after about 25 cm of movement or about 15 degrees of turn, up to 240 per room: a JPEG, the LiDAR depth map, the confidence map, the camera pose and the intrinsics. The bundle is written to `<id>-raw/` beside the USDZ and described by its own `manifest.json`.
- On iOS 17 and later the capture JSON also includes floors (with corner points) and each opening's parent wall id.

**`bb784e5` Scan several rooms in one iOS session**

- After a room is built, the scanner offers **Next room** or **Done** instead of closing. Up to 12 rooms per session.
- On iOS 17 and later the AR session keeps running between rooms, so all rooms share one coordinate space (`sharedSpace: true` in each capture).
- Each room has its own USDZ, capture JSON and raw bundle, and becomes a separate room in the app.
- A room that fails to build no longer throws away the rooms scanned before it.
- Only the 12 newest raw bundles are kept; older ones are deleted when a new scan starts.
- Web side: `src/services/capture.ts` now accepts several captures (`scanRooms`, `normalizeCaptures`), `src/App.tsx` saves each one, and `tests/room.test.ts` has a multi-room test.

## Verification

| Check | Result |
| --- | --- |
| Unit and API tests (`npm test`) | 15 passed, run locally |
| Web production build (`npm run build`) | Passed locally |
| Browser end-to-end tests (`npm run test:e2e`) | 2 passed locally |
| CI `ios` job (unsigned `xcodebuild`) | Passed on both runs |
| CI `web` job | Failed on the first run with a 45 s timeout in the sync test; passed on the second run |
| CI `android` job | Failed on both runs in the Android SDK setup step (`sdkmanager`), before the app build. Not investigated |
| Run on a physical iPhone | **Not done** |

CI runs: https://github.com/utkarsh23153124-boop/Lidar_tech/actions/runs/38066398684 and https://github.com/utkarsh23153124-boop/Lidar_tech/actions/runs/38066721250

## What must be checked on the first device run

1. **Is the scan button enabled?** This is the real test of the plugin registration fix.
2. **Does the camera resume after "Next room"?** This flow has only been compiled, never run.
3. **Was depth recorded?** Look at `depthFrames` in a raw bundle's `manifest.json`. RoomPlan runs its own ARKit configuration, and it is not confirmed that it exposes scene depth. If the count is 0, photos and poses are saved but depth is not, and the recording approach has to change.
4. **How accurate is it?** Measure two or three walls with a tape or laser and compare.
5. **How big is a raw bundle?** The "roughly 100 MB" figure in the README is an estimate.

## How to get it onto an iPhone

Needs a Mac with Xcode, a USB cable, a LiDAR iPhone (a Pro model) and any Apple ID.

1. Upgrade the Mac to macOS 26.6 or later, then install Xcode from the App Store: https://apps.apple.com/app/xcode/id497799835. If the App Store still says Xcode is not compatible with the Mac, download an earlier Xcode from https://developer.apple.com/download/all/?q=Xcode. The Xcode version must support the iOS version on the phone.
2. Open Xcode once, accept the licence and install the iOS platform component.
3. In the repo:
   ```
   git checkout ios-scanner-registration-raw-capture
   npm ci
   npm run native:sync
   npx cap open ios
   ```
4. In Xcode, select the **App** target, open **Signing & Capabilities**, and choose your Apple ID as the team. Change the bundle identifier if Xcode reports a conflict.
5. Connect the iPhone, trust the Mac, and turn on **Settings → Privacy & Security → Developer Mode**.
6. Choose the iPhone as the run destination and press Run. On first launch, trust the developer under **Settings → General → VPN & Device Management**.

With a free Apple ID the installed app expires after 7 days and has to be run from Xcode again. TestFlight avoids the cable but needs the paid Apple Developer Program.

## Not built yet

- A combined plan of all rooms from one session. The data for it exists (shared coordinates on iOS 17+), but the web model still treats each room separately.
- The LLM layer. The structured capture JSON and the raw keyframes are meant to feed it.
- A photoreal textured model.
- The "fallback wall height" field in the scan dialog is still shown on iOS although the iOS scanner ignores it.
- The Android CI failure.

## Demo estimate

About 1 to 3 days after Xcode and a LiDAR iPhone are available, and possibly the same day if the first scan works. This is an estimate: first device runs often expose problems that compiling does not.
