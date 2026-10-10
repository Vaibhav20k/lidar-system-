import UIKit
import Capacitor
import RoomPlan
import AVFoundation
import ARKit
import CoreImage
import ImageIO
import simd

class RoomformViewController: CAPBridgeViewController {
    override func capacitorDidLoad() {
        bridge?.registerPluginInstance(RoomScannerPlugin())
    }
}

@objc(RoomScannerPlugin)
public class RoomScannerPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "RoomScannerPlugin"
    public let jsName = "RoomScanner"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "capabilities", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "scan", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "shareOriginal", returnType: CAPPluginReturnPromise)
    ]
    private var scanning = false

    @objc func capabilities(_ call: CAPPluginCall) {
        if #available(iOS 16.0, *) {
            let supported = RoomCaptureSession.isSupported
            call.resolve(["supported": supported, "platform": "ios", "mode": supported ? "roomplan" : "manual",
                          "reason": supported ? "LiDAR room scanning available" : "Room scanning requires a supported LiDAR iPhone or iPad. Use manual room entry on this device."])
        } else {
            call.resolve(["supported": false, "platform": "ios", "mode": "manual", "reason": "Room scanning requires iOS 16 or later and LiDAR."])
        }
    }

    @objc func scan(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            guard #available(iOS 16.0, *), RoomCaptureSession.isSupported else {
                call.reject("This device does not support LiDAR room scanning."); return
            }
            guard !self.scanning else { call.reject("A scan is already in progress."); return }
            self.scanning = true
            AVCaptureDevice.requestAccess(for: .video) { granted in
                DispatchQueue.main.async {
                    guard granted else {
                        self.scanning = false
                        call.reject("Camera access is required. Allow it in Settings > Roomform > Camera."); return
                    }
                    guard let presenter = self.bridge?.viewController, presenter.presentedViewController == nil else {
                        self.scanning = false; call.reject("Close the current native screen before scanning."); return
                    }
                    let scanner = RoomScanController()
                    scanner.recordRaw = call.getBool("recordRaw", true)
                    scanner.completion = { result in
                        self.scanning = false
                        switch result {
                        case .success(let captures): call.resolve(["captures": captures])
                        case .failure(let error): call.reject(error.localizedDescription)
                        }
                    }
                    scanner.modalPresentationStyle = .fullScreen
                    DispatchQueue.global(qos: .utility).async { Self.pruneRawBundles() }
                    presenter.present(scanner, animated: true)
                }
            }
        }
    }

    @objc func shareOriginal(_ call: CAPPluginCall) {
        DispatchQueue.main.async {
            guard let path = call.getString("path"), let presenter = self.bridge?.viewController else {
                call.reject("No original scan file is available."); return
            }
            let url = URL(fileURLWithPath: path).standardizedFileURL
            let root = Self.scanDirectory.standardizedFileURL.path + "/"
            guard url.path.hasPrefix(root), url.pathExtension == "usdz", FileManager.default.fileExists(atPath: url.path) else {
                call.reject("The original scan file is no longer on this device."); return
            }
            let sheet = UIActivityViewController(activityItems: [url], applicationActivities: nil)
            sheet.popoverPresentationController?.sourceView = presenter.view
            sheet.popoverPresentationController?.sourceRect = CGRect(x: presenter.view.bounds.midX, y: presenter.view.bounds.midY, width: 1, height: 1)
            sheet.completionWithItemsHandler = { _, _, _, error in
                if let error = error { call.reject(error.localizedDescription) } else { call.resolve() }
            }
            presenter.present(sheet, animated: true)
        }
    }

    /// Raw bundles are large and only needed for re-processing, so only the newest ones are kept.
    static func pruneRawBundles(keeping limit: Int = 12) {
        guard let items = try? FileManager.default.contentsOfDirectory(at: scanDirectory, includingPropertiesForKeys: [.creationDateKey]) else { return }
        let created = { (url: URL) -> Date in (try? url.resourceValues(forKeys: [.creationDateKey]).creationDate) ?? .distantPast }
        let bundles = items.filter { $0.lastPathComponent.hasSuffix("-raw") }.sorted { created($0) > created($1) }
        for old in bundles.dropFirst(limit) { try? FileManager.default.removeItem(at: old) }
    }

    static var scanDirectory: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("RoomformScans", isDirectory: true)
    }
}

