import Foundation
import simd

/// The ceiling of a scanned room, measured on the phone from the raw LiDAR bundle, with no network and no vision model.
/// A Swift port of scripts/scan_processing/ceiling.py: the main ceiling height, lower and higher regions from a 5 cm
/// grid, beams running along walls from their own faces, and hanging fixtures (a fan, a lamp) split off the ceiling.
/// The result has the shape of the room's `ceiling` field. Pure Swift so it can also be compiled and checked on a Mac.
/// Cooperative cancellation uses monotonic time and is safe across the UI/worker queues.
final class CeilingWorkBudget {
    private let lock = NSLock()
    private var cancelled = false
    private let deadline: TimeInterval
    init(seconds: TimeInterval) { deadline = ProcessInfo.processInfo.systemUptime + seconds }
    func cancel() { lock.lock(); cancelled = true; lock.unlock() }
    var shouldStop: Bool {
        lock.lock(); let stopped = cancelled; lock.unlock()
        return stopped || ProcessInfo.processInfo.systemUptime >= deadline
    }
}

struct CeilingEstimator {

    // Same interval semantics as beam_runs.py: missing samples and changing
    // sections split runs. Unsupported corner continuations are not invented.
    static func supportedRuns(_ found: [(bottom: Float, wall: Float, front: Float)?]) -> [(Int, Int)] {
        var result: [(Int, Int)] = [], start: Int?
        for i in 0...found.count {
            let current = i < found.count ? found[i] : nil
            let previous = i > 0 ? found[i - 1] : nil
            var split = current == nil
            if let a = current, let b = previous {
                split = abs(a.bottom - b.bottom) > 0.08 || abs((a.front - a.wall) - (b.front - b.wall)) > 0.08
            }
            if split, let first = start {
                if i - first >= 4 { result.append((first, i)) }
                start = nil
            }
            if current != nil && start == nil { start = i }
        }
        return result
    }

    struct WallSurface { let id: String; let transform: simd_float4x4; let width: Float; let height: Float }
    /// A door, window or opening: only its top matters here (a beam bottom level with it is the opening's head).
    struct Opening { let transform: simd_float4x4; let width: Float; let height: Float }

    // Grid (same values as ceiling.py).
    static let cell: Float = 0.05, minHeight: Float = 1.8, facingDown: Float = -0.8, levelBand: Float = 0.07
    static let minLevelArea: Float = 0.15, minRegionArea: Float = 0.12, trace = 4, fillReach: Float = 0.15
    static let facingSide: Float = 0.3, maxGrow: Float = 0.12, faceSpan: Float = 0.25, faceMin: Float = 3
    // Beams along walls.
    static let beamStretch: Float = 0.25, beamMinDrop: Float = 0.10, beamMinDepth: Float = 0.04, beamMaxDepth: Float = 0.35
    static let beamMaxDrop: Float = 0.45, beamMinLength: Float = 1.5, faceFill: Float = 0.7
    // Big beams and bulkheads (25-90 cm out, down to 90 cm), read from their faces with tests only a beam passes: the
    // bottom is joined to the wall and fills the way out to the face, and it is not the head of a door or window.
    static let bigMinDepth: Float = 0.25, bigMaxDepth: Float = 0.9, bigMaxDrop: Float = 0.9
    static let beamAttach: Float = 0.06, beamBin: Float = 0.02, beamSpan: Float = 0.8, headGap: Float = 0.08
    static let framePoints = 20, minFrames = 3   // per-frame measurement of big beams (a slipped scan doubles them)

    let walls: [WallSurface]
    let floor: [SIMD2<Float>]   // the room's floor outline in world x/z
    var openings: [Opening] = []
    var shouldCancel: () -> Bool = { false }
    var onProgress: (String) -> Void = { _ in }

    // MARK: - Reading the bundle

    private struct Frame { let pose: simd_float4x4; let fx, fy, cx, cy: Float; let depth: [Float]; let confidence: [UInt8] }

