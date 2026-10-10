# Both videos show RoomPlan; prove yours works

Both reference videos show Apple's RoomPlan framework, and your existing repo already uses RoomPlan, so the fastest route to "open camera, scan room, get a 3D model" is to fix and device-test the code you have, not to start again. magicplan confirms RoomPlan in its own documentation; for Cyncly it is an inference from the on-screen UI, which is visually the stock RoomPlan capture screen. The catch is what "3D model" means: RoomPlan gives a clean, untextured, measurable "dollhouse" (walls, doors, windows, furniture as boxes), not a model that looks like the real room. A photoreal textured mesh or Gaussian splat is a different pipeline, not a higher quality setting of the same one, and Apple has no single API that produces it for a whole room on the phone. **The team must decide this first: parametric room model (RoomPlan) or photoreal mesh/splat.** The repo ("Roomform") is a sound design, but its iOS code has never been compiled or run on a device, and it has a probable one-line bug that stops the scanner plugin from loading. Our recommendation is to ship the parametric path first on the existing repo, record raw scan data from the same walk-through as cheap insurance, and add a cloud photoreal track later only if customers need it. Three things in this report are not confirmed and are flagged where they appear: what Apple changed in iOS 26/27, the LiDAR details of the iPhone 18 Pro, and every effort estimate (they are estimates, not measurements).

