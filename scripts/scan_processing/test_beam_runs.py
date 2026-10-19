import unittest
from beam_runs import supported_runs


class BeamRunTests(unittest.TestCase):
    def test_keeps_separate_short_beams(self):
        a=(2.8,0,.2)
        self.assertEqual(supported_runs([a]*4+[None]*2+[a]*5),[(0,4),(6,11)])

    def test_splits_profile_changes(self):
        self.assertEqual(supported_runs([(2.8,0,.2)]*4+[(2.6,0,.4)]*4),[(0,4),(4,8)])

    def test_does_not_bridge_unknown_or_promote_sparse_hits(self):
        a=(2.8,0,.2)
        self.assertEqual(supported_runs([a]*3+[None]+[a]*4),[(4,8)])
        self.assertEqual(supported_runs([]),[])
