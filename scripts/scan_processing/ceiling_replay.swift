import Foundation
import simd

@main
struct CeilingReplay {
    static func matrix(_ values: [NSNumber]) -> simd_float4x4 {
        let m = values.map { $0.floatValue }
        return simd_float4x4(columns: (SIMD4(m[0],m[1],m[2],m[3]), SIMD4(m[4],m[5],m[6],m[7]), SIMD4(m[8],m[9],m[10],m[11]), SIMD4(m[12],m[13],m[14],m[15])))
    }
    static func main() throws {
        let args = CommandLine.arguments
        let capture = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: args[1]))) as! [String: Any]
        let walls = (capture["walls"] as! [[String: Any]]).map { CeilingEstimator.WallSurface(id: $0["id"] as! String, transform: matrix($0["transform"] as! [NSNumber]), width: ($0["width"] as! NSNumber).floatValue, height: ($0["height"] as! NSNumber).floatValue) }
        let nativeFloor = (capture["floors"] as? [[String: Any]])?.max { ($0["corners"] as? [[NSNumber]])?.count ?? 0 < ($1["corners"] as? [[NSNumber]])?.count ?? 0 }
        let corners: [SIMD2<Float>] = (nativeFloor?["corners"] as? [[NSNumber]] ?? []).map { p in
            let world = matrix(nativeFloor!["transform"] as! [NSNumber]) * SIMD4(p[0].floatValue,p[1].floatValue,0,1)
            return SIMD2(world.x,world.z)
        }
        var estimator = CeilingEstimator(walls: walls, floor: CeilingEstimator.floorOutline(walls: walls, floorCorners: corners))
        estimator.openings = (capture["openings"] as? [[String: Any]] ?? []).map { CeilingEstimator.Opening(transform: matrix($0["transform"] as! [NSNumber]), width: ($0["width"] as! NSNumber).floatValue, height: ($0["height"] as! NSNumber).floatValue) }
        let raw = URL(fileURLWithPath: args[2])
        #if CURRENT
        let expired = CeilingWorkBudget(seconds: 0)
        assert(expired.shouldStop)
        let cancelled = CeilingWorkBudget(seconds: 60); cancelled.cancel(); assert(cancelled.shouldStop)
        estimator.shouldCancel = { cancelled.shouldStop }
        precondition(estimator.estimate(raw: raw) == nil, "Cancelled work must never publish a partial ceiling")
        var checks = 0
        estimator.shouldCancel = { checks += 1; return checks > 4 }
        precondition(estimator.estimate(raw: raw) == nil, "Cancellation during frame reading must stop")
        var starts: [Float] = []; var s: Float = -3.12345
        while s < 3.1 { starts.append(s); s += 0.25 }
        let values = (-10000...10000).map { Float($0)/1300 } + starts + starts.map { $0 + 0.25 }
        let before = starts.map { s in values.indices.filter { values[$0] >= s && values[$0] < s + 0.25 } }
        precondition(CeilingEstimator.stretchBuckets(values, starts: starts, width: 0.25) == before, "Binning must preserve boundary membership and point order")
        estimator.shouldCancel = { false }
        let begun = ProcessInfo.processInfo.systemUptime
        estimator.onProgress = { print(String(format: "%.3fs %@", ProcessInfo.processInfo.systemUptime - begun, $0)); fflush(stdout) }
        #endif
        let started = ProcessInfo.processInfo.systemUptime
        let ceiling = estimator.estimate(raw: raw)
        let result: [String: Any] = ["seconds": ProcessInfo.processInfo.systemUptime - started, "ceiling": ceiling ?? [:]]
        try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted,.sortedKeys]).write(to: URL(fileURLWithPath: args[3]))
        print("Finished replay", result["seconds"]!)
    }
}