A note on how to read this report. "Verified" means a researcher read it in a primary source (Apple docs, a WWDC transcript, the repo's code, the GitHub API). "Vendor claim" means a company says it about its own product. "Inference" or "estimate" means the researchers' own reasoning. Nothing in the repo was built or run during the audit, and no open-source repo was cloned or tested, so statements about them describe what the code or README says, not observed behaviour.

## Both videos show a white RoomPlan "dollhouse", not a photoreal scan

**Video 1** is "Flooring: Measure Mobile - Lidar" from Cyncly, a 90-second marketing clip with music and no narration, published 2025-01-28 ([YouTube](https://www.youtube.com/watch?v=bEiJrvvE5zk)). Measure Mobile is a flooring estimating tool for sales staff, listed at $86/month or $809/year ([Cyncly product page](https://www.cyncly.com/en/products/rfms-core/mobile-apps)). The recording shows a dialog saying "Stand near center of the room and press Start to begin scanning" with an "Include objects" checkbox, then a dimmed camera view with thin white lines tracing wall, door and window edges, the coaching text "Point camera at top edge of wall", and a small white 3D room model growing at the bottom of the screen. A "Next Room" button lets the user continue through several rooms, a full-screen grey view shows the combined multi-room model, and the result finally lands as a top-down 2D plan where a room is selected and its values edited ([Video 1](https://youtu.be/bEiJrvvE5zk)).

**Video 2** is a 60-second magicplan Short, "5 Ways to Create Floor Plans With magicplan", published 2024-01-12 ([YouTube](https://www.youtube.com/watch?v=2UTADUQvKVs)). Only the first of the five methods is LiDAR, and it takes roughly six seconds of screen time (about 8 to 14 s); the other four are manual drawing workflows. The LiDAR part shows the same elements as video 1: the rounded coaching graphic, "Point camera at the top, then bottom edge of wall", white edge lines, and a white mini-model with box furniture ([Video 2](https://youtube.com/shorts/2UTADUQvKVs)).

Which technology is it? For magicplan this is **verified**: its help article says Auto-Scan "makes use of Apple's RoomPlan API" and needs an iPhone 12 Pro or newer LiDAR device on iOS 17 ([magicplan help](https://help.magicplan.app/auto-scan-your-floor-plan)). For Cyncly it is an **inference**: neither its product page nor its blog names RoomPlan or ARKit, but the coaching overlay, white outlines and bottom-centre dollhouse are the same as magicplan's RoomPlan screen, and Cyncly's own blog says users can "include or exclude furniture and objects", which matches RoomPlan's object detection ([Cyncly blog](https://www.cyncly.com/resources/blog/measure-mobile-most-innovative-tech-at-surfaces-2025)). No primary source confirms Cyncly's SDK. Nothing in either video suggests ARKit mesh scanning, photogrammetry, point clouds or splats: there is no triangle wireframe, no texture and no point display.

So the flow to reproduce is: choose "scan with LiDAR", see a live camera view with outlines and coaching, accept the room and optionally scan the next one, view a rotatable untextured 3D model, and then get an editable, dimensioned 2D floor plan. RoomPlan's stock capture view supplies the live scanning screen. The 2D plan and its editor are each vendor's own work and are the largest custom piece in both products (researchers' inference from the videos). **Neither video shows photoreal textures, file export, or AR placement of products**, so none of those is needed to match them.

Two cautions on the vendors' words. Cyncly's "accuracy rate of just 2%" is marketing copy with no method given ([Cyncly blog](https://www.cyncly.com/resources/blog/measure-mobile-most-innovative-tech-at-surfaces-2025)). And magicplan documents the hard cases plainly: open floor plans, stairwells, glass walls, no sloped ceilings, and furniture that gets misidentified, such as a couch scanned as a table ([magicplan help](https://help.magicplan.app/auto-scan-your-floor-plan); [magicplan FAQ](https://help.magicplan.app/autoscan-faq)). Expect to inherit the same limits. One research limit: only the first 54 seconds of video 1 could be viewed at full resolution, so small on-screen text later in that video was not readable.

## The repo is a real RoomPlan app that has never run on an iPhone

The first finding corrects a possible assumption: **this repo is not a web-camera mock.** It contains a real native Swift plugin that calls Apple RoomPlan, plus a React and three.js editor. The audit was read-only and static (HEAD `97468df`); nothing was installed, built or run.

**What it is.** A React 19 + TypeScript + Vite single-page room editor wrapped with Capacitor 8 for iOS and Android, with one custom native plugin per platform called `RoomScanner` and a small optional Express + SQLite sync server ([package.json](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/package.json)). It is about 2,000 lines of hand-written source, and the product name in code is "Roomform" ([capacitor.config.ts](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/capacitor.config.ts)). The whole iOS scanner lives in one 190-line file. It imports RoomPlan, checks `RoomCaptureSession.isSupported`, asks for camera permission, presents a full-screen native `RoomCaptureView` with Cancel and "Finish scan" buttons, and on finish exports a parametric USDZ and sends a small JSON of walls, openings and objects back over the Capacitor bridge ([RoomScanner.swift](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/ios/App/App/RoomScanner.swift)). TypeScript then normalises that JSON into a versioned room schema, and three.js rebuilds the room from boxes for viewing and export ([native.ts](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/src/domain/native.ts); [buildRoom.ts](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/src/scene/buildRoom.ts)). Exports are JSON, GLB, SVG floor plan, PDF via browser print, and the original USDZ through the share sheet ([export.ts](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/src/services/export.ts)). There is no mesh, texture or point cloud on iOS, no ARKit code used directly, and no server-side reconstruction. This is the same technology and the same kind of output as both videos.

**What is broken or untested.** The repo's own verification record says "iOS compile/sign/install: Not run: the execution host is Windows", and physical LiDAR capture "Not run" ([VERIFICATION.md](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/docs/VERIFICATION.md)). On top of that the audit found the following, all from reading code rather than running it.

The most important item is a **probable plugin-registration bug**. `SceneDelegate.swift` creates the window with a plain `CAPBridgeViewController()` ([SceneDelegate.swift, lines 10-11](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/ios/App/App/SceneDelegate.swift#L10-L11)), but the plugin is only registered by the subclass `RoomformViewController` ([RoomScanner.swift, lines 7-11](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/ios/App/App/RoomScanner.swift#L7-L11)). If the auditor's reading is right, the plugin never loads, JavaScript falls into its "native scanner is not available in this build" branch, and the Start button stays disabled. The fix is likely one line (use `RoomformViewController()` in the scene delegate), but this is an inference until someone builds it. It also contradicts the README, which says the plugin "is already registered in the Xcode project and main storyboard" ([README.md](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/README.md)).

Other gaps, in rough order of importance:

| Gap | Where | Why it matters |
|---|---|---|
| Never compiled, signed or run on iOS; no `DEVELOPMENT_TEAM` set; Xcode project appears hand-edited | [project.pbxproj](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/ios/App/App.xcodeproj/project.pbxproj) | Compile errors are unknown until a Mac opens it |
| Swift-to-TypeScript contract tested only with hand-written fixtures; no real RoomPlan capture exists in the repo | [tests/room.test.ts](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/tests/room.test.ts) | Key names, matrix order and category strings could be wrong on real data |
| Floor is guessed by chaining wall endpoints within 0.18 m; left empty if walls do not close | [native.ts](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/src/domain/native.ts) | Rooms with open passages will show "floor missing"; RoomPlan provides floors itself since iOS 17 |
| Wall thickness hard-coded to 0.12 m | [native.ts](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/src/domain/native.ts) | Not measured |
| One room per capture; default configuration; no `RoomCaptureSessionDelegate` | [RoomScanner.swift](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/ios/App/App/RoomScanner.swift) | No multi-room (both videos show it) and no custom guidance |
| Scan files never cleaned up; absolute sandbox path stored in the room record | [room.ts](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/src/domain/room.ts) | Storage leak; paths break after reinstall |
| Deployment target iOS 15, `UIRequiredDeviceCapabilities` only `armv7` | [Info.plist](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/ios/App/App/Info.plist) | Template leftovers; RoomPlan needs iOS 16+ and LiDAR |
| Scan dialog asks iOS users for a "fallback wall height" that Swift never reads | [App.tsx](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/src/App.tsx) | Dead UI |
| Very compressed code style: single lines over 1,500 characters in `App.tsx`, 19 KB of CSS in 9 lines | [App.tsx](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/src/App.tsx) | Hard to maintain; restructure before building more on it |

The tests that exist (14 vitest and 2 Playwright, per the repo's own record) cover TypeScript geometry, the server and the web editor. Nothing tests native code, and the audit did not rerun them. Git history is a single commit, so there is no record of how the code evolved. CI is manual-trigger only and the docs say it was never run on GitHub ([checks.yml](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/.github/workflows/checks.yml)).

**What is reusable.** Quite a lot. `RoomScanController` (capture view hosting, finish, cancel, timeout, interruption handling, USDZ export, serialisation) depends on Capacitor only through one completion closure, so it lifts into a pure Swift app with small changes. It already has sensible guards: double-scan protection, camera-denied message, a 90-second processing timeout and a path-traversal check on share. The versioned room schema with per-element `evidence` (measured, estimated, edited, sample) is a good data contract. The wall-chaining, opening-matching and scale-calibration maths, the three.js viewer, the GLB/SVG/JSON exporters, the sync server with revision conflict handling, and the device test checklist in [DEVICE_TESTING.md](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/docs/DEVICE_TESTING.md) are all reusable. The Android ARCore code is irrelevant to an iPhone goal.

**Is Capacitor viable?** Yes, in exactly the pattern used here: a web view for UI, a native full-screen screen for capture, and a few kilobytes of JSON back over the bridge. A web view cannot reach LiDAR on its own; caniuse lists WebXR in Safari on iOS as "Not supported" through Safari 27.2 ([caniuse](https://caniuse.com/webxr)). The auditor's estimate is that for a parametric goal the repo holds perhaps 70 to 80% of the code for a demo but is **0% proven on device**; for a realistic mesh goal the scanning core must be largely new. The repo's own docs estimate 1 to 2 weeks for an iPhone pilot and 3 to 5 months for a "credible limited MVP" ([IMPLEMENTATION_PLAN.md](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/docs/IMPLEMENTATION_PLAN.md)). Those are the original author's estimates, not validated ones.

## iPhone LiDAR gives a 256x192 depth map, and Apple offers three tools on top

### How the sensor works and what you actually receive

The LiDAR scanner is a direct time-of-flight sensor: it fires light, times the reflection, and repeats this "millions of times every second" ([WWDC22 110429](https://developer.apple.com/videos/play/wwdc2022/110429/)). The key point for developers is that **you never get raw LiDAR**. The sensor's sparse readings are fused with the wide camera's colour image by a machine-learning model that outputs a dense depth map ([WWDC20 10611](https://developer.apple.com/videos/play/wwdc2020/10611/)). In ARKit that map is 256x192 pixels at 60 Hz, in metres, with a three-level confidence value per pixel; AVFoundation can give 320x240 while streaming and 768x576 with photos, but without camera tracking ([WWDC22 110429](https://developer.apple.com/videos/play/wwdc2022/110429/); [ARDepthData](https://developer.apple.com/documentation/arkit/ardepthdata)). Because the depth is metric, scans come out at real-world scale. For walking around a room you need ARKit, because camera pose is what lets frames be fused together (researchers' decision rule).

Range is about 5 m per Apple's 2020 launch wording, seen only in a press summary ([The Loop](https://loopinsight.com/2020/03/18/what-you-need-to-know-about-apples-lidar-scanner-in-the-ipad-pro)). Apple itself names the failure cases: full-height mirrors and glass, very dark surfaces, ceilings beyond range, and light below about 50 lux ([WWDC22 10127](https://developer.apple.com/videos/play/wwdc2022/10127/)). Apple publishes no hardware datasheet. Sensor internals such as the emitter type and dot count are widely reported but were not sourced in this research, so we leave them out.

LiDAR has been on Pro models only: iPhone 12 Pro onward through iPhone 17 Pro, according to press coverage read as a search summary ([iTech Post](https://www.itechpost.com/articles/237290/20260910/how-does-iphone-lidar-sensor-work-what-can-you-do-scanner.htm)), plus iPad Pro from 2020. **iPhone 18 Pro LiDAR is not confirmed**: no Apple spec page was found, only an unverified third-party listing. The practical answer is to gate on runtime checks (`RoomCaptureSession.isSupported`, `supportsSceneReconstruction`, `supportsFrameSemantics(.sceneDepth)`) and never on a hard-coded model list. Your addressable users are Pro iPhone and iPad Pro owners, so the app needs a clear path for unsupported devices.

### The three Apple tools produce three different things

| What you get | Apple API | Output | Main limits |
|---|---|---|---|
| Clean measured room: walls, doors, windows, furniture as boxes | **RoomPlan** (iOS 16+) | Parametric `CapturedRoom`, USD/USDZ | About 9 x 9 m per room, about 5 min per scan, about 186 m² for multi-room; no texture |
| Rough true-shape geometry | **ARKit scene reconstruction + scene depth** (iOS 13.4+ / 14+) | Untextured triangle mesh (`ARMeshAnchor`) with per-face labels; depth per frame | No colour or UVs; coarse; built for occlusion and physics |
| Photoreal textured model | **Object Capture** (iOS 17+) | Textured USDZ | Object-scale; reduced detail on iPhone; full quality needs a Mac |

Sources: [WWDC22 10127](https://developer.apple.com/videos/play/wwdc2022/10127/), [WWDC23 10192](https://developer.apple.com/videos/play/wwdc2023/10192/), [ARMeshGeometry](https://developer.apple.com/documentation/arkit/armeshgeometry), [WWDC23 10191](https://developer.apple.com/videos/play/wwdc2023/10191/), [WWDC24 10107](https://developer.apple.com/videos/play/wwdc2024/10107/).

**RoomPlan** is Apple's purpose-built room scanner. It uses the camera feed and LiDAR with trained models to identify walls, windows, openings and doors, and recognises furniture and appliances ([RoomPlan docs](https://developer.apple.com/documentation/roomplan)). It offers two levels. `RoomCaptureView` is the complete Apple scanning UI (overlays, coaching, live model, final preview) and is what both videos show and what your repo uses. `RoomCaptureSession` is the data API for a custom UI, with delegate callbacks for live room updates and instructions such as distance, speed and lighting ([WWDC22 10127](https://developer.apple.com/videos/play/wwdc2022/10127/)). `CapturedRoom` is `Codable`, so the full model serialises to JSON without going through USD, and export supports `.parametric`, `.mesh` and `.model` options ([CapturedRoom](https://developer.apple.com/documentation/roomplan/capturedroom); [USDExportOptions](https://developer.apple.com/documentation/roomplan/capturedroom/usdexportoptions)). Note that `.mesh` here means the same idealised room expressed as triangles, not a scan of real surfaces.

Almost everything valuable beyond the basics arrived in **iOS 17**, all confirmed from one WWDC session: multi-room scans merged by `StructureBuilder` into a `CapturedStructure`; floors; slanted, curved and polygon walls; room-type labels; object attributes and parent relationships; `ModelProvider` to swap furniture boxes for real 3D models in the export; a metadata file mapping USDZ nodes to room elements; and, most importantly for your architecture, the ability to run RoomPlan on **your own `ARSession`** ([WWDC23 10192](https://developer.apple.com/videos/play/wwdc2023/10192/)). That last feature means one walk-through can produce a RoomPlan model and ARKit mesh, depth and photos in the same coordinate system. The researchers' inference is that setting the deployment target to iOS 17 unlocks essentially the whole documented API at little device cost; check the exact cut-off against Apple's supported-device list.

RoomPlan's limits are guidance figures rather than hard caps: a single residential room up to about 9 x 9 m, scans under about 5 minutes for battery and heat reasons, and about 186 m² (2,000 sq ft) for a multi-room structure ([WWDC22 10127](https://developer.apple.com/videos/play/wwdc2022/10127/); [WWDC23 10192](https://developer.apple.com/videos/play/wwdc2023/10192/)). Multi-room needs all rooms in one world space, through a continuous AR session or `ARWorldMap` relocalisation, and backgrounding the app breaks that ([Apple: scanning a structure](https://developer.apple.com/documentation/roomplan/scanning-the-rooms-of-a-single-structure)). Developers also report a "Room size exceeded" error mid-scan ([Apple forums 775853](https://developer.apple.com/forums/thread/775853)). **Apple publishes no accuracy figure for RoomPlan.**

**ARKit scene reconstruction** gives a live triangle mesh split across many anchors. The geometry holds exactly four buffers (vertices, normals, faces, optional per-face classification) and no texture coordinates or colour ([ARMeshGeometry](https://developer.apple.com/documentation/arkit/armeshgeometry)). Apple ships no API to texture it and no file export for it. To get colour you must project camera frames onto the mesh yourself; ARKit can supply 4K video frames and high-resolution stills for that ([WWDC22 10126](https://developer.apple.com/videos/play/wwdc2022/10126/)). The researchers' inference is that this mesh is the right source for rough real-shape geometry and a poor source for a good-looking model on its own.

**Object Capture** is Apple's photogrammetry pipeline and its only first-party route to a photoreal textured mesh. On iPhone it reconstructs at reduced detail only; higher detail levels run on a Mac ([WWDC23 10191](https://developer.apple.com/videos/play/wwdc2023/10191/)). The "area mode" from WWDC24 drops the bounding box, but Apple says "areas larger than 6 feet may have reduced mesh and texture quality unless processed at a higher detail level on Mac" ([WWDC24 10107](https://developer.apple.com/videos/play/wwdc2024/10107/)). The session never says whole-room capture is supported. The researchers conclude it suits a wall section or an alcove, not a full room.

Two open gaps apply to this whole section. First, **no RoomPlan, ARKit or Object Capture changes for iOS 18, 26 or 27 were identified**, but the check was weak (the framework landing page shows no new or beta symbols, and no WWDC24 to WWDC26 RoomPlan session was located), so read this as "no changes identified", not "no changes". Second, whether RoomPlan, scene reconstruction and keyframe recording run together smoothly on one session is a use case Apple states but nobody has benchmarked in any source found.

## The key decision: a measurable dollhouse or a photoreal copy of the room

These are different pipelines, not quality tiers of one pipeline. Choose before more engineering goes in, because the choice changes the scanning core, the viewer, the need for a server, and what you can charge for.

| | Parametric room model (RoomPlan) | Photoreal textured mesh or splat |
|---|---|---|
| Looks like | White architectural diagram with box furniture | The actual room, with real surfaces and colours |
| Matches the two videos | Yes, exactly | No; neither video shows this |
| Matches the existing repo | Yes | No; scanning core would be largely new |
| Where it runs | On device, live | Rough mesh on device; good quality needs a server GPU or a Mac |
| Measurable and editable | Yes, directly | Mesh yes with effort; splat not directly |
| Feeds a 2D floor plan | Yes | Not directly |
| File size | Kilobytes of JSON, small USDZ | Tens of megabytes and up |
| Custom work | 2D plan and editor | Texturing, fusion, upload, cloud jobs, viewer |

The case for parametric is strong: it is what your references show, what your repo builds, and what the paying market buys. Polycam reserves floor plans and measurement tools for its $400/year Business tier ([Polycam pricing](https://poly.cam/pricing)), and Canvas charges per square foot to turn scans into CAD ([Canvas pricing](https://support.canvas.io/article/615-how-does-scan-to-cad-pricing-work)). The case for photoreal is equally clear if the user's real expectation is "it should look like my room", because RoomPlan will never deliver that.

You do not have to close the door on photoreal. Because RoomPlan can share an ARSession since iOS 17, the same walk-through can also record posed colour frames and depth. The researchers call recording this raw bundle from the first release "the cheapest insurance in the whole plan": reconstruction methods improve every year, and old scans can only be reprocessed if the raw data was kept. So the practical decision is which track to ship first, and we recommend parametric.

## awesome-lidar is 90% automotive; a handful of other repos actually help

### What the list contains

`szenergy/awesome-lidar` (1,355 stars, CC0, last pushed 2026-09-30) is a robotics and self-driving list ([GitHub](https://github.com/szenergy/awesome-lidar)). It has nine top-level sections. The researchers' estimate is that about 90% of entries can be skipped for this goal.

| Section | Contents | Use for an iPhone room scan |
|---|---|---|
| Manufacturers | About 24 vendors such as Velodyne, Ouster, Livox, Hesai | None; Apple is not listed |
| Datasets | Driving sets such as KITTI, Waymo, NuScenes | None; no indoor RGB-D data |
| **Libraries** | PCL, Open3D, PyTorch3D, Kaolin, PyVista, pyntcloud, LAStools | **Most useful section**; Open3D and PCL for server processing |
| Frameworks | Autoware, Baidu Apollo, ALFA | None |
| Algorithms: basic matching | simpleICP, libpointmatcher, NDT, KISS-ICP | Partly; ICP aligns two separate scans |
| Algorithms: segmentation, ground, SLAM, detection, calibration | RangeNet++, Patchwork, LOAM, LIO-SAM, FAST-LIO2, Cartographer and others | None on device |
| Simulators | CARLA, Gazebo, AirSim and others | None |
| Related awesome | 18 other lists | Marginal |
| **Others** | CloudCompare, MeshLab, Rerun, Foxglove, ARHeadsetKit | **Second most useful**; desktop inspection and debugging |

Source: the list's [README](https://raw.githubusercontent.com/szenergy/awesome-lidar/main/README.md).

The SLAM section looks relevant and is not. Those systems estimate position from spinning-LiDAR sweeps under ROS. On an iPhone, ARKit already supplies camera pose and a depth map each frame, so your problem is fusing posed depth frames into a surface (TSDF fusion and meshing), a family of algorithms the list does not cover. The list also has no iOS, ARKit, RoomPlan, USDZ, glTF, Gaussian splat or web viewer entries. Its one Swift entry, ARHeadsetKit, is archived. Tools you might expect there, such as PDAL, small_gicp, Potree and RTAB-Map, are not on it.

What to take from the list: **Open3D** (MIT, 14,061 stars) for server-side fusion and meshing, with documented RGB-D integration tutorials ([Open3D](https://github.com/isl-org/Open3D); [RGB-D integration](https://www.open3d.org/docs/release/tutorial/pipelines/rgbd_integration.html)); **PCL** (BSD) as a heavier C++ alternative; **libpointmatcher** (BSD-3) for aligning scans; **Rerun** (Apache-2.0) to replay a capture and debug pose and depth alignment; and **CloudCompare** and **MeshLab** as desktop inspection tools only. None is a practical on-device dependency: the only iOS port of Open3D found works by embedding a Python interpreter and has been stale since 2022 ([Open3D-iOS](https://github.com/kewlbear/Open3D-iOS)).

### The repos worth your time, in order

There is no well-maintained open-source "Polycam clone". The researchers ranked the following by fit and maturity; they did not build or run any of them, and no output-quality comparison exists.

| # | Repo or resource | Why study it | Licence |
|---|---|---|---|
| 1 | [Apple RoomPlan sample](https://developer.apple.com/documentation/roomplan/create-a-3d-model-of-an-interior-room-by-guiding-the-user-through-an-ar-experience) | Reference for the exact flow your repo implements | Apple sample licence (not read) |
| 2 | Apple [scene reconstruction](https://developer.apple.com/documentation/arkit/visualizing-and-interacting-with-a-reconstructed-scene) and [point cloud](https://developer.apple.com/documentation/arkit/displaying-a-point-cloud-using-scene-depth) samples | Canonical mesh and depth code | Apple sample licence (not read) |
| 3 | [introlab/rtabmap](https://github.com/introlab/rtabmap) `app/ios` | Only mature open-source end-to-end iOS LiDAR scanner; use its App Store build as a quality benchmark | BSD-style |
| 4 | [strayrobots/scanner](https://github.com/strayrobots/scanner) + [StrayVisualizer](https://github.com/kekeblom/StrayVisualizer) | Ready-made raw RGB-D + pose recorder with a documented format; prototype the server side before writing capture code | MIT |
| 5 | [isl-org/Open3D](https://github.com/isl-org/Open3D) | Server-side TSDF, meshing, registration | MIT |
| 6 | [TokyoYoshida/ExampleOfiOSLiDAR](https://github.com/TokyoYoshida/ExampleOfiOSLiDAR), [MetalWorldTextureScan](https://github.com/beatTheSystem42/MetalWorldTextureScan) | Compact examples of depth, OBJ export, textured scan; stale since 2021 and 2022 | MIT |
| 7 | [fordat/expo-roomplan](https://github.com/fordat/expo-roomplan) | Best reference for wrapping RoomPlan in a hybrid app, including all three export types | MIT |
| 8 | [google/model-viewer](https://github.com/google/model-viewer), [three.js](https://github.com/mrdoob/three.js) | Web display of GLB and USDZ | Apache-2.0 / MIT |
| 9 | [gsplat](https://github.com/nerfstudio-project/gsplat) + [MetalSplatter](https://github.com/scier/MetalSplatter) + [Spark](https://github.com/sparkjsdev/spark) | Optional photoreal track: train on server, render on iOS and web | Apache-2.0 / MIT / MIT |
| 10 | [xiongyiheng/ARKit-Scanner](https://github.com/xiongyiheng/ARKit-Scanner) | Second reference recorder, used for ScanNet++ collection | Apache-2.0 |

For your hybrid setup specifically: **no published Capacitor RoomPlan plugin exists** (the npm names checked do not exist and a GitHub search returned nothing), and the React Native and Flutter bridges are single-maintainer projects with 38 stars at most. Owning your own thin Swift plugin, as the repo already does, is the right call. Small RoomPlan-to-2D-floor-plan demos exist, such as [denniswave/RoomPlan-2D](https://github.com/denniswave/RoomPlan-2D), but they carry no licence file, so read them and do not copy them.

### Licence risks for a commercial app

This is not legal advice. Licences were classified from names and short excerpts, none was read in full, and Apple's sample-code licence was not read at all. Have a lawyer review anything you ship.

| Risk | Examples | Consequence |
|---|---|---|
| GPL | MeshLab and PyMeshLab (GPL-3.0), CloudCompare (GNU GPL), LichtFeld Studio (GPL-3.0), FAST_LIO (GPL-2.0) | Linking into a distributed app generally obliges releasing your source |
| AGPL | OpenSplat, openMVS, WebODM (AGPL-3.0) | Obligations reach software offered over a network, so a server-only pipeline is affected too |
| Weak copyleft | record3d (LGPL-2.1), Meshroom (MPL-2.0) | Usable with conditions; LGPL is awkward for statically linked iOS binaries |
| Non-commercial | ARKitScenes dataset, Inria `gaussian-splatting`, both on-device splat demos (PolyForm Noncommercial), DepthViz (CC BY-NC-ND), SpatialLM (Llama 3.2 licence) | Cannot be used in a commercial product without a separate licence |
| No licence file | IOS-PointCloud, ios-depth-point-cloud, markckim/RoomPlan, RoomPlan-2D, RoomPlanDemo and others | Default copyright applies; code cannot legally be copied |
| Permissive | Open3D, PCL, small_gicp, RTAB-Map, Stray Scanner, ARKit-Scanner, gsplat, nerfstudio, MetalSplatter, three.js, model-viewer, Spark, COLMAP | Attribution and notice only |

Metadata from the GitHub API on 2026-10-10 ([Open3D](https://github.com/isl-org/Open3D), [MeshLab](https://github.com/cnr-isti-vclab/meshlab), [OpenSplat](https://github.com/WebODM/OpenSplat), [Inria 3DGS](https://github.com/graphdeco-inria/gaussian-splatting), [ARKitScenes licence](https://github.com/apple-aiml-research/ARKitScenes/blob/main/LICENSE)).

A fully permissive stack is achievable (researchers' inference): Apple frameworks on the device, Open3D + small_gicp + gsplat on the server, and three.js, model-viewer or Spark on the web. The two traps to watch are the Inria splat code, which many splat projects quietly depend on, and AGPL tools inside a hosted processing service. Using CloudCompare or MeshLab as desktop apps on a developer's machine does not affect your app's licence.

## Pipelines, competitors and accuracy all point to selling the plan, not the scan

### Reconstruction options

This comparison is the researchers' synthesis. Timings that are not cited elsewhere in this report are background estimates.

| Pipeline | Output | Runs on | Strength | Weakness |
|---|---|---|---|---|
| RoomPlan | Parametric walls, openings, box furniture | Device, live | Clean, tiny, measurable, editable | Schematic, no texture |
| ARKit pose + LiDAR depth fusion + keyframe texturing | Textured metric mesh | Device, live | Offline, live preview | Soft edges, blurry texture, holes on glass and mirrors |
| Photogrammetry (COLMAP, Object Capture) | Dense textured mesh | Mac or cloud | Best texture on textured scenes | Fails on blank walls, slow |
| Gaussian splatting | Splat (PLY, SPZ), optional mesh | Cloud GPU; on device at lower quality | Photoreal, fast to render | Not directly measurable; mesh needs an extra step |
| Depth-supervised splat then mesh | Splat and mesh | Cloud GPU | LiDAR depth fixes blank walls | Research-grade code |
| Feed-forward models (VGGT family, MapAnything) | Poses, depth and points in one pass | Cloud GPU | No structure-from-motion step | Less accurate than LiDAR; licences vary by checkpoint |

Three sourced facts shape the photoreal track. First, **gsplat is Apache 2.0** and a from-scratch reimplementation, while the Inria reference rasteriser is for non-commercial research use ([gsplat paper](https://www.jmlr.org/beta/papers/v26/24-1476.html); [Inria licence mirror](https://dagshub.com/camenduru/diff-gaussian-rasterization/src/main/LICENSE.md)). Second, splat methods trained on images alone struggle with geometry in low-texture indoor scenes, and depth and normal supervision helps substantially ([DN-Splatter](https://arxiv.org/pdf/2403.17822)); blank walls are exactly a room, and LiDAR depth is exactly that prior. Third, on-device splat training is possible but hard: Scaniverse claims it as a vendor ([Niantic Spatial](https://nianticspatial.com/products/scaniverse)), the PocketGS paper reports "minute-scale" mobile training ([PocketGS](https://arxiv.org/abs/2601.17354)), and a summary of that paper puts the practical window at 3 to 5 minutes because of heat and battery.

Cloud compute is cheap. An RTX 4090 rents for roughly $0.22 to $0.45 per hour ([deploybase](https://deploybase.ai/articles/rtx-4090-cloud); [GPU price tracker](https://www.frenzycap.com/gpu-prices)), and one vendor benchmark shows gsplat training at about 12 to 18 minutes per scene on an unnamed GPU ([AMD ROCm benchmarks](https://rocm.docs.amd.com/projects/gsplat/en/latest/reference/benchmark-evaluation.html)). The researchers' arithmetic gives $0.07 to $0.15 per training run and a planning figure of **$0.20 to $0.50 per scan** once other steps and idle time are included. That is an estimate, not a measurement, and storage and egress costs were not estimated.

### The competitive landscape

All prices below should be rechecked on the vendor page before you rely on them; several sources conflict.

| App | What it does | Price anchor |
|---|---|---|
| Polycam | LiDAR mesh, photogrammetry, splats; floor plans on Business | Free; $150/yr Basic; $400/yr Business ([pricing](https://poly.cam/pricing)) |
| Scaniverse (Niantic Spatial) | Meshes and splats, processed on device (vendor claim) | Free; paid cloud tiers reported only by a competitor ([Niantic](https://nianticspatial.com/products/scaniverse)) |
| 3D Scanner App | LiDAR mesh, RoomPlan mode, wide export including DXF floor plans | Free with in-app purchases ([App Store](https://apps.apple.com/us/app/-/id1419913995)) |
| Canvas | Free scanning; paid human or cloud "Scan To CAD" | From $0.18/sq ft for 2D, $0.26/sq ft for 3D ([Canvas](https://support.canvas.io/article/615-how-does-scan-to-cad-pricing-work)) |
| magicplan | RoomPlan-based floor plans for trades | Per-project packages, per a secondary source ([directory](https://www.ki-syndikat.de/tools/magicplan/)) |
| KIRI Engine | LiDAR, photogrammetry; splats on Pro | $79.99/yr Pro ([KIRI](https://www.kiriengine.app/Pricing)) |
| RoomScan Pro | Floor plans with DXF and IFC export | Subscription; 2026 price not found ([App Store](https://apps.apple.com/us/app/-/id1504050801)) |
| Matterport | Hosted virtual tours | $0 to $309/month per a reseller guide ([guide](https://thefuture3d.com/blog/matterport-pricing-guide-2026/)) |

The pattern matters more than the individual prices. Scaniverse sets the price of "scan to mesh or splat" at zero, so a small team cannot compete as a general scanner (researchers' inference). The paid value everywhere sits in floor plans, measurements, CAD output and team workflow. Both of your reference videos are in that paid category. The internal technology of these apps could not be confirmed from any engineering source.

### What accuracy to expect

Independent studies, all on 2020 to 2022 hardware and read only as search summaries, report room-scale errors of about 1 to 4 cm: ±1.41 cm with one app versus ±3.94 cm with another on the same iPhone 12 Pro ([study record](https://avesis.erciyes.edu.tr/yayin/a99156bd-ffe3-4421-af12-b3fd8af6118e/investigation-of-geometric-object-and-indoor-mapping-capacity-of-apple-iphone-12-pro-lidar)), and about ±1 to 1.5 cm for iPad Pro against a professional laser scanner ([study record](https://acikerisim.ohu.edu.tr/items/d3d4a920-e7f5-42bf-88a4-5953b585f7a3)). The gap between apps shows accuracy belongs to the pipeline, not the sensor. For RoomPlan specifically there are only anecdotes: one forum user says it "seems to often work to 15cm" ([Apple forums](https://developer.apple.com/forums/thread/712500)), and a Rhino plug-in developer reports 1 to 4 inches and advises against construction use without checking ([McNeel forum](https://discourse.mcneel.com/t/introducing-room-scan/152378)). Vendor claims are looser still: Canvas says "up to 99% accuracy" ([Canvas](https://canvas.io/support/what-does-it-cost)), and magicplan gives no figure and recommends a Bluetooth laser measurer ([magicplan](https://help.magicplan.app/how-accurate-is-magicplan)). No peer-reviewed RoomPlan evaluation with numbers was found, and no independent data exists for iPhone 15 to 17 Pro.

The researchers' judgement from these numbers is that iPhone LiDAR alone is adequate for real-estate plans, interior layout, visualisation and material estimates, and **not adequate for fitted kitchens, wardrobes or worktops that need 1 to 3 mm**, nor for permit drawings. If your customers are in fitted furniture, the scan's value is capturing layout, openings and context in one visit, and the product should let the user override or pin critical dimensions from a laser measure. Run your own measurement study before making any accuracy claim; the repo's [DEVICE_TESTING.md](https://github.com/utkarsh23153124-boop/Lidar_tech/blob/main/docs/DEVICE_TESTING.md) already proposes one across 10 to 20 rooms.

The main engineering risks reported by developers are tracking drift on scans larger than a room, where an Apple forum responder advises checking `ARCamera.trackingState` and discarding poor frames ([Apple forums 788370](https://developer.apple.com/forums/thread/788370)), and errors that compound when separately scanned rooms are stitched ([Captur3D](https://support.captur3d.io/hc/en-us/articles/360045839172-Why-is-my-floor-plan-not-accurate)). One unconfirmed forum post describes a drift regression on LiDAR devices from iOS 26.4 ([Apple forums 827240](https://developer.apple.com/forums/thread/827240)); treat it as a thing to test, not a fact.

## Recommended build: keep the repo, fix it, test on a device, then decide

### Architecture

This is our recommendation, built from the researchers' inferences; it is not something any source prescribes.

**Keep the existing repo and its Capacitor shell for the parametric track.** The pattern is correct, the bridge carries only a few kilobytes, and the editor, exports and sync server already exist. All capture stays native Swift; never pass frames or meshes across the bridge as JSON. Raise the deployment target to iOS 17 to get floors, multi-room and shared sessions.

**Structure the native side around one ARSession.** RoomPlan runs on it for structure and measurements. In parallel, a recorder writes a raw bundle to disk: colour frames or video, the `sceneDepth` map and confidence map per keyframe, camera intrinsics and pose, tracking state, ARKit mesh anchors, and the `CapturedRoom` JSON. The pipelines researcher suggests a nerfstudio-compatible layout so that server training is almost direct. Whether all of this runs smoothly together is unbenchmarked, so measure heat and frame rate early.

**Deliver two artefacts if you go photoreal later:** the RoomPlan model for measuring and the floor plan, and a mesh or splat for looking. Competitors are converging on this pairing. The cloud side would be Open3D for fusion and meshing and gsplat with depth supervision for splats, with GLB and USDZ for meshes and SPZ or PLY for splats. If the photoreal track becomes the main product, the auditor's view is that a native SwiftUI app with RealityKit or Quick Look becomes simpler than a web view plus three.js; the Swift scan controller ports over with small changes.

### Phased plan

Every duration here is an estimate. Phase 0 uses the repo's own 1 to 2 week figure. The phase 1 and phase 3 figures come from the pipelines researcher's plan for a team of two or three, were made for a from-scratch native build, and are unvalidated; the phase 2 figure is our own guess. No public account of a comparable small-team build was found.

| Phase | Scope | Rough effort (estimate) | Exit test |
|---|---|---|---|
| **0. Prove it runs** | Get a Mac and a LiDAR iPhone. Change the scene delegate to `RoomformViewController()`. Open in Xcode, resolve packages, fix compile errors, set the signing team. Scan real rooms and save the real RoomPlan JSON as test fixtures. Measure three known rooms against a laser measure. In parallel, scan the same room with the Stray Scanner and RTAB-Map apps to see what a photoreal track would add | 1 to 2 weeks | A scanned room appears in the editor; an error table for your own rooms; **the parametric versus photoreal decision is made** |
| **1. Match the videos** | iOS 17 target. Use RoomPlan floors instead of wall chaining. Add multi-room with `StructureBuilder` and a "next room" step. Add `RoomCaptureSessionDelegate` guidance. Store relative file names and clean up scan files. Show the USDZ with Quick Look. Restructure `App.tsx` and the CSS. Strengthen the 2D plan: dimensions, room editing, laser-measure override | 6 to 8 weeks | Multi-room scan to dimensioned 2D plan and 3D model, as in both videos |
| **2. Record the raw bundle** | Move RoomPlan onto a shared ARSession. Record posed colour, depth, confidence and mesh to disk. Add thermal handling and tracking-state checks | 2 to 4 weeks, or folded into phase 1 | Every scan leaves a reprocessable bundle; no overheating in a 5-minute scan |
| **3. Photoreal, only if chosen** | Upload with resumable transfer, job queue, Open3D re-fusion, gsplat training, web viewer, share links | 8 to 10 weeks | Splat and mesh returned within about 30 minutes for cents of GPU |
| **4. Advanced** | Multi-room drift correction, splat-to-mesh, on-device splat training, non-LiDAR fallback, DXF or IFC export | Open-ended | Chosen by customer demand |

Phase 0 is the one that cannot be skipped or estimated away. Until it is done, every other statement about the repo's iOS side is a reading of code.

### Open gaps to close

Several things remain unknown and should be treated as tasks rather than assumptions. No RoomPlan, ARKit or Object Capture changes in iOS 26 or 27 were identified, on weak evidence. iPhone 18 Pro LiDAR presence and specifications are unconfirmed. The iOS code has never been built, so compile errors, the registration bug and real-data behaviour of the normalisation are all unproven. Cyncly's use of RoomPlan is inferred, not confirmed. There are no accuracy figures for RoomPlan from a controlled study, and none for recent iPhones. No research was done on App Store review rules, `UIRequiredDeviceCapabilities` for LiDAR, or privacy and data-residency rules for uploading home interiors, all of which matter before phase 3. Whether three.js's `USDZLoader` correctly loads RoomPlan files was not tested. Competitor prices conflict across sources. And no licence was read in full.

## Conclusion

The research changes the question. It began as "how do we build a LiDAR room scanner" and the answer is that Apple already built the scanner, your repo already wraps it, and both of your reference products are thin layers of trade-specific value on top of it. The scarce thing is not scanning technology. It is a working device build, a good 2D plan and editing experience, and an honest position on accuracy. The open-source LiDAR world that awesome-lidar catalogues is mostly solving a problem (finding the sensor's position) that ARKit hands you for free.

The other shift is in how to think about photoreal output. It is tempting to see it as the "better" version of the same feature, but it is a separate product with a server, a different viewer and a market where the leading app is free. Recording raw data from the start keeps that option open at low cost while you find out whether your customers want a room they can measure or a room they can look at. A Mac, a Pro iPhone and one changed line in the scene delegate will tell you more in the first week than any further desk research can.
