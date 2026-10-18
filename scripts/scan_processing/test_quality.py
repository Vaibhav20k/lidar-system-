"""Run locally: python -m unittest discover -s scripts/scan_processing -p 'test_*.py'."""
import unittest
import numpy as np
from elevation import Wall
from quality import repeat_view_check, robust_plane
from refit_walls import fit_wall
from registration import solve_edge


def wall_samples(shift=0., frames=20):
    rng = np.random.default_rng(42)
    points = np.concatenate([np.column_stack([rng.uniform(-1.99, 1.99, 3000),
                            rng.uniform(.31, 1.89, 3000), rng.normal(0, .002, 3000) + (shift if f >= frames//2 else 0)])
                             for f in range(frames)])
    ids = np.repeat(np.arange(frames), 3000)
    return points, ids


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.wall = Wall({'id': 'wall', 'width': 4., 'height': 3.,
                          'transform': [1,0,0,0,0,1,0,0,0,0,1,0,0,1.5,0,1]})

    def fit(self, shift=0., frames=20):
        p, ids = wall_samples(shift, frames)
        return fit_wall(self.wall, p, np.tile([0.,0.,1.], (len(p), 1)), ids, 0., [])

    def test_ten_cm_second_layer_is_rejected(self):
        fit, reason = self.fit(.1)
        self.assertIsNone(fit)
        self.assertIn('repeat views disagree', reason)
        self.assertIn('10.0 cm', reason)

    def test_stable_wall_is_accepted(self):
        fit, reason = self.fit()
        self.assertIsNone(reason)
        self.assertLess(abs(fit[2]['shift']), .003)

    def test_unobserved_repeat_fails_closed(self):
        fit, reason = self.fit(frames=2)
        self.assertIsNone(fit)
        self.assertIn('insufficient repeat-view', reason)

    def test_three_cm_shift_is_not_within_budget(self):
        self.assertIsNone(self.fit(.03)[0])

    def test_whole_frame_holdout_detects_bad_frames(self):
        p, ids = wall_samples()
        p[np.isin(ids, np.arange(20)[::3]), 2] += .06
        result = robust_plane(p, ids)
        self.assertFalse(result['passed'])
        self.assertGreater(result['heldoutP90'], .05)

    def test_dense_early_views_do_not_outvote_revisit(self):
        p, ids = wall_samples(.1)
        early = ids < 10
        p = np.concatenate([p, np.repeat(p[early], 5, axis=0)])
        ids = np.concatenate([ids, np.repeat(ids[early], 5)])
        result = repeat_view_check(p[:,0] + 2, p[:,2], p[:,1], ids, 4.)
        self.assertFalse(result['passed'])
        self.assertGreater(result['shift'], .09)

    def test_plane_needs_spatial_rank(self):
        p, ids = wall_samples()
        p[:, 1] = 1.
        self.assertFalse(robust_plane(p, ids)['passed'])

    def test_registration_recovers_known_translation(self):
        normals = np.repeat(np.eye(3), 30, axis=0)
        translation = np.array([.04, -.03, .06])
        delta = normals @ translation
        result = solve_edge(normals, delta, np.arange(90))
        self.assertTrue(result['passed'])
        np.testing.assert_allclose(result['translation'], translation, atol=1e-6)
        self.assertLess(result['heldoutAfter'], 1e-6)

    def test_single_wall_cannot_constrain_registration(self):
        normals = np.tile([0.,0.,1.], (90,1))
        self.assertFalse(solve_edge(normals, np.full(90,.1), np.arange(90))['passed'])

    def test_registration_rejects_disagreeing_holdout(self):
        normals = np.repeat(np.eye(3), 30, axis=0)
        delta = normals @ np.array([.04, -.03, .06])
        delta[np.arange(90)%3 == 0] += .05
        self.assertFalse(solve_edge(normals, delta, np.arange(90))['passed'])


if __name__ == '__main__':
    unittest.main()
