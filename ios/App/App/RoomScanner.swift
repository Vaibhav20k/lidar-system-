import UIKit
import Capacitor
import RoomPlan
import AVFoundation
import ARKit
import CoreImage
import ImageIO
import simd
import SceneKit

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
        CAPPluginMethod(name: "shareOriginal", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "enhance", returnType: CAPPluginReturnPromise)
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
                    // Preserve raw scans for repeatability checks. Never prune saved captures automatically.
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

    /// Sends a scan's raw bundle to a Roomform server, waits while it measures the ceiling and looks for fixtures,
    /// and resolves with the enriched room. Progress is reported through "enhanceProgress" events.
    @objc func enhance(_ call: CAPPluginCall) {
        guard let rawPath = call.getString("rawPath"), let base = call.getString("url"), let server = URL(string: base),
              let payload = call.getString("payload")?.data(using: .utf8) else {
            call.reject("The scan or the server address is missing."); return
        }
        let raw = URL(fileURLWithPath: rawPath).standardizedFileURL
        let root = Self.scanDirectory.standardizedFileURL.path + "/"
        guard raw.path.hasPrefix(root), raw.lastPathComponent.hasSuffix("-raw"), FileManager.default.fileExists(atPath: raw.path) else {
            call.reject("The raw scan data is no longer on this device."); return
        }
        let id = String(raw.lastPathComponent.dropLast("-raw".count)), token = call.getString("token") ?? ""
        Task.detached(priority: .userInitiated) {
            // Processing takes minutes; a sleeping phone would drop the connection.
            await MainActor.run { UIApplication.shared.isIdleTimerDisabled = true }
            do {
                let result = try await Self.process(scan: id, raw: raw, server: server, token: token, payload: payload) { stage in
                    self.notifyListeners("enhanceProgress", data: ["stage": stage])
                }
                call.resolve(result)
            } catch { call.reject(error.localizedDescription) }
            await MainActor.run { UIApplication.shared.isIdleTimerDisabled = false }
        }
    }

    private static func failure(_ message: String) -> NSError {
        NSError(domain: "Roomform", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }

    private static func process(scan id: String, raw: URL, server: URL, token: String, payload: Data, progress: @escaping (String) -> Void) async throws -> [String: Any] {
        func request(_ path: String, _ method: String) -> URLRequest {
            var value = URLRequest(url: server.appendingPathComponent(path))
            value.httpMethod = method; value.timeoutInterval = 600
            if !token.isEmpty { value.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
            return value
        }
        func body(_ data: Data, _ response: URLResponse) throws -> [String: Any] {
            let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                throw failure(json["error"] as? String ?? "The server could not process this scan.")
            }
            return json
        }
        progress("Preparing scan data")
        let archive = try zip(raw, named: id)
        defer { try? FileManager.default.removeItem(at: archive) }
        progress("Sending scan to the server")
        var upload = request("api/scans/\(id)/raw", "PUT")
        upload.setValue("application/zip", forHTTPHeaderField: "Content-Type")
        let sent = try await URLSession.shared.upload(for: upload, fromFile: archive)
        _ = try body(sent.0, sent.1)
        var start = request("api/scans/\(id)/process", "POST")
        start.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let started = try await URLSession.shared.upload(for: start, from: payload)
        _ = try body(started.0, started.1)
        for _ in 0..<600 {  // three seconds apart: up to half an hour
            try await Task.sleep(nanoseconds: 3_000_000_000)
            let polled = try await URLSession.shared.data(for: request("api/scans/\(id)", "GET"))
            let job = try body(polled.0, polled.1)
            if let stage = job["stage"] as? String { progress(stage) }
            switch job["status"] as? String {
            case "done":
                guard let room = job["room"] as? [String: Any] else { throw failure("The server returned no room.") }
                return ["room": room, "notes": job["notes"] as? [String] ?? []]
            case "failed": throw failure(job["error"] as? String ?? "Processing failed on the server.")
            default: continue
            }
        }
        throw failure("The server took too long to process this scan.")
    }

    /// Reading a folder "for uploading" makes the system hand over a zip of it.
    private static func zip(_ folder: URL, named name: String) throws -> URL {
        var coordination: NSError?
        var result: Result<URL, Error> = .failure(failure("The scan data could not be packed."))
        NSFileCoordinator().coordinate(readingItemAt: folder, options: [.forUploading], error: &coordination) { zipped in
            let target = FileManager.default.temporaryDirectory.appendingPathComponent("\(name).zip")
            try? FileManager.default.removeItem(at: target)
            result = Result { try FileManager.default.copyItem(at: zipped, to: target); return target }
        }
        if let coordination = coordination { throw coordination }
        return try result.get()
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
    private enum Phase { case scanning, processing, wallPhotos, reviewing }
    private let captureView = RoomCaptureView(frame: .zero)
    private let leftButton = UIButton(type: .system), rightButton = UIButton(type: .system)
    private var phase = Phase.scanning
    private var finished = false
    private var timeout: Timer?
    private var roomId = UUID().uuidString
    private var recorder: RawCaptureRecorder?
    private var captures: [[String: Any]] = []
    private var wallPass: AnyObject?  // WallPhotoPass, iOS 17 only
    private var wallPassCapture: (index: Int, json: URL)?
    private let hint = UILabel()
    private let measuring = DispatchGroup()   // ceilings being measured in the background
    private var ceilingBudgets: [Int: CeilingWorkBudget] = [:]
    private var ceilingFiles: [Int: URL] = [:]
    private var finishDeadline: DispatchWorkItem?
    private var finishResult: Result<[[String: Any]], Error>?
    private var finishing = false              // finish() has started; nothing new may begin
    private var spaceRestarted = false         // the photo step's camera switch restarted ARKit's coordinates
    // From iOS 17 the ARSession keeps running between rooms, so every room shares one coordinate space, unless the
    // coordinates were restarted (rooms scanned after that are in a new space).
    private var sharedSpace: Bool { if #available(iOS 17.0, *) { return !spaceRestarted } else { return false } }

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
        hint.translatesAutoresizingMaskIntoConstraints = false
        hint.numberOfLines = 0; hint.textAlignment = .center; hint.font = .preferredFont(forTextStyle: .headline)
        hint.textColor = .white; hint.backgroundColor = UIColor.black.withAlphaComponent(0.6)
        hint.layer.cornerRadius = 12; hint.clipsToBounds = true; hint.isHidden = true
        view.addSubview(hint)
        NSLayoutConstraint.activate([
            hint.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 12),
            hint.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 16),
            hint.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -16)
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
            recorder?.onGuidance = { [weak self] text in
                guard let self = self, self.phase == .scanning else { return }
                self.hint.isHidden = false; self.hint.text = "  " + text + "  "
            }
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
        case .wallPhotos:
            leftButton.setTitle("Skip photos", for: .normal); leftButton.isEnabled = true
            rightButton.setTitle("Photos done", for: .normal); rightButton.isEnabled = true
        case .reviewing:
            leftButton.setTitle("Next room", for: .normal); leftButton.isEnabled = captures.count < Self.maxRooms
            rightButton.setTitle(captures.count == 1 ? "Done" : "Done (\(captures.count) rooms)", for: .normal); rightButton.isEnabled = true
        }
    }

    @objc private func leftTapped() {
        if finishing && !finished { completeFinish(deferred: true); return }
        guard !finished, !finishing else { return }
        if phase == .scanning { endScan("Scan cancelled") } else if phase == .wallPhotos { finishWallPhotos() } else if phase == .reviewing { startRoom() }
    }
    @objc private func rightTapped() {
        guard !finished, !finishing else { return }
        if phase == .scanning { finishRoom() } else if phase == .wallPhotos { finishWallPhotos() } else if phase == .reviewing { finish(.success(captures)) }
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
        guard !finishing else { return }
        finishing = true; finishResult = result
        if wallPass != nil { finishWallPhotos() }
        // Free the camera/GPU while CPU depth processing finishes. Never make
        // saving an already-persisted room depend on an unbounded worker.
        recorder?.discard(); recorder = nil
        captureView.captureSession.stop()
        rightButton.setTitle("Measuring ceiling…", for: .normal); rightButton.isEnabled = false
        leftButton.setTitle("Save without ceiling", for: .normal); leftButton.isEnabled = true
        hint.isHidden = false; hint.text = "Room saved. Ceiling processing will wait at most 15 seconds."
        let deadline = DispatchWorkItem { [weak self] in self?.completeFinish(deferred: true) }
        finishDeadline = deadline
        DispatchQueue.main.asyncAfter(deadline: .now() + 15, execute: deadline)
        measuring.notify(queue: .main) { [weak self] in self?.completeFinish(deferred: false) }
    }

    private func completeFinish(deferred: Bool) {
        guard !finished, let initial = finishResult else { return }
        finished = true; finishDeadline?.cancel(); finishDeadline = nil
        timeout?.invalidate()
        for budget in ceilingBudgets.values { budget.cancel() }
        if deferred {
            for (index, url) in ceilingFiles where index < captures.count && captures[index]["ceiling"] == nil {
                captures[index]["ceilingStatus"] = "deferred"
                if let data = try? JSONSerialization.data(withJSONObject: captures[index], options: [.prettyPrinted, .sortedKeys]) {
                    try? data.write(to: url, options: .atomic)
                }
            }
        }
        let result: Result<[[String: Any]], Error> = { if case .success = initial { return .success(captures) }; return initial }()
        finishResult = nil
        dismiss(animated: true) { self.completion?(result); self.completion = nil }
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
        guard !finished, !finishing, phase == .processing else { return }
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
                    var value: [String: Any] = [
                        "id": object.identifier.uuidString, "transform": matrix(object.transform), "width": object.dimensions.x,
                        "height": object.dimensions.y, "depth": object.dimensions.z, "confidence": String(describing: object.confidence),
                        "category": String(describing: object.category)]
                    if #available(iOS 17.0, *) {
                        // e.g. ["ChairType": "swivel", "ChairLegType": "star"]; lets the viewer pick a matching shape.
                        var attributes: [String: String] = [:]
                        for attribute in object.attributes { attributes[String(describing: type(of: attribute))] = String(describing: attribute) }
                        if !attributes.isEmpty { value["attributes"] = attributes }
                    }
                    return value
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
            let jsonURL = folder.appendingPathComponent(id).appendingPathExtension("json")
            measureCeiling(room: processedResult, index: captures.count - 1, json: jsonURL)
            if #available(iOS 17.0, *), let raw = capture["rawPath"] as? String {
                startWallPhotos(walls: processedResult.walls, raw: URL(fileURLWithPath: raw), index: captures.count - 1, json: jsonURL)
            } else {
                phase = .reviewing; updateBar()
            }
        } catch { roomFailed(error.localizedDescription) }
    }

    /// Ceiling, beams along walls and hanging fixtures from the raw LiDAR, on the phone, while the user takes wall photos.
    private func measureCeiling(room: CapturedRoom, index: Int, json: URL) {
        guard let raw = captures[index]["rawPath"] as? String else { return }
        let walls = room.walls.map { CeilingEstimator.WallSurface(id: $0.identifier.uuidString, transform: $0.transform, width: $0.dimensions.x, height: $0.dimensions.y) }
        var corners: [SIMD2<Float>] = []
        if #available(iOS 17.0, *), let floor = room.floors.max(by: { $0.polygonCorners.count < $1.polygonCorners.count }) {
            corners = floor.polygonCorners.map { p in
                let w = floor.transform * SIMD4(p.x, p.y, 0, 1)
                return SIMD2(w.x, w.z)
            }
        }
        var estimator = CeilingEstimator(walls: walls, floor: CeilingEstimator.floorOutline(walls: walls, floorCorners: corners))
        estimator.openings = (room.doors + room.windows + room.openings).map { CeilingEstimator.Opening(transform: $0.transform, width: $0.dimensions.x, height: $0.dimensions.y) }
        let budget = CeilingWorkBudget(seconds: 30)
        ceilingBudgets[index] = budget; ceilingFiles[index] = json
        captures[index]["ceilingStatus"] = "processing"
        let group = measuring, started = ProcessInfo.processInfo.systemUptime
        let diagnostics = URL(fileURLWithPath: raw).appendingPathComponent("ceiling-processing.json")
        estimator.shouldCancel = { budget.shouldStop }
        estimator.onProgress = { stage in
            let record: [String: Any] = ["stage": stage, "elapsedSeconds": ProcessInfo.processInfo.systemUptime - started]
            if let data = try? JSONSerialization.data(withJSONObject: record) { try? data.write(to: diagnostics, options: .atomic) }
        }
        group.enter()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let ceiling = estimator.estimate(raw: URL(fileURLWithPath: raw))
            let stopped = budget.shouldStop
            let status = stopped ? "deferred" : ceiling == nil ? "unavailable" : "complete"
            let record: [String: Any] = ["stage": status, "elapsedSeconds": ProcessInfo.processInfo.systemUptime - started]
            if let data = try? JSONSerialization.data(withJSONObject: record) { try? data.write(to: diagnostics, options: .atomic) }
            DispatchQueue.main.async {
                defer { group.leave() }
                guard let self = self, !self.finished, index < self.captures.count else { return }
                self.ceilingBudgets.removeValue(forKey: index)
                self.captures[index]["ceilingStatus"] = status
                if !stopped, let ceiling = ceiling { self.captures[index]["ceiling"] = ceiling }
                if let data = try? JSONSerialization.data(withJSONObject: self.captures[index], options: [.prettyPrinted, .sortedKeys]) {
                    try? data.write(to: json, options: .atomic)
                }
            }
        }
    }

    /// Straight-on, full-resolution photos of each wall while the AR session is still running in the scan's
    /// coordinates. Video keyframes are small and taken at angles; these give the detail that plate edges, window
    /// frames and narrow gaps need.
    @available(iOS 17.0, *)
    private func startWallPhotos(walls: [CapturedRoom.Surface], raw: URL, index: Int, json: URL) {
        guard !finishing else { phase = .reviewing; updateBar(); return }
        let targets = walls.filter { $0.dimensions.x >= 0.6 }.map { WallPhotoPass.Target(id: $0.identifier.uuidString, transform: $0.transform, width: $0.dimensions.x, height: $0.dimensions.y) }
        let session = captureView.captureSession.arSession
        guard !targets.isEmpty, let pass = try? WallPhotoPass(session: session, targets: targets, folder: raw) else {
            phase = .reviewing; updateBar(); return
        }
        // RoomPlan runs the camera in a video format; full-resolution stills need the format meant for them. Switching
        // can restart ARKit's coordinates, so the camera's pose just before is kept to tie the new ones back to the scan.
        if let format = ARWorldTrackingConfiguration.recommendedVideoFormatForHighResolutionFrameCapturing, let before = session.currentFrame {
            pass.realign(from: before.camera.transform, at: before.timestamp)
            let configuration = ARWorldTrackingConfiguration()
            configuration.videoFormat = format
            if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) { configuration.frameSemantics.insert(.sceneDepth) }
            session.run(configuration)
        }
        wallPass = pass; wallPassCapture = (index, json)
        pass.view.translatesAutoresizingMaskIntoConstraints = false
        view.insertSubview(pass.view, aboveSubview: captureView)
        NSLayoutConstraint.activate([
            pass.view.topAnchor.constraint(equalTo: view.topAnchor), pass.view.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            pass.view.leadingAnchor.constraint(equalTo: view.leadingAnchor), pass.view.trailingAnchor.constraint(equalTo: view.trailingAnchor)
        ])
        hint.isHidden = false
        pass.onUpdate = { [weak self] text in self?.hint.text = "  " + text + "  " }
        pass.start()
        phase = .wallPhotos; updateBar()
    }

    private func finishWallPhotos() {
        guard #available(iOS 17.0, *), let pass = wallPass as? WallPhotoPass else { return }
        let count = pass.stop()
        if pass.restarted { spaceRestarted = true }
        pass.view.removeFromSuperview()
        hint.isHidden = true
        wallPass = nil
        if let (index, json) = wallPassCapture, index < captures.count, count > 0 {
            captures[index]["rawStills"] = count
            if let data = try? JSONSerialization.data(withJSONObject: captures[index], options: [.prettyPrinted, .sortedKeys]) { try? data.write(to: json, options: .atomic) }
        }
        wallPassCapture = nil
        if !finished { phase = .reviewing; updateBar() }
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
    deinit { timeout?.invalidate(); finishDeadline?.cancel(); for budget in ceilingBudgets.values { budget.cancel() }; NotificationCenter.default.removeObserver(self) }
}