    private static func frames(raw: URL, shouldCancel: () -> Bool) -> (frames: [Frame], width: Int, height: Int)? {
        guard let manifest = try? JSONSerialization.jsonObject(with: Data(contentsOf: raw.appendingPathComponent("manifest.json"))) as? [String: Any],
              let size = manifest["depthSize"] as? [Int], size.count == 2,
              let index = try? String(contentsOf: raw.appendingPathComponent("frames.jsonl"), encoding: .utf8) else { return nil }
        let (w, h) = (size[0], size[1])
        var result: [Frame] = []
        for line in index.split(separator: "\n") {
            if shouldCancel() { return nil }
            guard let record = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any],
                  record["depth"] as? Bool == true, let name = record["frame"] as? String,
                  let k = (record["intrinsics"] as? [NSNumber])?.map({ $0.floatValue }), k.count == 9,
                  let m = (record["cameraToWorld"] as? [NSNumber])?.map({ $0.floatValue }), m.count == 16,
                  let imageWidth = (record["imageWidth"] as? NSNumber)?.floatValue,
                  let depthData = try? Data(contentsOf: raw.appendingPathComponent("depth/\(name).bin")), depthData.count == w * h * 4,
                  let confidenceData = try? Data(contentsOf: raw.appendingPathComponent("confidence/\(name).bin")), confidenceData.count == w * h else { continue }
            let scale = Float(w) / imageWidth
            let depth = depthData.withUnsafeBytes { Array($0.bindMemory(to: Float.self)) }
            let pose = simd_float4x4(columns: (SIMD4(m[0], m[1], m[2], m[3]), SIMD4(m[4], m[5], m[6], m[7]), SIMD4(m[8], m[9], m[10], m[11]), SIMD4(m[12], m[13], m[14], m[15])))
            result.append(Frame(pose: pose, fx: k[0] * scale, fy: k[4] * scale, cx: k[6] * scale, cy: k[7] * scale, depth: depth, confidence: Array(confidenceData)))
        }
        return (result, w, h)
    }

    /// Points from every frame, sorted into what each step needs: ceiling-like (downward), side faces (for the grid's
    /// edges), and everything near a wall above 1 m with its normal (for beams along walls).
    private struct Points {
        var down: [SIMD3<Float>] = [], side: [SIMD3<Float>] = []
        var downFrame: [Int32] = []
        var near: [SIMD3<Float>] = [], nearNormal: [SIMD3<Float>] = [], nearFrame: [Int32] = []
    }

    private func points(_ bundle: (frames: [Frame], width: Int, height: Int), floorY: Float) -> Points {
        var out = Points()
        let (w, h) = (bundle.width, bundle.height)
        out.near.reserveCapacity(bundle.frames.count * w * h / 4); out.nearNormal.reserveCapacity(bundle.frames.count * w * h / 4)
        out.down.reserveCapacity(bundle.frames.count * w * h / 8)
        let planes = walls.map { (origin: SIMD3<Float>($0.transform.columns.3.x, $0.transform.columns.3.y, $0.transform.columns.3.z),
                                  axis: simd_normalize(SIMD3<Float>($0.transform.columns.0.x, $0.transform.columns.0.y, $0.transform.columns.0.z)),
                                  inward: simd_normalize(SIMD3<Float>($0.transform.columns.2.x, $0.transform.columns.2.y, $0.transform.columns.2.z)), width: $0.width) }
        var camera = [SIMD3<Float>](repeating: .zero, count: w * h)
        for (frameIndex, frame) in bundle.frames.enumerated() {
            if shouldCancel() { return out }
            for y in 0..<h {
                for x in 0..<w {
                    let d = frame.depth[y * w + x]
                    camera[y * w + x] = SIMD3((Float(x) + 0.5 - frame.cx) / frame.fx * d, -(Float(y) + 0.5 - frame.cy) / frame.fy * d, -d)
                }
            }
            let rotation = simd_float3x3(SIMD3(frame.pose.columns.0.x, frame.pose.columns.0.y, frame.pose.columns.0.z),
                                         SIMD3(frame.pose.columns.1.x, frame.pose.columns.1.y, frame.pose.columns.1.z),
                                         SIMD3(frame.pose.columns.2.x, frame.pose.columns.2.y, frame.pose.columns.2.z))
            let position = SIMD3(frame.pose.columns.3.x, frame.pose.columns.3.y, frame.pose.columns.3.z)
            for y in 0..<h {
                for x in 0..<w {
                    let i = y * w + x, d = frame.depth[i], c = frame.confidence[i]
                    guard c >= 1, d > 0.3, d < 5 else { continue }
                    // Central differences like numpy.gradient (one-sided at the border).
                    let gx = x == 0 ? camera[i + 1] - camera[i] : x == w - 1 ? camera[i] - camera[i - 1] : (camera[i + 1] - camera[i - 1]) * 0.5
                    let gy = y == 0 ? camera[i + w] - camera[i] : y == h - 1 ? camera[i] - camera[i - w] : (camera[i + w] - camera[i - w]) * 0.5
                    var n = simd_cross(gx, gy)
                    let length = simd_length(n)
                    guard length > 1e-9 else { continue }
                    n /= length
                    if simd_dot(n, camera[i]) > 0 { n = -n }
                    let world = rotation * camera[i] + position, normal = rotation * n, height = world.y - floorY
                    if height > Self.minHeight {
                        if normal.y < Self.facingDown { out.down.append(world); out.downFrame.append(Int32(frameIndex)) }
                        if c >= 2, abs(normal.y) < Self.facingSide { out.side.append(world) }
                    }
                    if d < 4.5, height > 1.0 {
                        for plane in planes {
                            let rel = world - plane.origin
                            if abs(simd_dot(rel, plane.axis)) < plane.width / 2 + 0.1, simd_dot(rel, plane.inward) > -0.25, simd_dot(rel, plane.inward) < Self.bigMaxDepth + 0.25 {
                                out.near.append(world); out.nearNormal.append(normal); out.nearFrame.append(Int32(frameIndex)); break
                            }
                        }
                    }
                }
            }
        }
        return out
    }

    // MARK: - Grid helpers

    private struct Grid<T> {
        let cols: Int, rows: Int
        var values: [T]
        init(cols: Int, rows: Int, value: T) { self.cols = cols; self.rows = rows; values = Array(repeating: value, count: cols * rows) }
        subscript(r: Int, c: Int) -> T { get { values[r * cols + c] } set { values[r * cols + c] = newValue } }
    }

    private static func inside(_ p: SIMD2<Float>, _ polygon: [SIMD2<Float>]) -> Bool {
        var result = false, j = polygon.count - 1
        for i in 0..<polygon.count {
            let a = polygon[i], b = polygon[j]
            if (a.y > p.y) != (b.y > p.y), p.x < (b.x - a.x) * (p.y - a.y) / (b.y - a.y) + a.x { result.toggle() }
            j = i
        }
        return result
    }

    private static func median<C: Collection>(_ values: C) -> Float where C.Element == Float {
        let sorted = values.sorted()
        guard !sorted.isEmpty else { return 0 }
        return sorted.count % 2 == 1 ? sorted[sorted.count / 2] : (sorted[sorted.count / 2 - 1] + sorted[sorted.count / 2]) / 2
    }

    /// Peak of a histogram smoothed over three bins, as the left edge of the peak bin plus half a bin.
    private static func peak(_ values: [Float], from low: Float, to high: Float, step: Float) -> Float? {
        let bins = Int(((high - low) / step).rounded(.down))
        guard bins > 0, !values.isEmpty else { return nil }
        var hist = [Float](repeating: 0, count: bins)
        for v in values { let b = Int(((v - low) / step).rounded(.down)); if b >= 0, b < bins { hist[b] += 1 } }
        var best = 0, bestValue: Float = -1
        for b in 0..<bins {
            let s = hist[b] + (b > 0 ? hist[b - 1] : 0) + (b + 1 < bins ? hist[b + 1] : 0)
            if s > bestValue { bestValue = s; best = b }
        }
        return low + Float(best) * step + step / 2
    }

    /// Like numpy.percentile with linear interpolation; q in 0...1.
    private static func percentile(_ values: [Float], _ q: Float) -> Float {
        let sorted = values.sorted()
        guard sorted.count > 1 else { return sorted.first ?? 0 }
        let position = q * Float(sorted.count - 1), low = Int(position.rounded(.down)), high = min(low + 1, sorted.count - 1)
        return sorted[low] + (sorted[high] - sorted[low]) * (position - Float(low))
    }

    private static func polyfit(_ x: [Float], _ y: [Float]) -> (slope: Float, offset: Float) {
        let n = Float(x.count), mx = x.reduce(0, +) / n, my = y.reduce(0, +) / n
        var sxx: Float = 0, sxy: Float = 0
        for i in 0..<x.count { sxx += (x[i] - mx) * (x[i] - mx); sxy += (x[i] - mx) * (y[i] - my) }
        let slope = sxx > 1e-9 ? sxy / sxx : 0
        return (slope, my - slope * mx)
    }

    /// Outer boundary of one 4-connected blob (Moore tracing), as pixel-corner coordinates.
    private static func outlineOf(_ mask: Grid<Bool>, start: (Int, Int)) -> [SIMD2<Float>] {
        // Walk the boundary between blob pixels and the outside, keeping the blob on the right.
        func filled(_ r: Int, _ c: Int) -> Bool { r >= 0 && c >= 0 && r < mask.rows && c < mask.cols && mask[r, c] }
        var points: [SIMD2<Float>] = []
        var (r, c) = start, direction = 0  // 0 right, 1 down, 2 left, 3 up (along pixel edges, starting at the top-left corner)
        let begin = (r, c)
        repeat {
            points.append(SIMD2(Float(c), Float(r)))
            // Pixels ahead-left and ahead-right of the current edge.
            let (al, ar): (Bool, Bool)
            switch direction {
            case 0: (al, ar) = (filled(r - 1, c), filled(r, c))
            case 1: (al, ar) = (filled(r, c), filled(r, c - 1))
            case 2: (al, ar) = (filled(r, c - 1), filled(r - 1, c - 1))
            default: (al, ar) = (filled(r - 1, c - 1), filled(r - 1, c))
            }
            if al { direction = (direction + 3) % 4 } else if !ar { direction = (direction + 1) % 4 }
            switch direction { case 0: c += 1; case 1: r += 1; case 2: c -= 1; default: r -= 1 }
            if points.count > mask.rows * mask.cols * 4 { break }
        } while (r, c) != begin
        return points
    }

    private static func simplify(_ points: [SIMD2<Float>], epsilon: Float) -> [SIMD2<Float>] {
        guard points.count > 2 else { return points }
        func dp(_ a: Int, _ b: Int, _ keep: inout [Bool]) {
            guard b > a + 1 else { return }
            let p = points[a], q = points[b], d = q - p, length = max(simd_length(d), 1e-6)
            var best = -1, far: Float = 0
            for i in (a + 1)..<b {
                let distance = abs(d.x * (points[i].y - p.y) - d.y * (points[i].x - p.x)) / length
                if distance > far { far = distance; best = i }
            }
            if far > epsilon { keep[best] = true; dp(a, best, &keep); dp(best, b, &keep) }
        }
        // Closed outline: split at the point furthest from the first.
        let far = points.indices.max { simd_distance(points[$0], points[0]) < simd_distance(points[$1], points[0]) } ?? 0
        var keep = [Bool](repeating: false, count: points.count)
        keep[0] = true; keep[far] = true
        dp(0, far, &keep)
        var loop = Array(points[far...]) + [points[0]]
        var keep2 = [Bool](repeating: false, count: loop.count)
        keep2[0] = true; keep2[loop.count - 1] = true
        func dp2(_ a: Int, _ b: Int) {
            guard b > a + 1 else { return }
            let p = loop[a], q = loop[b], d = q - p, length = max(simd_length(d), 1e-6)
            var best = -1, farDistance: Float = 0
            for i in (a + 1)..<b {
                let distance = abs(d.x * (loop[i].y - p.y) - d.y * (loop[i].x - p.x)) / length
                if distance > farDistance { farDistance = distance; best = i }
            }
            if farDistance > epsilon { keep2[best] = true; dp2(a, best); dp2(best, b) }
        }
        dp2(0, loop.count - 1)
        loop = loop.enumerated().filter { keep2[$0.offset] }.map { $0.element }
        let first = points.enumerated().filter { keep[$0.offset] && $0.offset < far }.map { $0.element }
        return first + loop.dropLast()
    }

    private static func area(_ polygon: [SIMD2<Float>]) -> Float {
        var sum: Float = 0
        for i in polygon.indices { let a = polygon[i], b = polygon[(i + 1) % polygon.count]; sum += a.x * b.y - b.x * a.y }
        return abs(sum) / 2
    }

    /// Rasterises polygons onto a shared 2 cm grid over `low`..`high`, for overlap tests.
    private static func mask(_ polygons: [[SIMD2<Float>]], low: SIMD2<Float>, cols: Int, rows: Int) -> [Bool] {
        var out = [Bool](repeating: false, count: cols * rows)
        for polygon in polygons where polygon.count >= 3 {
            let lo = polygon.reduce(SIMD2<Float>(repeating: .infinity)) { simd_min($0, $1) }, hi = polygon.reduce(SIMD2<Float>(repeating: -.infinity)) { simd_max($0, $1) }
            let c0 = max(0, Int((lo.x - low.x) * 50)), c1 = min(cols - 1, Int((hi.x - low.x) * 50) + 1)
            let r0 = max(0, Int((lo.y - low.y) * 50)), r1 = min(rows - 1, Int((hi.y - low.y) * 50) + 1)
            guard c0 <= c1, r0 <= r1 else { continue }
            for r in r0...r1 { for c in c0...c1 where inside(low + SIMD2(Float(c) + 0.5, Float(r) + 0.5) / 50, polygon) { out[r * cols + c] = true } }
        }
        return out
    }

    /// Millimetre-rounded Double for JSON (a Float would print as 2.881999969).
    private static func mm(_ v: Float) -> Double { (Double(v) * 1000).rounded() / 1000 }

    /// The floor outline the app will give the room: wall ends joined in order (as chainWalls in native.ts), else the
    /// scanner's floor polygon.
    static func floorOutline(walls: [WallSurface], floorCorners: [SIMD2<Float>]) -> [SIMD2<Float>] {
        var remaining = walls.map { w -> (SIMD2<Float>, SIMD2<Float>) in
            let o = SIMD2(w.transform.columns.3.x, w.transform.columns.3.z), a = SIMD2(w.transform.columns.0.x, w.transform.columns.0.z)
            return (o - a * w.width / 2, o + a * w.width / 2)
        }
        guard !remaining.isEmpty else { return floorCorners }
        let first = remaining.removeFirst()
        var points = [first.0, first.1]
        while !remaining.isEmpty {
            let end = points.last!
            var best = -1, reverse = false, gap: Float = 0.18
            for (i, w) in remaining.enumerated() {
                if simd_distance(end, w.0) < gap { best = i; gap = simd_distance(end, w.0); reverse = false }
                if simd_distance(end, w.1) < gap { best = i; gap = simd_distance(end, w.1); reverse = true }
            }
            guard best >= 0 else { return floorCorners }
            let w = remaining.remove(at: best)
            points.append(reverse ? w.0 : w.1)
        }
        guard simd_distance(points.last!, points[0]) <= 0.18 else { return floorCorners }
        points.removeLast()
        return points.count >= 3 ? points : floorCorners
    }

    // MARK: - Estimate

    /// The `ceiling` field for the room, or nil when too little of the ceiling was scanned.
    func estimate(raw: URL) -> [String: Any]? {
        onProgress("Reading depth frames")
        guard !shouldCancel(), floor.count >= 3, !walls.isEmpty, let bundle = Self.frames(raw: raw, shouldCancel: shouldCancel), !bundle.frames.isEmpty else { return nil }
        let floorY = walls.map { $0.transform.columns.3.y - $0.height / 2 }.min()!
        onProgress("Projecting depth")
        let pts = points(bundle, floorY: floorY)
        guard !shouldCancel(), !pts.down.isEmpty else { return nil }

        onProgress("Fitting ceiling regions")
        // A frame turned to the longest wall, so beams and soffits come out axis-aligned.
        let longest = walls.max { $0.width < $1.width }!.transform
        let angle = atan2(longest.columns.0.z, longest.columns.0.x), cosA = cos(angle), sinA = sin(angle)
        let toRoom = { (x: Float, z: Float) -> SIMD2<Float> in SIMD2(x * cosA + z * sinA, -x * sinA + z * cosA) }
        let toWorld = { (p: SIMD2<Float>) -> SIMD2<Float> in SIMD2(p.x * cosA - p.y * sinA, p.x * sinA + p.y * cosA) }
        let outline = floor.map { toRoom($0.x, $0.y) }
        let lo = outline.reduce(SIMD2<Float>(repeating: .infinity)) { simd_min($0, $1) }, hi = outline.reduce(SIMD2<Float>(repeating: -.infinity)) { simd_max($0, $1) }
        let origin = lo - 0.1, cell = Self.cell
        let cols = Int(((hi.x + 0.1 - origin.x) / cell).rounded(.up)), rows = Int(((hi.y + 0.1 - origin.y) / cell).rounded(.up))
        var inside = Grid(cols: cols, rows: rows, value: false)
        for r in 0..<rows { for c in 0..<cols { inside[r, c] = Self.inside(origin + SIMD2(Float(c) + 0.5, Float(r) + 0.5) * cell, outline) } }

        // Equal weight per view. Selecting max-8cm first biased noisy ceilings
        // upwards, and a close, dense view could dominate every later revisit.
        var observations = [Int: [Float]]()
        for (i, p) in pts.down.enumerated() {
            let q = toRoom(p.x, p.z), c = Int(((q.x - origin.x) / cell).rounded(.down)), r = Int(((q.y - origin.y) / cell).rounded(.down))
            guard c >= 0, c < cols, r >= 0, r < rows, inside[r, c] else { continue }
            let key = Int(pts.downFrame[i]) * rows * cols + r * cols + c
            observations[key, default: []].append(p.y - floorY)
        }
        guard !shouldCancel() else { return nil }
        var cells = [[Float]](repeating: [], count: rows * cols)
        for (key, values) in observations where values.count >= 2 {
            cells[key % (rows * cols)].append(Self.median(values))
        }
        var seen = Grid(cols: cols, rows: rows, value: false), height = Grid(cols: cols, rows: rows, value: Float(0))
        var repeatSpreads: [Float] = []
        // Retain sparsely observed cells for surface/beam classification. Missing
        // repeat coverage lowers quality; it must not erase the main ceiling and
        // make a better-observed beam become the room's ceiling.
        for i in cells.indices where !cells[i].isEmpty {
            seen.values[i] = true; height.values[i] = Self.median(cells[i])
            guard cells[i].count >= 3 else { continue }
            let sorted = cells[i].sorted()
            repeatSpreads.append(sorted[Int(Float(sorted.count - 1) * 0.9)] - sorted[Int(Float(sorted.count - 1) * 0.1)])
        }
        let seenCount = seen.values.filter { $0 }.count
        guard Float(seenCount) * cell * cell >= 1 else { return nil }

        // Peel off height levels, largest first.
        var found: [(height: Float, cells: Int)] = []
        var free = seen.values
        while found.count < 8 {
            let values = (0..<free.count).filter { free[$0] }.map { height.values[$0] }
            guard Float(values.count) * cell * cell >= Self.minLevelArea, let vmin = values.min(), let vmax = values.max() else { break }
            let bins = max(1, Int(((vmax + 0.05 - vmin) / 0.025).rounded(.up)) - 1)
            var hist = [Int](repeating: 0, count: bins)
            for v in values { hist[min(bins - 1, max(0, Int(((v - vmin) / 0.025).rounded(.down))))] += 1 }
            let peakValue = vmin + Float(hist.indices.max { hist[$0] < hist[$1] }!) * 0.025 + 0.0125
            let members = (0..<free.count).filter { free[$0] && abs(height.values[$0] - peakValue) < Self.levelBand }
            guard Float(members.count) * cell * cell >= Self.minLevelArea else { break }
            found.append((members.map { height.values[$0] }.reduce(0, +) / Float(members.count), members.count))
            for m in members { free[m] = false }
        }
        guard !found.isEmpty else { return nil }
        found.sort { $0.height > $1.height }
        let levels = found.map { $0.height }
        let main = found.indices.max { found[$0].cells < found[$1].cells }!
        let ceiling = levels[main]

        var label = Grid(cols: cols, rows: rows, value: -1)
        for i in 0..<(cols * rows) where seen.values[i] {
            let distances = levels.map { abs(height.values[i] - $0) }
            let best = distances.indices.min { distances[$0] < distances[$1] }!
            if distances[best] < Self.levelBand * 1.5 { label.values[i] = best }
        }
        // Unseen cells copy a measured neighbour within reach, otherwise the main ceiling.
        let reach = Int((Self.fillReach / cell).rounded(.down))
        var filled = Grid(cols: cols, rows: rows, value: main)
        for r in 0..<rows {
            for c in 0..<cols {
                if label[r, c] >= 0 { filled[r, c] = label[r, c]; continue }
                var best: (Float, Int)?
                for dr in -reach...reach {
                    for dc in -reach...reach {
                        let rr = r + dr, cc = c + dc
                        guard rr >= 0, cc >= 0, rr < rows, cc < cols, label[rr, cc] >= 0 else { continue }
                        let d = Float(dr * dr + dc * dc).squareRoot()
                        if d * cell <= Self.fillReach, best == nil || d < best!.0 { best = (d, label[rr, cc]) }
                    }
                }
                filled[r, c] = best?.1 ?? main
            }
        }
        // 5x5 median, edges replicated.
        var smoothed = filled
        for r in 0..<rows {
            for c in 0..<cols {
                var window: [Int] = []
                for dr in -2...2 { for dc in -2...2 { window.append(filled[min(rows - 1, max(0, r + dr)), min(cols - 1, max(0, c + dc))]) } }
                window.sort(); smoothed[r, c] = window[12]
            }
        }
        for i in 0..<(cols * rows) where !inside.values[i] { smoothed.values[i] = -1 }

        // Side faces on the fine grid, to place the edges of regions hanging below the ceiling.
        let fineCols = cols * Self.trace, fineRows = rows * Self.trace
        var regions: [[String: Any]] = [], regionPolygons: [(polygon: [SIMD2<Float>], height: Float, evidence: String, holes: [[SIMD2<Float>]])] = []
        for index in levels.indices where index != main {
            if shouldCancel() { return nil }
            // 3x3 opening removes specks up to 10 cm wide without shifting the mask.
            var mask = Grid(cols: cols, rows: rows, value: false)
            for i in 0..<(cols * rows) { mask.values[i] = smoothed.values[i] == index }
            var eroded = mask
            for r in 0..<rows { for c in 0..<cols where mask[r, c] {
                outer: for dr in -1...1 { for dc in -1...1 { let rr = r + dr, cc = c + dc
                    if rr >= 0, cc >= 0, rr < rows, cc < cols, !mask[rr, cc] { eroded[r, c] = false; break outer } } }
            } }
            var opened = eroded
            for r in 0..<rows { for c in 0..<cols where eroded[r, c] { for dr in -1...1 { for dc in -1...1 {
                let rr = r + dr, cc = c + dc
                if rr >= 0, cc >= 0, rr < rows, cc < cols { opened[rr, cc] = true } } } } }
            var fine = Grid(cols: fineCols, rows: fineRows, value: false)
            for r in 0..<fineRows { for c in 0..<fineCols { fine[r, c] = opened[r / Self.trace, c / Self.trace] } }
            if levels[index] < ceiling { fine = reachSideFace(fine, low: levels[index], ceiling: ceiling, side: pts.side, floorY: floorY, origin: origin, toRoom: toRoom, inside: inside) }
            // Blobs, traced and simplified.
            var visited = [Bool](repeating: false, count: fineCols * fineRows)
            for start in 0..<(fineCols * fineRows) where fine.values[start] && !visited[start] {
                if shouldCancel() { return nil }
                var blob = Grid(cols: fineCols, rows: fineRows, value: false)
                var stack = [start], size = 0, first = start
                var minR = fineRows, maxR = 0, minC = fineCols, maxC = 0
                visited[start] = true
                while let i = stack.popLast() {
                    blob.values[i] = true; size += 1; first = min(first, i)
                    let r = i / fineCols, c = i % fineCols
                    minR = min(minR, r); maxR = max(maxR, r); minC = min(minC, c); maxC = max(maxC, c)
                    for (rr, cc) in [(r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)] where rr >= 0 && cc >= 0 && rr < fineRows && cc < fineCols {
                        let j = rr * fineCols + cc
                        if fine.values[j] && !visited[j] { visited[j] = true; stack.append(j) }
                    }
                }
                let fineCell = cell / Float(Self.trace)
                guard Float(size) * fineCell * fineCell >= Self.minRegionArea else { continue }
                var outlinePixels = Self.outlineOf(blob, start: (first / fineCols, first % fineCols))
                var epsilon = 1.2 * Float(Self.trace)
                var simplified = Self.simplify(outlinePixels, epsilon: epsilon)
                while simplified.count > 120 { epsilon *= 1.5; simplified = Self.simplify(outlinePixels, epsilon: epsilon) }
                outlinePixels = simplified
                guard outlinePixels.count >= 3 else { continue }
                let polygon = outlinePixels.map { toWorld($0 * fineCell + origin) }
                // Holes: cells inside the blob's outline that are not part of it (a soffit running round the room has
                // the main ceiling in the middle). Found as the gaps not reachable from outside the blob's box.
                var holes: [[SIMD2<Float>]] = []
                let r0 = max(0, minR - 1), r1 = min(fineRows - 1, maxR + 1), c0 = max(0, minC - 1), c1 = min(fineCols - 1, maxC + 1)
                let bw = c1 - c0 + 1, bh = r1 - r0 + 1
                var outside = [Bool](repeating: false, count: bw * bh), queue: [Int] = []
                for r in 0..<bh { for c in 0..<bw where (r == 0 || c == 0 || r == bh - 1 || c == bw - 1) && !blob[r0 + r, c0 + c] { outside[r * bw + c] = true; queue.append(r * bw + c) } }
                while let i = queue.popLast() {
                    let r = i / bw, c = i % bw
                    for (rr, cc) in [(r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)] where rr >= 0 && cc >= 0 && rr < bh && cc < bw {
                        let j = rr * bw + cc
                        if !outside[j] && !blob[r0 + rr, c0 + cc] { outside[j] = true; queue.append(j) }
                    }
                }
                var seenHole = [Bool](repeating: false, count: bw * bh)
                for i in 0..<(bw * bh) where !outside[i] && !seenHole[i] && !blob[r0 + i / bw, c0 + i % bw] && holes.count < 32 {
                    var hole = Grid(cols: fineCols, rows: fineRows, value: false)
                    var pending = [i], count = 0, firstHole = Int.max
                    seenHole[i] = true
                    while let k = pending.popLast() {
                        let r = k / bw, c = k % bw
                        hole[r0 + r, c0 + c] = true; count += 1; firstHole = min(firstHole, (r0 + r) * fineCols + c0 + c)
                        for (rr, cc) in [(r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)] where rr >= 0 && cc >= 0 && rr < bh && cc < bw {
                            let j = rr * bw + cc
                            if !seenHole[j] && !outside[j] && !blob[r0 + rr, c0 + cc] { seenHole[j] = true; pending.append(j) }
                        }
                    }
                    guard Float(count) * fineCell * fineCell >= 0.05 else { continue }
                    let ring = Self.simplify(Self.outlineOf(hole, start: (firstHole / fineCols, firstHole % fineCols)), epsilon: 1.2 * Float(Self.trace))
                    if ring.count >= 3 { holes.append(Array(ring.prefix(128)).map { toWorld($0 * fineCell + origin) }) }
                }
                regionPolygons.append((polygon, levels[index], "estimated", holes))
            }
        }

        // Hanging fixtures: small, round, a little below the ceiling and away from every wall.
        let wallLines = walls.map { w -> (SIMD2<Float>, SIMD2<Float>) in
            let o = SIMD2(w.transform.columns.3.x, w.transform.columns.3.z), a = simd_normalize(SIMD2(w.transform.columns.0.x, w.transform.columns.0.z))
            return (o - a * w.width / 2, o + a * w.width / 2)
        }
        func wallDistance(_ p: SIMD2<Float>) -> Float {
            wallLines.map { a, b in let t = max(0, min(1, simd_dot(p - a, b - a) / max(simd_length_squared(b - a), 1e-9))); return simd_distance(p, a + t * (b - a)) }.min() ?? 9
        }
        var items: [[String: Any]] = [], kept: [(polygon: [SIMD2<Float>], height: Float, evidence: String, holes: [[SIMD2<Float>]])] = []
        for region in regionPolygons {
            let lo = region.polygon.reduce(SIMD2<Float>(repeating: .infinity)) { simd_min($0, $1) }, hi = region.polygon.reduce(SIMD2<Float>(repeating: -.infinity)) { simd_max($0, $1) }
            let size = hi - lo, clear = region.polygon.map(wallDistance).min() ?? 0, drop = ceiling - region.height
            if drop > 0.05, drop < 0.7, clear > 0.3, max(size.x, size.y) < 1.3, max(size.x, size.y) / max(min(size.x, size.y), 0.05) < 2.2 {
                let centre = region.polygon.reduce(SIMD2<Float>(), +) / Float(region.polygon.count)
                if items.contains(where: { simd_distance(SIMD2(($0["x"] as! NSNumber).floatValue, ($0["z"] as! NSNumber).floatValue), centre) < 0.5 }) { continue }
                items.append(["id": UUID().uuidString.lowercased(), "kind": "other_electrical", "x": Self.mm(centre.x), "z": Self.mm(centre.y), "height": Self.mm(region.height),
                              "width": Self.mm(min(3, max(size.x, 0.05))), "depth": Self.mm(min(3, max(size.y, 0.05))), "rotation": 0, "evidence": "estimated", "review": true,
                              "note": "Hanging fixture (ceiling fan or lamp) seen by depth; not part of the ceiling."])
            } else { kept.append(region) }
        }

        // Beams along walls, from their own faces. Grid pieces drawn from them give way to the measured strips, taken
        // together (an L of beams replaces the one L-shaped grid piece). A beam a wider grid shape holds whole is left
        // out and that shape kept; this is settled before anything is removed (see ceiling.py).
        onProgress("Measuring beam faces")
        if shouldCancel() { return nil }
        let beams = wallBeams(pts, floorY: floorY, ceiling: ceiling)
        if !beams.isEmpty {
            let lowerIndices = kept.indices.filter { kept[$0].height < ceiling }
            let all = beams.flatMap { $0.polygon } + lowerIndices.flatMap { kept[$0].polygon }
            let low = all.reduce(SIMD2<Float>(repeating: .infinity)) { simd_min($0, $1) } - 0.2
            let high = all.reduce(SIMD2<Float>(repeating: -.infinity)) { simd_max($0, $1) } + 0.2
            let mc = Int((high.x - low.x) * 50) + 1, mr = Int((high.y - low.y) * 50) + 1
            let masks = lowerIndices.map { Self.mask([kept[$0].polygon], low: low, cols: mc, rows: mr) }
            let stripMasks = beams.map { Self.mask([$0.polygon], low: low, cols: mc, rows: mr) }
            var accepted = Array(beams.indices), replaced = [Bool](repeating: false, count: masks.count)
            for _ in 0...beams.count {
                var union = [Bool](repeating: false, count: mc * mr)
                for i in accepted { for j in union.indices where stripMasks[i][j] { union[j] = true } }
                var near = union
                for r in 0..<mr { for c in 0..<mc where union[r * mc + c] { for dr in -5...5 { for dc in -5...5 {
                    let rr = r + dr, cc = c + dc
                    if rr >= 0, cc >= 0, rr < mr, cc < mc { near[rr * mc + cc] = true } } } } }
                replaced = masks.map { m in
                    !accepted.isEmpty && Float(zip(m, near).filter { $0 && $1 }.count) >= 0.6 * Float(max(m.filter { $0 }.count, 1))
                }
                let keptMasks = masks.indices.filter { !replaced[$0] }.map { masks[$0] }
                let still = accepted.filter { i in
                    let strip = stripMasks[i], cells = strip.filter { $0 }.count
                    var covered = [Bool](repeating: false, count: mc * mr)
                    for m in keptMasks where zip(m, strip).contains(where: { $0 && $1 }) { for j in covered.indices where m[j] { covered[j] = true } }
                    let inside = zip(strip, covered).filter { $0 && $1 }.count
                    return !(Float(inside) >= 0.8 * Float(cells) && Float(covered.filter { $0 }.count) >= 2 * Float(cells))
                }
                if still == accepted { break }
                accepted = still
            }
            let gone = Set(lowerIndices.indices.filter { replaced[$0] }.map { lowerIndices[$0] })
            kept = kept.indices.filter { !gone.contains($0) }.map { kept[$0] }
            for i in accepted { kept.append((beams[i].polygon, beams[i].height, beams[i].evidence, [])) }
        }
        // Measured beams first, so they are kept if there are more regions than a room may hold.
        for region in (kept.filter { $0.evidence == "measured" } + kept.filter { $0.evidence != "measured" }).prefix(64) {
            var entry: [String: Any] = ["points": region.polygon.map { ["x": Self.mm($0.x), "z": Self.mm($0.y)] }, "height": Self.mm(region.height), "evidence": region.evidence]
            if !region.holes.isEmpty { entry["holes"] = region.holes.map { $0.map { ["x": Self.mm($0.x), "z": Self.mm($0.y)] } } }
            regions.append(entry)
        }
        var result: [String: Any] = ["height": Self.mm(ceiling), "coverage": (Double(seenCount) / Double(max(1, inside.values.filter { $0 }.count)) * 100).rounded() / 100, "regions": regions]
        let spreads = repeatSpreads.sorted()
        result["measurementDiagnostics"] = ["method": "frame-balanced-median-v2", "heightDatum": "RoomPlan floor (not independently validated)",
            "repeatSpreadP90Meters": spreads.isEmpty ? 0 : spreads[Int(Float(spreads.count - 1) * 0.9)],
            "repeatCoverage": Double(spreads.count) / Double(max(1, seenCount)),
            "absoluteAccuracyVerified": false] as [String: Any]
        if !items.isEmpty { result["items"] = items }
        return shouldCancel() ? nil : result
    }

    private func reachSideFace(_ fine: Grid<Bool>, low: Float, ceiling: Float, side: [SIMD3<Float>], floorY: Float, origin: SIMD2<Float>,
                               toRoom: (Float, Float) -> SIMD2<Float>, inside: Grid<Bool>) -> Grid<Bool> {
        let cols = fine.cols, rows = fine.rows, trace = Float(Self.trace)
        var count = [Float](repeating: 0, count: cols * rows)
        for p in side {
            let h = p.y - floorY
            guard h > low + 0.05, h < ceiling - 0.05 else { continue }
            let q = toRoom(p.x, p.z), c = Int(((q.x - origin.x) / Self.cell * trace).rounded(.down)), r = Int(((q.y - origin.y) / Self.cell * trace).rounded(.down))
            if c >= 0, c < cols, r >= 0, r < rows { count[r * cols + c] += 1 }
        }
        var grown = fine
        let steps = Int((Self.maxGrow / Self.cell * trace).rounded()), span = Int((Self.faceSpan / Self.cell * trace).rounded()) | 1, halfSpan = span / 2
        func reflect(_ i: Int, _ n: Int) -> Int { n == 1 ? 0 : i < 0 ? -i : i >= n ? 2 * n - 2 - i : i }
        for axis in 0..<2 {
            // Pool face points along the edge (a box blur across the direction of growth).
            var pooled = [Float](repeating: 0, count: cols * rows)
            for r in 0..<rows { for c in 0..<cols {
                var s: Float = 0
                for k in -halfSpan...halfSpan { s += axis == 0 ? count[r * cols + reflect(c + k, cols)] : count[reflect(r + k, rows) * cols + c] }
                pooled[r * cols + c] = s / Float(span)
            } }
            for direction in [1, -1] {
                for r in 0..<rows { for c in 0..<cols where fine[r, c] {
                    let (nr, nc) = axis == 0 ? (r + direction, c) : (r, c + direction)
                    let neighbour = nr >= 0 && nc >= 0 && nr < rows && nc < cols ? fine[nr, nc] : false
                    guard !neighbour else { continue }
                    var best = 0, strength: Float = -1
                    for n in 1...steps {
                        let (rr, cc) = axis == 0 ? (r + direction * n, c) : (r, c + direction * n)
                        let v = rr >= 0 && cc >= 0 && rr < rows && cc < cols ? pooled[rr * cols + cc] : 0
                        if v > strength { strength = v; best = n }
                    }
                    guard strength >= Self.faceMin else { continue }
                    for n in 1...best {
                        let (rr, cc) = axis == 0 ? (r + direction * n, c) : (r, c + direction * n)
                        if rr >= 0, cc >= 0, rr < rows, cc < cols { grown[rr, cc] = true }
                    }
                } }
            }
        }
        for r in 0..<rows { for c in 0..<cols where !inside[r / Self.trace, c / Self.trace] { grown[r, c] = false } }
        return grown
    }

    private struct Beam { let polygon: [SIMD2<Float>]; let height: Float; let evidence = "measured" }

    /// Beams running along a wall: a flat face standing out from the wall from the beam's bottom up to the ceiling,
    /// a downward-facing bottom, and no ceiling visible behind the face. Read in 25 cm stretches.
    static func stretchBuckets(_ along: [Float], starts: [Float], width: Float) -> [[Int]] {
        var buckets = [[Int]](repeating: [], count: starts.count)
        guard !starts.isEmpty else { return buckets }
        for (index, value) in along.enumerated() {
            var lo = 0, hi = starts.count
            while lo < hi {
                let mid = (lo + hi) / 2
                if starts[mid] <= value { lo = mid + 1 } else { hi = mid }
            }
            let bucket = lo - 1
            if bucket >= 0 && value < starts[bucket] + width { buckets[bucket].append(index) }
        }
        return buckets
    }

    private func wallBeams(_ pts: Points, floorY: Float, ceiling: Float) -> [Beam] {
        var beams: [Beam] = []
        for wall in walls where wall.width >= 0.8 {
            if shouldCancel() { return [] }
            let origin = SIMD3(wall.transform.columns.3.x, wall.transform.columns.3.y, wall.transform.columns.3.z)
            let axis = simd_normalize(SIMD3(wall.transform.columns.0.x, wall.transform.columns.0.y, wall.transform.columns.0.z))
            let inward = simd_normalize(SIMD3(wall.transform.columns.2.x, wall.transform.columns.2.y, wall.transform.columns.2.z))
            var along: [Float] = [], out: [Float] = [], height: [Float] = [], facing: [Bool] = [], down: [Bool] = [], frameOf: [Int32] = []
            for (k, p) in pts.near.enumerated() {
                let n = pts.nearNormal[k], rel = p - origin, a = simd_dot(rel, axis), o = simd_dot(rel, inward)
                guard abs(a) < wall.width / 2, o > -0.2, o < Self.bigMaxDepth + 0.2 else { continue }
                along.append(a); out.append(o); height.append(p.y - floorY); facing.append(simd_dot(n, inward) > 0.8); down.append(n.y < -0.8); frameOf.append(pts.nearFrame[k])
            }
            var starts: [Float] = []
            var s0 = -wall.width / 2
            while s0 < wall.width / 2 - Self.beamStretch / 2 { starts.append(s0); s0 += Self.beamStretch }
            let byStretch = Self.stretchBuckets(along, starts: starts, width: Self.beamStretch)
            let surfaces: [Float?] = byStretch.map { idx in
                let body = idx.filter { facing[$0] && height[$0] > 1.0 && height[$0] < 2.0 && abs(out[$0]) < 0.15 }.map { out[$0] }
                return body.count >= 40 ? Self.peak(body, from: -0.15, to: 0.155, step: 0.01) : nil
            }
            let known = surfaces.compactMap { $0 }
            guard known.count >= 3 else { continue }
            let typical = Self.median(known)
            // Tops of doors and windows on this wall: (from, to, height above the floor) along it.
            let heads: [(Float, Float, Float)] = openings.compactMap { o in
                let rel = SIMD3(o.transform.columns.3.x, o.transform.columns.3.y, o.transform.columns.3.z) - origin
                let c = simd_dot(rel, axis)
                guard abs(simd_dot(rel, inward)) < 0.3, abs(c) < wall.width / 2 + 0.2 else { return nil }
                return (c - o.width / 2, c + o.width / 2, o.transform.columns.3.y + o.height / 2 - floorY)
            }
            func filledShare(_ face: [Int], from bottom: Float, to topLimit: Float) -> Float? {
                var bands: [Float] = []
                var b = bottom + 0.02
                while b < topLimit { bands.append(b); b += 0.02 }
                guard bands.count >= 2, face.count >= 15 else { return nil }
                let faceHeights = face.map { height[$0] }
                return Float(bands.filter { band in faceHeights.filter { $0 >= band && $0 < band + 0.02 }.count >= 3 }.count) / Float(bands.count)
            }
            /// A big beam in one stretch (see ceiling.py big_beam).
            func bigBeam(_ idx: [Int], wallAt start: Float, s0: Float) -> (bottom: Float, wall: Float, front: Float)? {
                var wallAt = start
                let under = idx.filter { down[$0] && height[$0] > 1.9 && height[$0] < ceiling - Self.beamMinDrop && out[$0] > wallAt - 0.03 && out[$0] < wallAt + Self.bigMaxDepth + 0.05 }
                guard under.count >= 15 else { return nil }
                let bins = Int(((ceiling - 1.9) / 0.01).rounded(.down))
                guard bins > 0 else { return nil }
                var hist = [Float](repeating: 0, count: bins)
                for i in under { let b = Int(((height[i] - 1.9) / 0.01).rounded(.down)); if b >= 0, b < bins { hist[b] += 1 } }
                let smooth = (0..<bins).map { b in hist[b] + (b > 0 ? hist[b - 1] : 0) + (b + 1 < bins ? hist[b + 1] : 0) }
                let steps = Int(((Self.bigMaxDepth + 0.08) / Self.beamBin).rounded(.down))
                for peak in smooth.indices.sorted(by: { smooth[$0] > smooth[$1] }).prefix(4) {
                    guard smooth[peak] >= 6 else { return nil }
                    var bottom = 1.9 + Float(peak) * 0.01 + 0.005
                    var filled = [Int](repeating: 0, count: steps)
                    for i in under where abs(height[i] - bottom) < 0.02 {
                        let k = Int(((out[i] - wallAt + 0.03) / Self.beamBin).rounded(.down)); if k >= 0, k < steps { filled[k] += 1 }
                    }
                    let seen = filled.map { $0 >= 2 }
                    guard let first = seen.firstIndex(of: true), Float(first) * Self.beamBin - 0.03 <= Self.beamAttach else { continue }
                    if heads.contains(where: { $0.0 - 0.1 < s0 + Self.beamStretch && $0.1 + 0.1 > s0 && abs($0.2 - bottom) < Self.headGap }) { return nil }
                    // The wall just under the beam, seen in the same views as the beam.
                    let below = idx.filter { facing[$0] && height[$0] > bottom - 0.5 && height[$0] < bottom - 0.05 && abs(out[$0] - wallAt) < 0.12 }.map { out[$0] }
                    if below.count >= 40, let w = Self.peak(below, from: wallAt - 0.12, to: wallAt + 0.125, step: 0.01) { wallAt = w }
                    let topLimit = ceiling - 0.06
                    let face = idx.filter { facing[$0] && height[$0] > bottom + 0.02 && height[$0] < topLimit && out[$0] > wallAt + Self.bigMinDepth - 0.03 && out[$0] < wallAt + Self.bigMaxDepth + 0.05 }
                    guard let share = filledShare(face, from: bottom, to: topLimit) else { return nil }
                    let front = Self.median(face.map { out[$0] })
                    let lead = Int((0.03 / Self.beamBin).rounded()), last = max(lead + 1, Int((front - wallAt + 0.03) / Self.beamBin))
                    let span = seen[lead..<min(last, seen.count)]
                    let spanShare = span.isEmpty ? 0 : Float(span.filter { $0 }.count) / Float(span.count)
                    guard share >= Self.faceFill, front - wallAt >= Self.bigMinDepth, front - wallAt <= Self.bigMaxDepth,
                          ceiling - bottom <= Self.bigMaxDrop, spanShare >= Self.beamSpan else { return nil }
                    // Depth and drop inside each frame, then the median (see ceiling.py): one frame is rigid, while a
                    // scan whose tracking slipped puts the same beam in two places and pooled points mix the copies.
                    var depths: [Float] = [], drops: [Float] = [], front2 = front
                    let local = idx.filter { out[$0] < wallAt + Self.bigMaxDepth + 0.2 }
                    for m in Dictionary(grouping: local, by: { frameOf[$0] }).sorted(by: { $0.key < $1.key }).map({ $0.value }) {
                        let underF = m.filter { down[$0] && abs(height[$0] - bottom) < 0.12 }
                        guard underF.count >= Self.framePoints else { continue }
                        let bottomF = Self.median(underF.map { height[$0] })
                        let wallF = m.filter { facing[$0] && height[$0] > bottomF - 0.5 && height[$0] < bottomF - 0.05 }.map { out[$0] }
                        guard wallF.count >= Self.framePoints else { continue }
                        let back = Self.percentile(wallF, 0.1)
                        let wallOut = Self.median(wallF.filter { abs($0 - back) < 0.03 })
                        let faceF = m.filter { facing[$0] && height[$0] > bottomF + 0.03 && height[$0] < ceiling - 0.08 && out[$0] > wallOut + Self.bigMinDepth - 0.05 }
                        guard faceF.count >= Self.framePoints else { continue }
                        let faceOut = Self.median(faceF.map { out[$0] })
                        depths.append(faceOut - wallOut)
                        let besideF = m.filter { down[$0] && out[$0] > faceOut + 0.05 && abs(height[$0] - ceiling) < 0.2 }
                        if besideF.count >= Self.framePoints { drops.append(Self.median(besideF.map { height[$0] }) - bottomF) }
                    }
                    if depths.count >= Self.minFrames {
                        front2 = wallAt + Self.median(depths)
                        guard front2 - wallAt >= Self.bigMinDepth, front2 - wallAt <= Self.bigMaxDepth else { return nil }
                    }
                    if drops.count >= Self.minFrames { bottom = ceiling - Self.median(drops) }
                    return (bottom, wallAt, front2)
                }
                return nil
            }
            var found: [(bottom: Float, wall: Float, front: Float)?] = []
            for (k, idx) in byStretch.enumerated() {
                if shouldCancel() { return [] }
                let wallAt = surfaces[k].flatMap { abs($0 - typical) > 0.05 ? nil : $0 } ?? typical
                let s0 = starts[k]
                // The narrow-beam reading looks no further out than it always has.
                let narrow = idx.filter { out[$0] < Self.beamMaxDepth + 0.2 }
                let under = narrow.filter { down[$0] && height[$0] > 1.9 && height[$0] < ceiling - Self.beamMinDrop && out[$0] > wallAt - 0.03 }.map { height[$0] }
                guard under.count >= 15, let bottom = Self.peak(under, from: 1.9, to: ceiling, step: 0.01) else { found.append(bigBeam(idx, wallAt: wallAt, s0: s0)); continue }
                let topLimit = ceiling - 0.06
                let face = narrow.filter { facing[$0] && height[$0] > bottom + 0.02 && height[$0] < topLimit && out[$0] > wallAt + Self.beamMinDepth - 0.01 }
                guard let filled = filledShare(face, from: bottom, to: topLimit) else { found.append(bigBeam(idx, wallAt: wallAt, s0: s0)); continue }
                let front = Self.median(face.map { out[$0] })
                if filled < Self.faceFill || front - wallAt < Self.beamMinDepth || ceiling - bottom > Self.beamMaxDrop { found.append(bigBeam(idx, wallAt: wallAt, s0: s0)); continue }
                found.append((bottom, wallAt, front))
            }
            for best in Self.supportedRuns(found) {
            let hitIndices = (best.0..<best.1).filter { found[$0] != nil }
            let length = Float(best.1 - best.0) * Self.beamStretch
            guard hitIndices.count >= 4, length >= 1.0 else { continue }
            let hits = hitIndices.map { found[$0]! }
            let centres = hitIndices.map { -wall.width / 2 + (Float($0) + 0.5) * Self.beamStretch }
            let bottoms = hits.map { $0.bottom }, depths = hits.map { $0.front - $0.wall }
            let trend = Self.polyfit(centres, depths), medianBottom = Self.median(bottoms)
            // Heights come in 1 cm steps, so the bottom gets 4 cm: a tighter limit flips on a millimetre of rounding.
            // A big beam's face is far from the wall, so a few cm of scan drift weigh on it more: 4 cm there.
            let depthLimit: Float = Self.median(depths) >= Self.bigMinDepth ? 0.04 : 0.03
            let agree = hits.indices.map { abs(bottoms[$0] - medianBottom) <= 0.04 && abs(depths[$0] - (trend.slope * centres[$0] + trend.offset)) <= depthLimit }
            guard Float(agree.filter { $0 }.count) / Float(agree.count) >= 0.6 else { continue }
            let good = hits.indices.filter { agree[$0] }
            let bottom = Self.median(good.map { hits[$0].bottom }), wallAt = Self.median(good.map { hits[$0].wall }), front = Self.median(good.map { hits[$0].front })
            let frontLine = good.count >= 3 ? Self.polyfit(good.map { centres[$0] }, good.map { hits[$0].front }) : (slope: 0, offset: front)
            // No ceiling visible behind the face.
            let runStart = -wall.width / 2 + Float(best.0) * Self.beamStretch, runEnd = -wall.width / 2 + Float(best.1) * Self.beamStretch
            var seenCeiling = 0, seenBottom = 0
            let margin = max(0.01, 0.25 * (front - wallAt))  // a fixed 2 cm would leave nothing to look at behind a 4-6 cm beam
            for i in along.indices where down[i] && along[i] >= runStart && along[i] < runEnd && out[i] > wallAt + margin && out[i] < front - margin {
                if abs(height[i] - ceiling) < 0.04 { seenCeiling += 1 }
                if abs(height[i] - bottom) < 0.03 { seenBottom += 1 }
            }
            guard seenBottom >= 30, Float(seenCeiling) <= 0.5 * Float(seenBottom) else { continue }
            var u0 = runStart, u1 = min(wall.width / 2, runEnd)
            let inner = min(0, wallAt) - 0.01
            let corners = [(u0, inner), (u1, inner), (u1, frontLine.slope * u1 + frontLine.offset), (u0, frontLine.slope * u0 + frontLine.offset)].map { u, o -> SIMD2<Float> in
                let p = origin + axis * u + inward * o
                return SIMD2(p.x, p.z)
            }
            beams.append(Beam(polygon: corners, height: bottom))
            }
        }
        return beams
    }
}
