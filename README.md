# Roomform

A room capture and review app for **iPhone and Android**, with a desktop browser editor. Built from the Cyncly research as an independent implementation using public platform APIs. Cyncly's proprietary scanning engine has not been identified or copied.

## Current delivery

- Editable 3D room and dimensioned floorplan; manual room creation and JSON import.
- Wall heights/thicknesses, floor corners, doors/windows, furniture volumes, undo and reference scale calibration.
- Local saving/reloading with IndexedDB. Optional SQLite sync with bearer tokens and revision conflict protection.
- JSON, GLB, SVG and browser print/PDF export. Native file sharing; original iOS USDZ sharing.
- Native **Swift RoomPlan capture** integrated into the iOS project.
- Native **Java ARCore capture** integrated into the Android project: tracked floor corners plus experimental depth-based convex layout inference.
- Clear separation of the sample scene, manual geometry and actual captures.

**Physical scan quality is not yet validated.** iOS compilation requires macOS/Xcode; signing and an iPhone are needed for the actual scan. The available iPhone 16 Pro is suitable for the RoomPlan path. Android depth capability varies by device. The browser is a review/editor interface and cannot access Apple RoomPlan or Android ARCore.

## Run the editor

Install Node.js 24 LTS, then from this directory:

```sh
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. The initial room is an explicitly labelled sample. Use **Create a room manually** to enter your dimensions, or **Import room** for a Roomform JSON/native capture file. **Save** persists changes on this device; exports make portable backups. Clearing site/app storage removes saved editor rooms.

For optional sync, run `npm run server` in another terminal. Leave the server field blank in the local editor to use Vite's `/api` proxy. No server is needed for capture, editing or exports in the installed app.

## iPhone 16 Pro: build and scan

On a Mac with Xcode supporting your phone's iOS version and Capacitor 8:

```sh
npm ci
npm run build
npx cap sync ios
npx cap open ios
```

1. In Xcode, select the **App** target, set your development team under **Signing & Capabilities**, and choose a unique bundle identifier if required.
2. Connect the iPhone 16 Pro, trust the Mac, enable Developer Mode if requested, select the phone as the run destination, and press Run.
3. In Roomform, select **Scan a room**, enter a name, start and grant camera access. Slowly cover the floor, each wall, openings and furniture. Tap **Finish scan**.
4. Review dimensions in the editor. Save and export JSON/GLB; **Original Apple capture** shares the original USDZ. Edits to the shared model do not rewrite that original USDZ.

`RoomScanner.swift` is already registered in the Xcode project and main storyboard. Camera usage text is included. Unsupported iOS devices retain manual entry and review. The simulator cannot perform LiDAR capture. A Windows browser cannot install a signed iOS app. The optional CI workflow can compile unsigned iOS code, but signing/device testing is still separate.

## Android: build and scan

Use Android Studio, Android SDK platform 36/build tools, and **JDK 21**. ARCore 1.54.0 is pinned for this API-36 project. Do not point Gradle 8.14.3 at Java 25.

```sh
npm ci
npm run build
npx cap sync android
npx cap open android
```

Build/run on an ARCore-supported Android device. Or use `android/gradlew -p android assembleDebug` (Windows: `android\gradlew.bat -p android assembleDebug`). Ensure `JAVA_HOME` points to JDK 21 and `ANDROID_HOME` to the SDK. Output: `android/app/build/outputs/apk/debug/app-debug.apk`.

- **Guided room:** aim the center crosshair at each floor corner and select **Add corner**, walking around the perimeter in order. Do not repeat the first corner. Enter the measured wall height before capture. Tracking anchors supply the measured corner positions; height is manual.
- **Depth estimate (experimental):** select **Set floor**, scan every wall, leave the corner list empty, and finish. Confidence-filtered depth is fitted to a convex boundary. If coverage is insufficient, the app rejects the estimate and asks for guided corners. L-shaped/non-convex rooms require guided corners.
- Depth support is checked at runtime. Android does not automatically label furniture, windows or doors in this version; add them during review. No dedicated LiDAR sensor is assumed.

If OneDrive or antivirus locks Gradle caches, put `GRADLE_USER_HOME` and `--project-cache-dir` in a local non-synced temporary directory. Avoid relocating personal files or deleting existing Gradle caches to fix a build.

## Verification

```sh
npm test
npm run build
npm run test:e2e
npm audit
```

End-to-end tests use installed Chrome and start/reuse local Vite/API servers. They create test rooms in the local API database. Geometry/API tests cover concave floors, incomplete scans, invalid openings, Apple transforms, depth boundary inference, mesh openings, authentication, persistence and optimistic concurrency. Browser tests cover editing, reload, export contents, import, sync conflict recovery, unsupported browser scanning and mobile layout.

See [verification record](docs/VERIFICATION.md), [implementation plan](docs/IMPLEMENTATION_PLAN.md) and [device test checklist](docs/DEVICE_TESTING.md) for what has actually been run and what remains.

## Model and storage

The schema is versioned (`schemaVersion: 1`), measured in meters, right-handed and Y-up. Floor points use X/Z. Walls keep endpoints, height, base height and display thickness. Openings are relative to wall start; furniture uses center, size and yaw. Evidence distinguishes measured, estimated, edited and sample elements. Floor outlines inferred from wall endpoints are labelled as inferred. RoomPlan wall thickness is a display default. A measured wall still has estimation error; evidence is not an accuracy guarantee.

IndexedDB holds editor rooms on each device. Native raw captures are kept in the app's private files/Application Support folders to allow recovery if normalization fails. Deleting an editor room currently removes that local editor entry, not the retained native recovery files or server copy. Uninstalling/clearing app data removes private captures. Tokens are held only in memory. No camera images are uploaded by Roomform. Exports and sync contain room geometry; Apple device file paths are omitted.

Optional server environment variables: `ROOMFORM_HOST` (default `127.0.0.1`), `ROOMFORM_PORT` (`8787`), `ROOMFORM_DATA_DIR` (`.data`), `ROOMFORM_API_TOKEN`, and comma-separated `ROOMFORM_ALLOWED_ORIGINS`. A non-loopback host requires a token of at least 24 characters. For remote use, supply HTTPS through your own authenticated deployment/reverse proxy. This is a **single workspace sync service**, not a multi-tenant production service. It lists up to 500 rooms. Do not expose the development server publicly.

## Source map

| Path | Responsibility |
| --- | --- |
| `src/domain/room.ts` | Schema, validation, dimensions and editing |
| `src/domain/native.ts` | Apple/Android normalization, conservative depth inference |
| `src/scene/buildRoom.ts` | Exportable room meshes with wall openings |
| `src/components/` | 3D view, floorplan, inspector and dialogs |
| `src/services/` | Local storage, sync, capture bridge and exports |
| `ios/App/App/RoomScanner.swift` | RoomPlan capture and original USDZ sharing |
| `android/app/src/main/java/com/roomform/studio/` | ARCore activity and Capacitor bridge |
| `server/` | Validated SQLite API |

Primary technical references: [Apple RoomPlan](https://developer.apple.com/augmented-reality/roomplan/), [processed capture callback](https://developer.apple.com/documentation/roomplan/roomcaptureviewdelegate/captureview(didpresent:error:)), [ARCore depth](https://developers.google.com/ar/develop/depth), [raw-depth coordinate conversion](https://codelabs.developers.google.com/codelabs/arcore-rawdepthapi), [ARCore releases](https://github.com/google-ar/arcore-android-sdk/releases), and [Capacitor native workflow](https://capacitorjs.com/docs/basics/workflow).