/// Guides the user to photograph each wall straight on and takes full-resolution stills automatically.
/// Walls are drawn over the camera: grey still to do, blue the one being faced, green covered. Each wall is a grid of
/// cells, 20 cm along the wall by three height bands (0.2-0.9, 0.9-1.6, 1.6-2.3 m). A still is taken automatically
/// when the phone is steady, faces a wall within ~25 degrees from 0.6-4.5 m, and would show cells not yet covered in
/// enough detail; any view fills whatever cells it shows. The shutter button takes one at any time. Each still is
/// saved with its pose and intrinsics, and with the LiDAR depth of the same moment, into <raw>/stills.
@available(iOS 17.0, *)
private final class WallPhotoPass: NSObject {
    struct Target { let id: String; let transform: simd_float4x4; let width: Float; let height: Float }
    static let maxStills = 60, strip: Float = 0.2, coveredShare: Float = 0.85
    static let bands: [Float] = [0.55, 1.25, 1.95]   // cell centres above the wall's base
    static let minDetail: Float = 800  // pixels per metre of wall a still must give for a cell to count (8 px per cm)
    let view = ARSCNView(frame: .zero)
    var onUpdate: ((String) -> Void)?
    private weak var session: ARSession?
    private let targets: [Target]
    private var covered: [[Bool]]
    private var nodes: [SCNNode] = []
    private let folder: URL, index: FileHandle
    private let queue = DispatchQueue(label: "roomform.wall-stills", qos: .userInitiated)
    private let context = CIContext()
    private let flash = UIView()
    private let shutter = UIButton(type: .custom)
    private var timer: Timer?, lastPose: simd_float4x4?, lastShot = Date.distantPast
    private var shooting = false, count = 0
    private var stillWidth: Float?   // learnt from a test still at the start
    // ARKit coordinates after the camera switch -> the scan's. Measured from the camera's pose just before the switch and
    // the first pose after it (the phone is held still in between); the Mac refines it from the stills' depth.
    private var toScan = matrix_identity_float4x4
    private var pendingAlign: (pose: simd_float4x4, time: TimeInterval)?
    private(set) var restarted = false   // the camera switch did restart the coordinates