@available(iOS 16.0, *)
private final class RoomScanController: UIViewController, RoomCaptureViewDelegate {
    static let maxRooms = 12
    var completion: ((Result<[[String: Any]], Error>) -> Void)?
    var recordRaw = true
    private enum Phase { case scanning, processing, reviewing }
    private let captureView = RoomCaptureView(frame: .zero)
    private let leftButton = UIButton(type: .system), rightButton = UIButton(type: .system)
    private var phase = Phase.scanning
    private var finished = false
    private var timeout: Timer?
    private var roomId = UUID().uuidString
    private var recorder: RawCaptureRecorder?
    private var captures: [[String: Any]] = []
    // From iOS 17 the ARSession keeps running between rooms, so every room shares one coordinate space.
    private var sharedSpace: Bool { if #available(iOS 17.0, *) { return true } else { return false } }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        captureView.delegate = self
        captureView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(captureView)
        leftButton.addTarget(self, action: #selector(leftTapped), for: .touchUpInside)
        rightButton.addTarget(self, action: #selector(rightTapped), for: .touchUpInside)
        let bar = UIStackView(arrangedSubviews: [leftButton, rightButton])
        bar.axis = .horizontal; bar.distribution = .fillEqually; bar.spacing = 20
        bar.backgroundColor = .secondarySystemBackground
        bar.layer.cornerRadius = 16
        bar.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(bar)
        NSLayoutConstraint.activate([
            captureView.topAnchor.constraint(equalTo: view.topAnchor), captureView.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            captureView.leadingAnchor.constraint(equalTo: view.leadingAnchor), captureView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            bar.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 20),
            bar.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -20),
            bar.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -12), bar.heightAnchor.constraint(equalToConstant: 54)
        ])
        NotificationCenter.default.addObserver(self, selector: #selector(interrupted), name: UIApplication.didEnterBackgroundNotification, object: nil)
        startRoom()
    }

    private func startRoom() {
        roomId = UUID().uuidString
        phase = .scanning; updateBar()
        captureView.captureSession.run(configuration: RoomCaptureSession.Configuration())
        if recordRaw {
            // Raw recording is best-effort: a failure here must never block the RoomPlan scan itself.
            let folder = RoomScannerPlugin.scanDirectory.appendingPathComponent(roomId + "-raw", isDirectory: true)
            recorder = try? RawCaptureRecorder(session: captureView.captureSession.arSession, folder: folder)
            recorder?.start()
        }
    }

    private func updateBar() {
        switch phase {
        case .scanning:
            leftButton.setTitle(captures.isEmpty ? "Cancel" : "Stop here", for: .normal); leftButton.isEnabled = true
            rightButton.setTitle("Finish room", for: .normal); rightButton.isEnabled = true
        case .processing:
            leftButton.isEnabled = false
            rightButton.setTitle("Building room…", for: .normal); rightButton.isEnabled = false
        case .reviewing:
            leftButton.setTitle("Next room", for: .normal); leftButton.isEnabled = captures.count < Self.maxRooms
            rightButton.setTitle(captures.count == 1 ? "Done" : "Done (\(captures.count) rooms)", for: .normal); rightButton.isEnabled = true
        }
    }

