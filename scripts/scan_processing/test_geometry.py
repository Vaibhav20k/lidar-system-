import copy
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from geometry import refine


def scene(bad_ceiling=False):
    capture={'walls':[]}
    room={'name':'Synthetic','walls':[],'floor':[{'x':0.,'z':0.},{'x':4.,'z':0.},{'x':4.,'z':3.},{'x':0.,'z':3.}],
          'openings':[],'objects':[],'ceiling':{'height':3.2,'regions':[]},'warnings':[]}
    for i, start in enumerate(room['floor']):
        end=room['floor'][(i+1)%4]
        a=np.array([start['x'],0.,start['z']]); b=np.array([end['x'],0.,end['z']])
        axis=(b-a)/np.linalg.norm(b-a); normal=np.cross(axis,[0.,1.,0.])
        matrix=np.eye(4);matrix[:3,0]=axis;matrix[:3,1]=[0,1,0];matrix[:3,2]=normal;matrix[:3,3]=(a+b)/2+[0,1.6,0]
        capture['walls'].append({'id':str(i),'width':float(np.linalg.norm(b-a)),'height':3.2,'transform':matrix.T.flatten().tolist()})
        room['walls'].append({'id':str(i),'start':start.copy(),'end':end.copy(),'height':3.2,'baseY':0.})
    rng=np.random.default_rng(7);points=[];normals=[];frames=[]
    for f in range(20):
        for level,normal in [(.04,[0,1,0]),(3.2+(.08 if bad_ceiling and f>=10 else 0),[0,-1,0])]:
            points.append(np.column_stack([rng.uniform(.05,3.95,2000),rng.normal(level,.001,2000),rng.uniform(.05,2.95,2000)]))
            normals.append(np.tile(normal,(2000,1)));frames.append(np.full(2000,f))
    return capture,room,(np.concatenate(points),np.concatenate(normals),np.concatenate(frames))


class GeometryTests(unittest.TestCase):
    def test_supported_floor_ceiling_correct_height_without_targets(self):
        capture,room,cloud=scene();original=copy.deepcopy(room)
        with patch('geometry.depth_points',return_value=cloud):
            result,report=refine(capture,Path('/does-not-exist'),room)
        self.assertIn('floor-ceiling',report['applied'])
        self.assertAlmostEqual(result['ceiling']['height'],3.16,places=3)
        self.assertEqual(room,original)
        # Applying again must not silently double-shift heights.
        with self.assertRaisesRegex(ValueError,'original height datum'):
            refine(capture,Path('/does-not-exist'),result)

    def test_bad_ceiling_with_good_floor_leaves_both_unchanged(self):
        capture,room,cloud=scene(True)
        with patch('geometry.depth_points',return_value=cloud):
            result,report=refine(capture,Path('/does-not-exist'),room)
        self.assertTrue(report['floor']['passed'])
        self.assertFalse(report['ceiling']['passed'])
        self.assertNotIn('floor-ceiling',report['applied'])
        self.assertEqual(result['ceiling']['height'],room['ceiling']['height'])
        self.assertEqual(result['walls'],room['walls'])


if __name__=='__main__': unittest.main()