    init(session: ARSession, targets: [Target], folder: URL) throws {
        self.session = session; self.targets = targets
        self.covered = targets.map { t in
            let strips = max(1, Int((t.width / Self.strip).rounded(.up))), rows = Self.bands.filter { $0 < t.height - 0.1 }.count
            return Array(repeating: false, count: strips * max(1, rows))
        }
        self.folder = folder.appendingPathComponent("stills", isDirectory: true)
        try FileManager.default.createDirectory(at: self.folder, withIntermediateDirectories: true)
        let file = folder.appendingPathComponent("stills.jsonl")
        guard FileManager.default.createFile(atPath: file.path, contents: nil) else { throw CocoaError(.fileWriteUnknown) }
        index = try FileHandle(forWritingTo: file)
        super.init()
        view.session = session
        view.scene = SCNScene()
        view.automaticallyUpdatesLighting = false
        for target in targets {
            let plane = SCNPlane(width: CGFloat(target.width), height: CGFloat(target.height))
            plane.firstMaterial?.isDoubleSided = true
            plane.firstMaterial?.lightingModel = .constant
            let node = SCNNode(geometry: plane)
            node.simdTransform = target.transform
            view.scene.rootNode.addChildNode(node); nodes.append(node)
        }
        flash.backgroundColor = .white; flash.alpha = 0; flash.isUserInteractionEnabled = false
        flash.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(flash)
        shutter.backgroundColor = .white
        shutter.layer.cornerRadius = 36; shutter.layer.borderWidth = 5; shutter.layer.borderColor = UIColor.black.withAlphaComponent(0.25).cgColor
        shutter.accessibilityLabel = "Take a wall photo"
        shutter.translatesAutoresizingMaskIntoConstraints = false
        shutter.addTarget(self, action: #selector(shutterTapped), for: .touchUpInside)
        view.addSubview(shutter)
        NSLayoutConstraint.activate([flash.topAnchor.constraint(equalTo: view.topAnchor), flash.bottomAnchor.constraint(equalTo: view.bottomAnchor),
                                     flash.leadingAnchor.constraint(equalTo: view.leadingAnchor), flash.trailingAnchor.constraint(equalTo: view.trailingAnchor),
                                     shutter.widthAnchor.constraint(equalToConstant: 72), shutter.heightAnchor.constraint(equalToConstant: 72),
                                     shutter.centerXAnchor.constraint(equalTo: view.centerXAnchor),
                                     shutter.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -84)])
    }