    @objc private func leftTapped() {
        guard !finished else { return }
        if phase == .scanning { endScan("Scan cancelled") } else if phase == .reviewing { startRoom() }
    }
    @objc private func rightTapped() {
        guard !finished else { return }
        if phase == .scanning { finishRoom() } else if phase == .reviewing { finish(.success(captures)) }
    }
    private func finishRoom() {
        phase = .processing; updateBar()
        recorder?.stop()
        if #available(iOS 17.0, *) { captureView.captureSession.stop(pauseARSession: false) } else { captureView.captureSession.stop() }
        timeout = Timer.scheduledTimer(withTimeInterval: 90, repeats: false) { [weak self] _ in
            self?.endScan("Room processing timed out. Please scan again.")
        }
    }
    @objc private func interrupted() { endScan("Scan interrupted when the app entered the background. Please scan again.") }

    /// Ends the whole session. Rooms already captured are kept; only the room in progress is lost.
    private func endScan(_ message: String) {
        if captures.isEmpty { fail(message) } else { finish(.success(captures)) }
    }
    private func fail(_ message: String) {
        finish(.failure(NSError(domain: "Roomform", code: 1, userInfo: [NSLocalizedDescriptionKey: message])))
    }
    private func finish(_ result: Result<[[String: Any]], Error>) {
        DispatchQueue.main.async {
            guard !self.finished else { return }
            self.finished = true; self.timeout?.invalidate()
            // A recorder still present here belongs to a room that never completed.
            self.recorder?.discard(); self.recorder = nil
            self.captureView.captureSession.stop()
            self.dismiss(animated: true) { self.completion?(result); self.completion = nil }
        }
    }
    /// The current room could not be built. With earlier rooms in hand, keep them and let the user continue.
    private func roomFailed(_ message: String) {
        timeout?.invalidate()
        recorder?.discard(); recorder = nil
        guard !captures.isEmpty else { fail(message); return }
        phase = .reviewing; updateBar()
        let alert = UIAlertController(title: "Room not saved", message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default))
        present(alert, animated: true)
    }

    func captureView(shouldPresent roomDataForProcessing: CapturedRoomData, error: Error?) -> Bool {
        if let error = error { if !finished { roomFailed(error.localizedDescription) }; return false }
        return !finished
    }
    func captureView(didPresent processedResult: CapturedRoom, error: Error?) {
        guard !finished, phase == .processing else { return }
        timeout?.invalidate()
        if let error = error { roomFailed(error.localizedDescription); return }
        guard processedResult.walls.count >= 3 else { roomFailed("Too few walls detected. Scan every wall, including corners, then finish."); return }
        do {
            let folder = RoomScannerPlugin.scanDirectory
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let id = roomId
            let usdz = folder.appendingPathComponent(id).appendingPathExtension("usdz")
            try processedResult.export(to: usdz, exportOptions: .parametric)
            var openings = processedResult.doors.map { surface($0, kind: "door") }
            openings += processedResult.windows.map { surface($0, kind: "window") }
            openings += processedResult.openings.map { surface($0, kind: "opening") }
            var capture: [String: Any] = [
                "kind": "roomplan", "walls": processedResult.walls.map { surface($0) }, "openings": openings,
                "objects": processedResult.objects.map { object -> [String: Any] in
                    ["id": object.identifier.uuidString, "transform": matrix(object.transform), "width": object.dimensions.x,
                     "height": object.dimensions.y, "depth": object.dimensions.z, "confidence": String(describing: object.confidence),
                     "category": String(describing: object.category)]
                }, "usdzPath": usdz.path, "roomIndex": captures.count, "sharedSpace": sharedSpace
            ]
            if #available(iOS 17.0, *) {
                capture["floors"] = processedResult.floors.map { floor -> [String: Any] in
                    var value = surface(floor)
                    value["corners"] = floor.polygonCorners.map { [$0.x, $0.y, $0.z] }
                    return value
                }
            }
            if let raw = recorder?.finish(scanId: id) { capture.merge(raw) { _, new in new } }
            recorder = nil
            // Keep raw data alongside USDZ so a normalization failure does not destroy the measurement.
            let json = try JSONSerialization.data(withJSONObject: capture, options: [.prettyPrinted, .sortedKeys])
            try json.write(to: folder.appendingPathComponent(id).appendingPathExtension("json"), options: .atomic)
            captures.append(capture)
            phase = .reviewing; updateBar()
        } catch { roomFailed(error.localizedDescription) }
    }
    private func surface(_ value: CapturedRoom.Surface, kind: String? = nil) -> [String: Any] {
        var result: [String: Any] = ["id": value.identifier.uuidString, "transform": matrix(value.transform),
                                   "width": value.dimensions.x, "height": value.dimensions.y, "depth": max(0, value.dimensions.z),
                                   "confidence": String(describing: value.confidence)]
        if let kind = kind { result["kind"] = kind }
        if #available(iOS 17.0, *), let parent = value.parentIdentifier { result["parentId"] = parent.uuidString }
        return result
    }
    private func matrix(_ value: simd_float4x4) -> [Float] {
        (0..<4).flatMap { column in (0..<4).map { row in value[column][row] } }
    }
    deinit { timeout?.invalidate(); NotificationCenter.default.removeObserver(self) }
}

