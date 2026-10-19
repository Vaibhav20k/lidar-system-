import unittest
import numpy as np
from pose_graph import optimize


def move(x):
    tr=np.eye(4);tr[0,3]=x
    return tr


class PoseGraphTests(unittest.TestCase):
    def test_loop_is_globally_balanced_with_fixed_anchor(self):
        initial={0:move(0),1:move(.02),2:move(.04)}
        edges=[{'from':0,'to':1,'transform':move(.02)},
               {'from':1,'to':2,'transform':move(.02)},
               {'from':0,'to':2,'transform':move(.043)}]
        poses,report=optimize(initial,edges)
        self.assertLess(report['finalResidual'],report['initialResidual'])
        np.testing.assert_array_equal(poses[0],initial[0])
        self.assertAlmostEqual(poses[1][0,3],.021,places=5)
        for pose in poses.values():
            self.assertAlmostEqual(np.linalg.det(pose[:3,:3]),1)

    def test_disconnected_graph_is_rejected(self):
        with self.assertRaises(ValueError): optimize({0:move(0),1:move(0)},[])