    func realign(from pose: simd_float4x4, at time: TimeInterval) { pendingAlign = (pose, time) }

    /// A camera pose in the scan's coordinates.
    private func scanPose(_ camera: ARCamera) -> simd_float4x4 { toScan * camera.transform }

    func start() {
        // A test still tells how big the stills are, so detail is judged for them and not for the smaller video frames.
        session?.captureHighResolutionFrame { [weak self] still, _ in
            if let still = still { DispatchQueue.main.async { self?.stillWidth = Float(still.camera.imageResolution.width) } }
        }
        timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in self?.tick() }
        tick()
    }

    /// Stops and returns how many stills were saved.
    func stop() -> Int {
        timer?.invalidate(); timer = nil
        queue.sync { try? index.close() }
        return count
    }

    private func share(_ wall: Int) -> Float { Float(covered[wall].filter { $0 }.count) / Float(covered[wall].count) }

    /// The wall the camera is pointed at, straight on enough to photograph.
    private func facingWall(_ camera: ARCamera) -> Int? {
        let pose = scanPose(camera), position = simd_make_float3(pose.columns.3), forward = -simd_make_float3(pose.columns.2)
        var facing: Int?, best: Float = 0.9
        for (i, t) in targets.enumerated() {
            let n = simd_make_float3(t.transform.columns.2), origin = simd_make_float3(t.transform.columns.3)
            let cosine = simd_dot(forward, -n), distance = simd_dot(position - origin, n)
            guard cosine > best, distance > 0.6, distance < 4.5 else { continue }
            let hit = position + forward * (distance / cosine) - origin
            let u = simd_dot(hit, simd_make_float3(t.transform.columns.0)), v = simd_dot(hit, simd_make_float3(t.transform.columns.1))
            guard abs(u) <= t.width / 2 + 0.2, abs(v) <= t.height / 2 + 0.3 else { continue }
            facing = i; best = cosine
        }
        return facing
    }

    private func tick() {
        guard let frame = session?.currentFrame else { return }
        if let pending = pendingAlign {
            // Wait for the first steady pose after the switch, then tie the new coordinates to the scan's.
            guard frame.timestamp > pending.time + 0.3, case .normal = frame.camera.trackingState else { onUpdate?("Hold still for a moment…"); return }
            // Gravity is the same before and after, so only a turn about the vertical and a shift are allowed: copying
            // the phone's tilt between the two moments into the tie would tip every still.
            // The turn is read from the whole relative rotation (old * new^T), which stays defined even when the phone
            // points nearly straight up or down, where a heading from the viewing direction alone would not.
            let relative = pending.pose * frame.camera.transform.inverse
            let turn = atan2(relative.columns.2.x - relative.columns.0.z, relative.columns.0.x + relative.columns.2.z)
            let before = simd_make_float3(pending.pose.columns.3), after = simd_make_float3(frame.camera.transform.columns.3)
            // No restart: the pose barely changed, so the coordinates are still the scan's and nothing needs tying.
            if simd_distance(before, after) < 0.15 && abs(turn) < 0.09 {
                pendingAlign = nil
            } else {
            restarted = true
            var tie = simd_float4x4(simd_quatf(angle: turn, axis: SIMD3(0, 1, 0)))
            let moved = SIMD3(pending.pose.columns.3.x, pending.pose.columns.3.y, pending.pose.columns.3.z)
                - simd_make_float3(tie * SIMD4(frame.camera.transform.columns.3.x, frame.camera.transform.columns.3.y, frame.camera.transform.columns.3.z, 1))
            tie.columns.3 = SIMD4(moved.x, moved.y, moved.z, 1)
            toScan = tie
            pendingAlign = nil
            for (node, target) in zip(nodes, targets) { node.simdTransform = toScan.inverse * target.transform }
            }
        }
        let camera = frame.camera, pose = scanPose(camera)
        let facing = facingWall(camera)
        let steady: Bool = {
            defer { lastPose = pose }
            guard let last = lastPose else { return false }
            return simd_distance(last.columns.3, pose.columns.3) < 0.03 && simd_dot(simd_make_float3(last.columns.2), simd_make_float3(pose.columns.2)) > 0.9986
        }()
        for (i, node) in nodes.enumerated() {
            let colour: UIColor = share(i) >= Self.coveredShare ? .systemGreen : (i == facing ? .systemBlue : .systemGray)
            node.geometry?.firstMaterial?.diffuse.contents = colour.withAlphaComponent(i == facing ? 0.35 : 0.22)
        }
        let done = targets.indices.filter { share($0) >= Self.coveredShare }.count
        let summary = "Walls photographed: \(done) of \(targets.count) · \(count) photos · or tap the button"
        guard case .normal = camera.trackingState else { onUpdate?("Hold on, tracking…\n" + summary); return }
        guard let wall = facing else { onUpdate?("Face a wall straight on, 1–3 m away\n" + summary); return }
        let (gain, tooFar) = newCells(wall: wall, camera: camera)
        if gain.isEmpty {
            let advice = share(wall) >= Self.coveredShare ? "This wall is done. Turn to the next one"
                : tooFar ? "Come a little closer to the wall" : "Point at the part of this wall not yet green"
            onUpdate?(advice + "\n" + summary)
            return
        }
        guard steady else { onUpdate?("Hold steady…\n" + summary); return }
        shoot(wall: wall, frame: frame)
    }

    @objc private func shutterTapped() {
        guard pendingAlign == nil, let frame = session?.currentFrame else { return }
        shoot(wall: facingWall(frame.camera), frame: frame, manual: true)
    }

    private func shoot(wall: Int?, frame: ARFrame, manual: Bool = false) {
        guard !shooting, count < Self.maxStills, manual || Date().timeIntervalSince(lastShot) > 1.0, let session = session else { return }
        shooting = true; lastShot = Date()
        session.captureHighResolutionFrame { [weak self] still, _ in
            DispatchQueue.main.async { self?.save(still ?? frame, depthFrame: frame, wall: wall, highResolution: still != nil, manual: manual) }
        }
    }

    /// Cells of the wall a photo from this camera would fill, and whether some were in view but too far for the detail needed.
    private func newCells(wall: Int, camera: ARCamera, width: Float? = nil, anyDetail: Bool = false) -> (cells: [Int], tooFar: Bool) {
        let t = targets[wall], size = camera.imageResolution
        let toCamera = scanPose(camera).inverse, k = camera.intrinsics
        let scale = (width ?? stillWidth ?? Float(size.width)) / Float(size.width)
        let rows = covered[wall].count / max(1, Int((t.width / Self.strip).rounded(.up))), strips = covered[wall].count / max(1, rows)
        var far = false, found: [Int] = []
        for i in 0..<strips {
            for r in 0..<rows where !covered[wall][i * rows + r] {
                let u = -t.width / 2 + (Float(i) + 0.5) * t.width / Float(strips), v = -t.height / 2 + Self.bands[r]
                let p = toCamera * (t.transform * simd_float4(u, v, 0, 1)), depth = -p.z
                guard depth > 0.3 else { continue }
                let x = k[2][0] + k[0][0] * p.x / depth, y = k[2][1] - k[1][1] * p.y / depth
                guard x > Float(size.width) * 0.03 && x < Float(size.width) * 0.97 && y > Float(size.height) * 0.03 && y < Float(size.height) * 0.97 else { continue }
                if !anyDetail && k[0][0] * scale / depth < Self.minDetail { far = true; continue }
                found.append(i * rows + r)
            }
        }
        return (found, far)
    }

    private func save(_ still: ARFrame, depthFrame: ARFrame, wall: Int?, highResolution: Bool, manual: Bool) {
        // Only a real full-resolution still says how big stills are; a video frame saved as a fallback does not.
        let width = Float(still.camera.imageResolution.width)
        if highResolution { stillWidth = width }
        if let wall = wall {
            for c in newCells(wall: wall, camera: still.camera, width: width, anyDetail: manual).cells { covered[wall][c] = true }
        }
        count += 1
        let number = count, id = wall.map { targets[$0].id } ?? ""
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        flash.alpha = 0.6
        UIView.animate(withDuration: 0.25) { self.flash.alpha = 0 }
        queue.async { [weak self] in
            self?.write(still, depthFrame: depthFrame, number: number, wallId: id, highResolution: highResolution)
            DispatchQueue.main.async { self?.shooting = false }
        }
    }

    private func write(_ still: ARFrame, depthFrame: ARFrame, number: Int, wallId: String, highResolution: Bool) {
        let name = String(format: "s%03d", number)
        guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
              let jpeg = context.jpegRepresentation(of: CIImage(cvPixelBuffer: still.capturedImage), colorSpace: colorSpace,
                                                    options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.88]),
              (try? jpeg.write(to: folder.appendingPathComponent("\(name).jpg"), options: .atomic)) != nil else { return }
        // Depth comes from the video frame of the same moment; it covers the same field of view at a lower resolution.
        var hasDepth = false, depthSize: [Int] = []
        if let depth = depthFrame.sceneDepth, (try? pixelBytes(depth.depthMap).write(to: folder.appendingPathComponent("\(name).depth.bin"), options: .atomic)) != nil {
            hasDepth = true; depthSize = [CVPixelBufferGetWidth(depth.depthMap), CVPixelBufferGetHeight(depth.depthMap)]
        }
        let camera = still.camera, m = scanPose(camera), k = camera.intrinsics, dm = scanPose(depthFrame.camera)
        let column = { (x: simd_float4x4) in (0..<4).flatMap { c in (0..<4).map { r in x[c][r] } } }
        let record: [String: Any] = [
            "frame": name, "timestamp": still.timestamp, "depth": hasDepth, "depthSize": depthSize, "wallId": wallId, "highResolution": highResolution,
            "cameraToWorld": column(m), "depthCameraToWorld": column(dm), "toScan": column(toScan),
            "intrinsics": (0..<3).flatMap { column in (0..<3).map { row in k[column][row] } },
            "imageWidth": Int(camera.imageResolution.width), "imageHeight": Int(camera.imageResolution.height)
        ]
        guard var line = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]) else { return }
        line.append(0x0A)
        try? index.write(contentsOf: line)
    }
}