/// Saves posed RGB + LiDAR depth keyframes from the ARSession RoomPlan is driving, so a scan can be
/// re-processed later (texturing, vision models) without asking the user to scan again.
/// Polls `currentFrame` instead of becoming the ARSession delegate, which RoomPlan owns.
@available(iOS 16.0, *)
private final class RawCaptureRecorder {
    static let maxFrames = 240
    private let folder: URL
    private weak var session: ARSession?
    private let queue = DispatchQueue(label: "roomform.raw-capture", qos: .utility)
    private let context = CIContext()
    private let index: FileHandle
    private var timer: Timer?
    // Main thread only.
    private var busy = false, attempts = 0
    private var lastPose: simd_float4x4?
    // `queue` only.
    private var saved = 0, depthSaved = 0, depthSize: [Int] = []

    init(session: ARSession, folder: URL) throws {
        self.session = session; self.folder = folder
        for name in ["frames", "depth", "confidence"] {
            try FileManager.default.createDirectory(at: folder.appendingPathComponent(name, isDirectory: true), withIntermediateDirectories: true)
        }
        let file = folder.appendingPathComponent("frames.jsonl")
        guard FileManager.default.createFile(atPath: file.path, contents: nil) else { throw CocoaError(.fileWriteUnknown) }
        index = try FileHandle(forWritingTo: file)
    }

