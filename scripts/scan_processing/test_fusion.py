import unittest
import numpy as np
from fusion import fuse


class FusionTests(unittest.TestCase):
    def test_repeated_pixels_do_not_dominate_independent_frames(self):
        p=np.array([[.001,.001,.001]]*100+[[.009,.001,.001]])
        f=fuse(p,np.tile([0,1,0],(101,1)),np.array([0]*100+[4]))
        self.assertAlmostEqual(f['points'][0,0],.005)
        self.assertEqual(f['frameCount'][0],2)
        self.assertEqual(set(f['support'][:,1]),{0,4})
        self.assertGreater(f['variance'][0,0],0)

    def test_opposite_surfaces_are_not_averaged(self):
        f=fuse([[0,0,0],[.001,0,0]],[[1,0,0],[-1,0,0]],[0,1])
        self.assertEqual(len(f['points']),2)

    def test_empty_and_invalid_inputs(self):
        self.assertEqual(len(fuse(np.empty((0,3)),np.empty((0,3)),[])['points']),0)
        with self.assertRaises(ValueError):fuse([[float('nan'),0,0]],[[1,0,0]],[0])