/// Copies row by row because pixel buffer rows can be padded beyond width * bytesPerPixel.
private func pixelBytes(_ buffer: CVPixelBuffer) -> Data {
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

/// Saves posed RGB + LiDAR depth keyframes from the ARSession RoomPlan is driving, so a scan can be
/// re-processed later (texturing, vision models) without asking the user to scan again.
/// Polls `currentFrame` instead of becoming the ARSession delegate, which RoomPlan owns.
// Bounded viewpoint budget; mirrored by coverage.py for replay testing.
// Camera-view diversity is not a claim of complete surface coverage.
private struct CaptureSamplingPolicy {
    let primary = 360, maximum = 480
    private(set) var count = 0
    private var views: [String: (count: Int, last: TimeInterval)] = [:]
    mutating func select(_ pose: simd_float4x4, time: TimeInterval) -> Bool {
        guard count < maximum else { return false }
        let p = pose.columns.3, f = -pose.columns.2
        let key = [p.x / 0.3, p.y / 0.3, p.z / 0.3, f.x / 0.25, f.y / 0.25, f.z / 0.25].map { String(Int(floor($0))) }.joined(separator: ":")
        let prior = views[key] ?? (count: 0, last: -Double.infinity)
        let revisit = prior.count > 0 && time - prior.last >= 20
        guard prior.count < 3 || revisit else { return false }
        guard count < primary || prior.count == 0 || revisit else { return false }
        views[key] = (prior.count + 1, time); count += 1
        return true
    }
}

@available(iOS 16.0, *)
private final class RawCaptureRecorder {
    static let maxFrames = 480
    private var sampling = CaptureSamplingPolicy()
    private let folder: URL
    private weak var session: ARSession?
    private let queue = DispatchQueue(label: "roomform.raw-capture", qos: .utility)
    private let context = CIContext()
    private let index: FileHandle
    private let trackingIndex: FileHandle
    var onGuidance: ((String) -> Void)?
    private var lastTelemetry = -Double.infinity, lastSavedTime = -Double.infinity
    private var previousSample: (pose: simd_float4x4, time: TimeInterval)?
    private var limitedMappingSamples = 0, sampledFrames = 0
    private var telemetryCount = 0
    private var timer: Timer?
    // Main thread only.
    private var busy = false, attempts = 0
    // `queue` only.
    private var saved = 0, depthSaved = 0, depthSize: [Int] = []

    init(session: ARSession, folder: URL) throws {
        self.session = session; self.folder = folder
        for name in ["frames", "depth", "confidence", "features"] {
            try FileManager.default.createDirectory(at: folder.appendingPathComponent(name, isDirectory: true), withIntermediateDirectories: true)
        }
        let file = folder.appendingPathComponent("frames.jsonl")
        guard FileManager.default.createFile(atPath: file.path, contents: nil) else { throw CocoaError(.fileWriteUnknown) }
        index = try FileHandle(forWritingTo: file)
        let trackingFile = folder.appendingPathComponent("tracking.jsonl")
        guard FileManager.default.createFile(atPath: trackingFile.path, contents: nil) else { throw CocoaError(.fileWriteUnknown) }
        trackingIndex = try FileHandle(forWritingTo: trackingFile)
    }

    func start() {
        timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in self?.sample() }
    }
    func stop() { timer?.invalidate(); timer = nil }

    private func sample() {
        guard let frame = session?.currentFrame else { return }
        let tracking = Self.trackingName(frame.camera.trackingState)
        let pose = frame.camera.transform
        var speed: Float = 0, angularSpeed: Float = 0
        if let before = previousSample, frame.timestamp > before.time {
            let seconds = Float(frame.timestamp - before.time)
            speed = simd_distance(simd_make_float3(pose.columns.3), simd_make_float3(before.pose.columns.3)) / seconds
            let a = simd_float3x3(simd_make_float3(pose.columns.0), simd_make_float3(pose.columns.1), simd_make_float3(pose.columns.2))
            let b = simd_float3x3(simd_make_float3(before.pose.columns.0), simd_make_float3(before.pose.columns.1), simd_make_float3(before.pose.columns.2))
            let rotation = b.transpose * a
            angularSpeed = acos(max(-1, min(1, (rotation[0][0] + rotation[1][1] + rotation[2][2] - 1) / 2))) * 180 / .pi / seconds
        }
        previousSample = (pose, frame.timestamp)
        // Record limited/unavailable tracking too; previous bundles silently omitted it.
        if frame.timestamp - lastTelemetry >= 0.5 && telemetryCount < 7200 {
            let gap = lastTelemetry.isFinite ? frame.timestamp - lastTelemetry : 0
            lastTelemetry = frame.timestamp; telemetryCount += 1
            sampledFrames += 1
            if frame.worldMappingStatus == .limited || frame.worldMappingStatus == .notAvailable { limitedMappingSamples += 1 }
            let record: [String: Any] = ["timestamp": frame.timestamp, "tracking": tracking,
                "worldMapping": String(describing: frame.worldMappingStatus), "sampleGapSeconds": gap,
                "mappingState": Self.mappingName(frame.worldMappingStatus), "featureCount": frame.rawFeaturePoints?.points.count ?? 0,
                "speedMetersPerSecond": speed, "angularSpeedDegreesPerSecond": angularSpeed,
                "recordingLimitReached": attempts >= Self.maxFrames]
            if var data = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]) {
                data.append(0x0A)
                let line = data
                queue.async { [weak self] in try? self?.trackingIndex.write(contentsOf: line) }
            }
        }
        guard case .normal = frame.camera.trackingState else {
            onGuidance?("Tracking limited. Pause and face an area already scanned.")
            return
        }
        if attempts >= Self.maxFrames {
            onGuidance?("Depth recording is full. Finish this room; repeat-view accuracy still needs review.")
            return
        }
        if frame.worldMappingStatus == .limited || frame.worldMappingStatus == .notAvailable {
            onGuidance?("Map is weak. Pause and face a textured corner already scanned; include floor and wall together.")
        } else if angularSpeed > 20 || speed > 0.5 {
            onGuidance?("Slow down and pause at the corner before turning. Fast motion reduces repeat-view quality.")
        } else {
            onGuidance?("Cover floor and ceiling; revisit the same starting corner at the same angle. Glass needs independent measurement.")
        }
        guard !busy, frame.timestamp - lastSavedTime >= 0.35 else { return }
        guard sampling.select(pose, time: frame.timestamp) else { return }
        lastSavedTime = frame.timestamp; busy = true; attempts += 1
        let number = attempts
        // Holds one ARFrame at a time; `busy` stops a backlog that would starve ARKit of buffers.
        queue.async { [weak self] in
            self?.write(frame, number: number)
            DispatchQueue.main.async { self?.busy = false }
        }
    }

    private static func trackingName(_ state: ARCamera.TrackingState) -> String {
        switch state {
        case .normal: return "normal"
        case .notAvailable: return "unavailable"
        case .limited(let reason): return "limited:\(String(describing: reason))"
        }
    }

    private static func mappingName(_ state: ARFrame.WorldMappingStatus) -> String {
        switch state {
        case .notAvailable: return "unavailable"
        case .limited: return "limited"
        case .extending: return "extending"
        case .mapped: return "mapped"
        @unknown default: return "unknown"
        }
    }

    private func write(_ frame: ARFrame, number: Int) {
        let name = String(format: "%06d", number)
        guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
              let jpeg = context.jpegRepresentation(of: CIImage(cvPixelBuffer: frame.capturedImage), colorSpace: colorSpace,
                                                    options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.75]),
              (try? jpeg.write(to: folder.appendingPathComponent("frames/\(name).jpg"), options: .atomic)) != nil else { return }
        var hasDepth = false
        if let depth = frame.sceneDepth, (try? pixelBytes(depth.depthMap).write(to: folder.appendingPathComponent("depth/\(name).bin"), options: .atomic)) != nil {
            hasDepth = true
            depthSize = [CVPixelBufferGetWidth(depth.depthMap), CVPixelBufferGetHeight(depth.depthMap)]
            if let confidence = depth.confidenceMap {
                try? pixelBytes(confidence).write(to: folder.appendingPathComponent("confidence/\(name).bin"), options: .atomic)
            }
        }
        let camera = frame.camera, m = camera.transform, k = camera.intrinsics
        var hasFeatures = false
        if let cloud = frame.rawFeaturePoints {
            // Keep feature identities for future geometric revisit checks. No image service is called.
            let features: [String: Any] = ["ids": cloud.identifiers.map { String($0) },
                "points": cloud.points.map { [$0.x, $0.y, $0.z] }, "timestamp": frame.timestamp]
            if let data = try? JSONSerialization.data(withJSONObject: features),
               (try? data.write(to: folder.appendingPathComponent("features/\(name).json"), options: .atomic)) != nil { hasFeatures = true }
        }
        let record: [String: Any] = [
            "frame": name, "timestamp": frame.timestamp, "depth": hasDepth,
            "tracking": Self.trackingName(camera.trackingState), "worldMapping": String(describing: frame.worldMappingStatus),
            "mappingState": Self.mappingName(frame.worldMappingStatus), "features": hasFeatures,
            "featureCount": frame.rawFeaturePoints?.points.count ?? 0,
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

    /// Writes the manifest and returns the fields to merge into the capture, or nil if nothing usable was recorded.
    func finish(scanId: String) -> [String: Any]? {
        stop()
        var counts = (frames: 0, depth: 0, size: [Int]())
        queue.sync { counts = (saved, depthSaved, depthSize); try? index.close(); try? trackingIndex.close() }
        guard counts.frames > 0 else { discard(); return nil }
        let manifest: [String: Any] = [
            "version": 1, "recordingPolicyVersion": 2, "scanId": scanId, "createdAt": ISO8601DateFormatter().string(from: Date()),
            "device": UIDevice.current.model, "system": UIDevice.current.systemVersion,
            "frames": counts.frames, "depthFrames": counts.depth, "depthSize": counts.size,
            "image": "frames/<frame>.jpg, sensor-native landscape orientation; intrinsics apply to this unrotated image",
            "depthFormat": "depth/<frame>.bin, little-endian float32 meters, row-major, depthSize = [width, height]",
            "confidenceFormat": "confidence/<frame>.bin, uint8 per pixel: 0 low, 1 medium, 2 high",
            "index": "frames.jsonl, one JSON object per saved frame",
            "trackingIndex": "tracking.jsonl, tracking state and gaps every 0.5 seconds including limited tracking",
            "sampling": "viewpoint-budget-v2: 360 primary + 120 new-view/revisit slots; maximum 3 redundant views per 30 cm/heading cell before a 20 second revisit",
            "coverageComplete": false,
            "attemptedFrames": attempts, "failedFrameWrites": attempts - counts.frames,
            "recordingLimitReached": attempts >= Self.maxFrames,
            "mappingSamples": sampledFrames, "limitedMappingSamples": limitedMappingSamples,
            "featuresFormat": "features/<frame>.json, ARKit feature ids and world positions at capture time; not ground truth",
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
        queue.async { [self] in try? index.close(); try? trackingIndex.close(); try? FileManager.default.removeItem(at: folder) }
    }
}
