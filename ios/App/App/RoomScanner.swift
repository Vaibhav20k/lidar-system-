import UIKit
import Capacitor
import RoomPlan
import AVFoundation
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
                    scanner.completion = { result in
                        self.scanning = false
                        switch result {
                        case .success(let capture): call.resolve(["capture": capture])
                        case .failure(let error): call.reject(error.localizedDescription)
                        }
                    }
                    scanner.modalPresentationStyle = .fullScreen
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

    static var scanDirectory: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("RoomformScans", isDirectory: true)
    }
}

@available(iOS 16.0, *)
private final class RoomScanController: UIViewController, RoomCaptureViewDelegate {
    var completion: ((Result<[String: Any], Error>) -> Void)?
    private let captureView = RoomCaptureView(frame: .zero)
    private let doneButton = UIButton(type: .system)
    private var finished = false
    private var processing = false
    private var timeout: Timer?

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        captureView.delegate = self
        captureView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(captureView)
        let cancel = UIButton(type: .system)
        cancel.setTitle("Cancel", for: .normal)
        cancel.addTarget(self, action: #selector(cancelScan), for: .touchUpInside)
        doneButton.setTitle("Finish scan", for: .normal)
        doneButton.addTarget(self, action: #selector(finishScan), for: .touchUpInside)
        let bar = UIStackView(arrangedSubviews: [cancel, doneButton])
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
        captureView.captureSession.run(configuration: RoomCaptureSession.Configuration())
    }

    @objc private func finishScan() {
        guard !processing && !finished else { return }
        processing = true; doneButton.isEnabled = false; doneButton.setTitle("Building room…", for: .normal)
        captureView.captureSession.stop()
        timeout = Timer.scheduledTimer(withTimeInterval: 90, repeats: false) { [weak self] _ in
            self?.fail("Room processing timed out. Please scan again.")
        }
    }
    @objc private func cancelScan() { fail("Scan cancelled") }
    @objc private func interrupted() { fail("Scan interrupted when the app entered the background. Please scan again.") }
    private func fail(_ message: String) {
        finish(.failure(NSError(domain: "Roomform", code: 1, userInfo: [NSLocalizedDescriptionKey: message])))
    }
    private func finish(_ result: Result<[String: Any], Error>) {
        DispatchQueue.main.async {
            guard !self.finished else { return }
            self.finished = true; self.timeout?.invalidate()
            self.captureView.captureSession.stop()
            self.dismiss(animated: true) { self.completion?(result); self.completion = nil }
        }
    }
    func captureView(shouldPresent roomDataForProcessing: CapturedRoomData, error: Error?) -> Bool {
        if let error = error { finish(.failure(error)); return false }
        return !finished
    }
    func captureView(didPresent processedResult: CapturedRoom, error: Error?) {
        if let error = error { finish(.failure(error)); return }
        guard !finished else { return }
        guard processedResult.walls.count >= 3 else { fail("Too few walls detected. Scan every wall, including corners, then finish."); return }
        do {
            let folder = RoomScannerPlugin.scanDirectory
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let id = UUID().uuidString
            let usdz = folder.appendingPathComponent(id).appendingPathExtension("usdz")
            try processedResult.export(to: usdz, exportOptions: .parametric)
            var openings = processedResult.doors.map { surface($0, kind: "door") }
            openings += processedResult.windows.map { surface($0, kind: "window") }
            openings += processedResult.openings.map { surface($0, kind: "opening") }
            let capture: [String: Any] = [
                "kind": "roomplan", "walls": processedResult.walls.map { surface($0) }, "openings": openings,
                "objects": processedResult.objects.map { object -> [String: Any] in
                    ["id": object.identifier.uuidString, "transform": matrix(object.transform), "width": object.dimensions.x,
                     "height": object.dimensions.y, "depth": object.dimensions.z, "confidence": String(describing: object.confidence),
                     "category": String(describing: object.category)]
                }, "usdzPath": usdz.path
            ]
            // Keep raw data alongside USDZ so a normalization failure does not destroy the measurement.
            let json = try JSONSerialization.data(withJSONObject: capture, options: [.prettyPrinted, .sortedKeys])
            try json.write(to: folder.appendingPathComponent(id).appendingPathExtension("json"), options: .atomic)
            finish(.success(capture))
        } catch { finish(.failure(error)) }
    }
    private func surface(_ value: CapturedRoom.Surface, kind: String? = nil) -> [String: Any] {
        var result: [String: Any] = ["id": value.identifier.uuidString, "transform": matrix(value.transform),
                                   "width": value.dimensions.x, "height": value.dimensions.y, "depth": max(0, value.dimensions.z),
                                   "confidence": String(describing: value.confidence)]
        if let kind = kind { result["kind"] = kind }
        return result
    }
    private func matrix(_ value: simd_float4x4) -> [Float] {
        (0..<4).flatMap { column in (0..<4).map { row in value[column][row] } }
    }
    deinit { timeout?.invalidate(); NotificationCenter.default.removeObserver(self) }
}