    func start() {
        timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in self?.sample() }
    }
    func stop() { timer?.invalidate(); timer = nil }

    private func sample() {
        guard !busy, attempts < Self.maxFrames, let frame = session?.currentFrame else { return }
        guard case .normal = frame.camera.trackingState else { return }
        let pose = frame.camera.transform
        if let last = lastPose {
            // Keyframe only after ~25 cm of travel or ~15 degrees of turn, to bound storage.
            let moved = simd_distance(last.columns.3, pose.columns.3) > 0.25
            let turned = simd_dot(simd_make_float3(last.columns.2), simd_make_float3(pose.columns.2)) < 0.966
            guard moved || turned else { return }
        }
        lastPose = pose; busy = true; attempts += 1
        let number = attempts
        // Holds one ARFrame at a time; `busy` stops a backlog that would starve ARKit of buffers.
        queue.async { [weak self] in
            self?.write(frame, number: number)
            DispatchQueue.main.async { self?.busy = false }
        }
    }

    private func write(_ frame: ARFrame, number: Int) {
        let name = String(format: "%06d", number)
        guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
              let jpeg = context.jpegRepresentation(of: CIImage(cvPixelBuffer: frame.capturedImage), colorSpace: colorSpace,
                                                    options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.75]),
              (try? jpeg.write(to: folder.appendingPathComponent("frames/\(name).jpg"), options: .atomic)) != nil else { return }
        var hasDepth = false
        if let depth = frame.sceneDepth, (try? bytes(depth.depthMap).write(to: folder.appendingPathComponent("depth/\(name).bin"), options: .atomic)) != nil {
            hasDepth = true
            depthSize = [CVPixelBufferGetWidth(depth.depthMap), CVPixelBufferGetHeight(depth.depthMap)]
            if let confidence = depth.confidenceMap {
                try? bytes(confidence).write(to: folder.appendingPathComponent("confidence/\(name).bin"), options: .atomic)
            }
        }
        let camera = frame.camera, m = camera.transform, k = camera.intrinsics
        let record: [String: Any] = [
            "frame": name, "timestamp": frame.timestamp, "depth": hasDepth,
            "cameraToWorld": (0..<4).flatMap { column in (0..<4).map { row in m[column][row] } },
            "intrinsics": (0..<3).flatMap { column in (0..<3).map { row in k[column][row] } },
            "imageWidth": Int(camera.imageResolution.width), "imageHeight": Int(camera.imageResolution.height)
        ]
        guard var line = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]) else { return }
        line.append(0x0A)
        guard (try? index.write(contentsOf: line)) != nil else { return }
        saved += 1
        if hasDepth { depthSaved += 1 }
    }

    // Copies row by row because pixel buffer rows can be padded beyond width * bytesPerPixel.
    private func bytes(_ buffer: CVPixelBuffer) -> Data {
        CVPixelBufferLockBaseAddress(buffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { return Data() }
        let height = CVPixelBufferGetHeight(buffer), stride = CVPixelBufferGetBytesPerRow(buffer)
        let rowBytes = CVPixelBufferGetWidth(buffer) * (CVPixelBufferGetPixelFormatType(buffer) == kCVPixelFormatType_DepthFloat32 ? 4 : 1)
        var data = Data(capacity: rowBytes * height)
        for row in 0..<height {
            data.append(base.advanced(by: row * stride).assumingMemoryBound(to: UInt8.self), count: rowBytes)
        }
        return data
    }

    /// Writes the manifest and returns the fields to merge into the capture, or nil if nothing usable was recorded.
    func finish(scanId: String) -> [String: Any]? {
        stop()
        var counts = (frames: 0, depth: 0, size: [Int]())
        queue.sync { counts = (saved, depthSaved, depthSize); try? index.close() }
        guard counts.frames > 0 else { discard(); return nil }
        let manifest: [String: Any] = [
            "version": 1, "scanId": scanId, "createdAt": ISO8601DateFormatter().string(from: Date()),
            "device": UIDevice.current.model, "system": UIDevice.current.systemVersion,
            "frames": counts.frames, "depthFrames": counts.depth, "depthSize": counts.size,
            "image": "frames/<frame>.jpg, sensor-native landscape orientation; intrinsics apply to this unrotated image",
            "depthFormat": "depth/<frame>.bin, little-endian float32 meters, row-major, depthSize = [width, height]",
            "confidenceFormat": "confidence/<frame>.bin, uint8 per pixel: 0 low, 1 medium, 2 high",
            "index": "frames.jsonl, one JSON object per saved frame",
            "pose": "cameraToWorld is a column-major 4x4 in the ARKit world space of the RoomPlan session (meters, Y up)",
            "intrinsicsFormat": "column-major 3x3 in pixels at imageWidth x imageHeight; scale to depthSize for depth"
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: manifest, options: [.prettyPrinted, .sortedKeys]),
              (try? data.write(to: folder.appendingPathComponent("manifest.json"), options: .atomic)) != nil else { discard(); return nil }
        return ["rawPath": folder.path, "rawFrames": counts.frames, "rawDepthFrames": counts.depth]
    }

    func discard() {
        stop()
        let folder = self.folder
        queue.async { try? FileManager.default.removeItem(at: folder) }
    }
}
