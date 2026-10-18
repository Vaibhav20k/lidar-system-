import json
import tempfile
import unittest
from pathlib import Path
import cv2
import numpy as np
from registration import solve_rigid_edge, register
from ceiling import balanced_cell_heights
from geometry import tracking_summary


class AccuracyRegressions(unittest.TestCase):
    def geometry(self):
        rng = np.random.default_rng(312)
        p = rng.uniform(-2, 2, (180, 3)); normals = np.repeat(np.eye(3), 60, axis=0)
        p[:60,0] = -2; p[60:120,1] = -2; p[120:,2] = -2
        R = cv2.Rodrigues(np.radians([.6, 1.3, -.5]))[0]; t = np.array([.04, -.03, .02])
        return p @ R.T + t, p, normals

    def test_rotation_and_translation_recovered_without_tape_targets(self):
        source, target, n = self.geometry()
        result = solve_rigid_edge(source, target, n, np.arange(len(n)))
        self.assertTrue(result['passed'], result)
        tr = np.array(result['transform'])
        np.testing.assert_allclose(source @ tr[:3,:3].T + tr[:3,3], target, atol=1e-6)
        self.assertGreater(result['heldoutBefore'], .02)
        self.assertLess(result['heldoutAfter'], 1e-6)

    def test_single_plane_does_not_invent_six_dof_correction(self):
        source, target, n = self.geometry()
        result = solve_rigid_edge(source[:60], target[:60], n[:60], np.arange(60))
        self.assertFalse(result['passed']); self.assertIn('underconstrained', result['reason'])

    def test_unseen_patches_reject_bad_correction(self):
        source, target, n = self.geometry()
        source[np.arange(len(n)) % 3 == 0] += n[np.arange(len(n)) % 3 == 0] * .06
        result = solve_rigid_edge(source, target, n, np.arange(len(n)))
        self.assertFalse(result['passed']); self.assertGreater(result['heldoutAfter'], .04)

    def test_missing_segments_leave_points_and_normals_untouched(self):
        p, _, n = self.geometry(); f = np.repeat([0,12,24],60)
        result, rotated, report = register(p, n, f, {}, -20.)
        self.assertFalse(report['passed'])
        np.testing.assert_array_equal(result,p); np.testing.assert_array_equal(rotated,n)

    def test_ceiling_dense_high_outlier_cannot_set_height(self):
        heights = np.r_[np.repeat(3.2, 12), np.repeat(3.35, 1000)]
        frames = np.r_[np.repeat(np.arange(6),2), np.repeat(6,1000)]
        result, seen, spread = balanced_cell_heights(np.zeros(len(heights),int), heights, frames, 1)
        self.assertTrue(seen[0]); self.assertAlmostEqual(result[0],3.2)
        # Stable samples remain unbiased even if one visit has far more points.
        old = np.mean(heights[heights > heights.max()-.08])
        self.assertGreater(old-result[0], .14)

    def test_one_view_cannot_fake_repeat_coverage(self):
        values, seen, _ = balanced_cell_heights(np.zeros(100,int), np.full(100,3.2), np.zeros(100,int), 1)
        self.assertFalse(seen[0])
        self.assertAlmostEqual(values[0], 3.2)  # don't misclassify a beam as ceiling by erasing sparse cells

    def test_startup_warning_is_not_capture_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            records=[{'timestamp':0.,'tracking':'limited:initializing'},
                     {'timestamp':1.,'tracking':'normal','sampleGapSeconds':.5},
                     {'timestamp':2.,'tracking':'normal','sampleGapSeconds':.5}]
            frames=[{'timestamp':1.,'worldMapping':'ARWorldMappingStatus(rawValue: 1)'},
                    {'timestamp':2.,'mappingState':'mapped'}]
            (p/'tracking.jsonl').write_text('\n'.join(map(json.dumps,records)))
            (p/'frames.jsonl').write_text('\n'.join(map(json.dumps,frames)))
            result=tracking_summary(p)
            self.assertEqual(result['startupLimitedSamples'],1)
            self.assertEqual(result['limitedOrGapSamples'],0)
            self.assertEqual(result['weakMappingFrames'],1)
            records[1]['tracking']='limited:excessiveMotion'
            (p/'tracking.jsonl').write_text('\n'.join(map(json.dumps,records)))
            self.assertEqual(tracking_summary(p)['limitedOrGapSamples'],1)

    def test_connected_graph_rotates_points_and_normals(self):
        from elevation import Wall
        rng=np.random.default_rng(32);p=[];n=[]
        for axis in range(3):
         q=rng.uniform(-1.9,1.9,(12000,3));q[:,axis]=-2;p.append(q);n.append(np.tile(np.eye(3)[axis],(len(q),1)))
        p=np.concatenate(p);n=np.concatenate(n);R=cv2.Rodrigues(np.radians([.2,.3,-.2]))[0];shift=np.array([.02,-.01,.025]);points=[];normals=[];ids=[]
        for f in [0,1,2,12,13,14]:
         points.append(p if f<12 else p@R.T+shift);normals.append(n if f<12 else n@R.T);ids.append(np.full(len(p),f))
        walls={}
        for axis,along in [(0,[0,0,-1]),(2,[1,0,0])]:
         m=np.eye(4);m[:3,0]=along;m[:3,2]=np.eye(3)[axis];m[axis,3]=-2
         walls[str(axis)]=Wall({'id':str(axis),'width':4.,'height':4.,'transform':m.T.flatten().tolist()})
        out,normal,report=register(np.concatenate(points),np.concatenate(normals),np.concatenate(ids),walls,-2.)
        self.assertTrue(report["passed"],report)
        np.testing.assert_allclose(out[:len(p)],out[-len(p):],atol=1e-4)
        np.testing.assert_allclose(normal[:len(p)],normal[-len(p):],atol=1e-4)

if __name__=='__main__': unittest.main()
