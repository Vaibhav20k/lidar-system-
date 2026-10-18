# Accuracy investigation · 17 October 2026

**2 cm absolute accuracy is not achieved: 0/5 scans pass all dimensions.**

Confirmed findings:

- The former 14–20 cm number is disagreement between observed surfaces, not a calibrated pose-drift measurement. 10 cm spatial patches still disagree by 13–18 cm, so coarse patch size does not explain it away.
- Saved frames: 137/360 had weak mapping (133 limited, 4 unavailable). The 3 limited-tracking warnings were before the first saved frame; none occurred inside the recorded interval.
- A matched long revisit supports a candidate 1.33° rotation / 4.48 cm translation difference. Held-out image error fell from 23.45 to 1.10 px. This cannot uniquely separate sensor depth error, reflections and true pose error.
- The old correction handled translation only. The new rigid solver handles rotation too, but this capture has only up to 8 stable shared patches per edge and no connected correction graph across 30 segments. It cannot safely repair these walls.
- Ceiling max-tail selection introduced upward bias. Equal-frame medians remove that selection bias. Floor height still comes from RoomPlan; the raw alternative differs by 7.74 cm but is too inconsistent to apply.
- Laser setup confirmed: tool rear against room-side glass, rear reference selected. No device-body offset was assumed.

Fixes implemented: rigid correction with held-out/closure checks, ceiling aggregation without maximum-based selection, sparse-ceiling/beam classification protection, separate tracking/mapping diagnostics, on-phone mapping/motion guidance and local feature logging. No original scan was overwritten.

| Scan | Length error before → after | Width error before → after | Height error before → after |
|---|---:|---:|---:|
| 17:32 | -11.40 → -11.40 cm | -0.74 → -0.74 cm | +5.80 → +5.30 cm |
| 15:39 | -6.73 → -6.73 cm | +6.07 → +6.07 cm | -4.20 → -4.80 cm |
| 16:48 | +5.28 → +5.28 cm | -14.44 → -14.44 cm | +5.20 → +4.50 cm |
| 16:50 | +6.51 → +6.51 cm | +1.65 → +1.65 cm | -4.30 → -4.60 cm |
| 15:28 | -5.00 → -5.00 cm | -2.81 → -2.81 cm | +0.30 → +0.00 cm |

Reference for same room: 6.423 × 4.570 × 3.226 m. The 15:28 room uses 4.939 × 4.295 × 3.230 m. If newest height reference were 3.246 m, its final height error would still be +3.3 cm. Some older height errors worsened slightly; no uniform improvement is claimed.

Offline Python sparse-ceiling replay also retains the main ceiling (3.271 m in both Python and native Swift).

Validation: 20 Python tests and 26 application tests passed; signed iOS build 4 compiled and signature verified. Build 4 was not installed because the phone disconnected. Raw scan backups remain intact. No AI API calls were made.

What remains: a controlled new capture with visible static targets on the room-side glass and opaque walls is needed to separate pose error from depth/surface error. The existing evidence cannot establish one exact physical root cause or guarantee 2 cm. Keep reference distances independent of the estimator; accept only when both absolute error and repeat spread are ≤2 cm.

[All replay results](before-after-v4.json) · [Raw diagnostics](raw-diagnostics.json) · [Visual revisit evidence](visual-correspondences.json) · [Preservation verification](preservation.json)
