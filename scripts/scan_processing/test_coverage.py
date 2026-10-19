import unittest
from coverage import CaptureSamplingPolicy, patch_coverage


class CoverageTests(unittest.TestCase):
    def test_redundancy_leaves_budget_for_new_view_and_revisit(self):
        policy=CaptureSamplingPolicy(primary=3,reserve=2)
        for t in range(3): self.assertTrue(policy.select([0,0,0],[0,0,-1],t))
        self.assertFalse(policy.select([0,0,0],[0,0,-1],3))
        self.assertTrue(policy.select([1,0,0],[0,0,-1],4))
        self.assertTrue(policy.select([0,0,0],[0,0,-1],30))
        self.assertFalse(policy.select([2,0,0],[0,0,-1],31))

    def test_same_pose_does_not_count_as_independent_evidence(self):
        result=patch_coverage([[0,0,0]]*3,[1,2,3],{1:[0,0,2],2:[0,0,2],3:[0,0,2]})
        self.assertEqual(result['patches'][0]['distinctViews'],1)
        self.assertFalse(result['complete'])

    def test_distinct_sides_add_support_but_do_not_certify_unseen_space(self):
        result=patch_coverage([[0,0,0]]*2,[1,2],{1:[0,0,2],2:[2,0,0]})
        self.assertEqual(result['patches'][0]['status'],'repeat-observed')
        self.assertEqual(result['unseenSpace'],'unknown')
