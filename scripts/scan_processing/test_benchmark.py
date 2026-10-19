import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from benchmark_structure import run, score_case
from metrics import dimension_score, match_instances, rigid_alignment

ROOT = Path(__file__).resolve().parents[2] / 'verification/benchmark'
CONFIG = {'toleranceMeters': .02, 'matchDistanceMeters': .25, 'sharedFrame': True}


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.reference = json.loads((ROOT / 'fixtures/reference.json').read_text())
        self.prediction = json.loads((ROOT / 'fixtures/exact.json').read_text())

    def test_exact_dimensions_pass_but_do_not_certify_shape(self):
        score = score_case(self.prediction, self.reference, CONFIG)
        self.assertTrue(score['basicChecksPassed'])
        self.assertFalse(score['releaseQualified'])
        self.assertEqual(score['shapeStatus'], 'not-scored')

    def test_30mm_error_fails(self):
        self.prediction['elements'][0]['dimensions']['width'] += .03
        score = score_case(self.prediction, self.reference, CONFIG)
        self.assertFalse(score['basicChecksPassed'])
        self.assertAlmostEqual(score['dimensionSummary']['worstMeters'], .03)

    def test_uncertainty_consumes_budget(self):
        self.assertTrue(dimension_score(1.015, 1, .005)['passed'])
        self.assertFalse(dimension_score(1.016, 1, .005)['passed'])
        self.assertFalse(dimension_score(None, 1)['passed'])
        with self.assertRaises(ValueError):
            dimension_score(float('nan'), 1)

    def test_missing_dimensions_and_instance_count_as_failures(self):
        self.prediction['elements'].pop()
        del self.prediction['elements'][0]['dimensions']['depth']
        score = score_case(self.prediction, self.reference, CONFIG)
        self.assertEqual(score['missing'], ['beam-A'])
        self.assertEqual(score['dimensionSummary']['missing'], 4)
        self.assertEqual(score['instanceRecall'], .5)

    def test_duplicate_prediction_is_extra_not_second_true_positive(self):
        duplicate = copy.deepcopy(self.prediction['elements'][0]); duplicate['id'] = 'duplicate'
        self.prediction['elements'].append(duplicate)
        score = score_case(self.prediction, self.reference, CONFIG)
        self.assertEqual(score['matched'], 2)
        self.assertEqual(len(score['extras']), 1)
        self.assertIn('column-A', score['associationAmbiguity']['references'])

    def test_matching_can_reassign_and_does_not_use_kind_or_id(self):
        preds = [{'id': 'x', 'center': [.1, 0, 0]}, {'id': 'y', 'center': [-.2, 0, 0]}]
        refs = [{'id': 'a', 'center': [0, 0, 0]}, {'id': 'b', 'center': [.3, 0, 0]}]
        pairs, _ = match_instances(preds, refs, .25)
        self.assertEqual({(i, j) for i, j, _ in pairs}, {(0, 1), (1, 0)})
        self.prediction['elements'][0]['kind'] = 'beam'
        score = score_case(self.prediction, self.reference, CONFIG)
        self.assertEqual(score['matched'], 2)
        self.assertEqual(score['classConfusions'], ['column-A'])

    def test_merged_instance_cannot_satisfy_two_references(self):
        reference = copy.deepcopy(self.reference)
        reference['elements'][1]['center'] = [1.1, 1.5, 1]
        self.prediction['elements'] = self.prediction['elements'][:1]
        score = score_case(self.prediction, reference, CONFIG)
        self.assertEqual(len(score['missing']), 1)
        self.assertTrue(score['associationAmbiguity']['predictions'])

    def test_rigid_alignment_corrects_rotation_translation_without_scale(self):
        points = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
        rotation = np.array([[0., -1, 0], [1, 0, 0], [0, 0, 1]])
        target = points @ rotation.T + [3, 4, 5]
        r, t, error = rigid_alignment(points, target)
        np.testing.assert_allclose(points @ r.T + t, target, atol=1e-12)
        self.assertLess(error, 1e-12)
        r, _, error = rigid_alignment(points, points*2)
        self.assertGreater(error, .1)
        self.assertAlmostEqual(np.linalg.det(r), 1)
        with self.assertRaises(ValueError):
            rigid_alignment([[0,0,0],[1,0,0],[2,0,0]], [[0,0,0],[1,0,0],[2,0,0]])

    def test_control_alignment_applies_only_to_positions(self):
        for element in self.prediction['elements']:
            element['center'][0] += 3
        config = {**CONFIG, 'controls': {'prediction': [[3,0,0],[4,0,0],[3,1,0]],
                                       'reference': [[0,0,0],[1,0,0],[0,1,0]], 'maxResidualMeters': .005}}
        self.assertTrue(score_case(self.prediction, self.reference, config)['basicChecksPassed'])

    def test_inputs_unchanged_and_invalid_data_rejected(self):
        original = copy.deepcopy(self.prediction)
        score_case(self.prediction, self.reference, CONFIG)
        self.assertEqual(original, self.prediction)
        self.prediction['elements'][1]['id'] = self.prediction['elements'][0]['id']
        with self.assertRaises(ValueError):
            score_case(self.prediction, self.reference, CONFIG)

    def test_smoke_outputs_hash_inputs_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = run(ROOT / 'synthetic.json', Path(temporary))
            self.assertEqual([c['basicChecksPassed'] for c in result['cases']], [True, False])
            self.assertEqual(len(result['manifestSha256']), 64)
            self.assertTrue((Path(temporary)/'structure-dimensions.csv').exists())
            self.assertTrue(all(not r['passed'] for r in result['repeatability']))
            with self.assertRaises(ValueError):
                run(ROOT / 'synthetic.json', Path(temporary))

    def test_room_partition_leak_is_rejected(self):
        manifest = json.loads((ROOT / 'synthetic.json').read_text())
        manifest['cases'][1]['partition'] = 'holdout'
        with tempfile.TemporaryDirectory() as temporary:
            original_read = __import__('benchmark_structure').read_json
            with patch('benchmark_structure.read_json', side_effect=lambda p: manifest if p == ROOT / 'synthetic.json' else original_read(p)):
                with self.assertRaisesRegex(ValueError, 'leaked'):
                    run(ROOT / 'synthetic.json', Path(temporary))

    def test_estimator_modules_do_not_import_reference_scorer(self):
        import ast
        folder = Path(__file__).parent
        for name in ('geometry.py', 'registration.py', 'ceiling.py', 'refit_walls.py', 'structure.py'):
            tree = ast.parse((folder/name).read_text(encoding='utf-8'))
            imported = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            imported += [alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names]
            self.assertFalse({'metrics', 'benchmark_structure', 'benchmark_geometry'} & set(imported), name)

    def test_legacy_replay_reads_targets_after_writing_estimation(self):
        import benchmark_geometry
        room = {'walls': [{'start': {'x': 0, 'z': 0}, 'end': {'x': 4, 'z': 0}}],
                'floor': [{'x':0,'z':0},{'x':4,'z':0},{'x':4,'z':3},{'x':0,'z':3}],
                'ceiling': {'height': 3.2}, 'measurementQuality': {'status': 'needs-rescan'}}
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); source = base/'source'; source.mkdir(); output = base/'output'
            (source/'scan-1.room.json').write_text(json.dumps(room))
            (source/'scan-1.json').write_text('{}')
            (source/'scan-1-raw').mkdir()
            manifest = base/'manifest.json'; reference = base/'reference.json'
            manifest.write_text(json.dumps({'version':1,'cases':[{'id':'scan-1','label':'test',
                                'group':'same-room','reference':'reference.json'}]}))
            reference.write_text(json.dumps({'length':4,'width':3,'height':3.2}))
            read_text = Path.read_text

            def checked_read(path, *args, **kwargs):
                if path == reference:
                    self.assertTrue((output/'scan-1.room.json').exists())
                return read_text(path, *args, **kwargs)

            with patch('sys.argv', ['benchmark_geometry.py', str(source), str(output), '--manifest', str(manifest)]), \
                    patch('benchmark_geometry.refine', return_value=(room, {})) as estimator, \
                    patch.object(Path, 'read_text', checked_read), patch('builtins.print'):
                benchmark_geometry.main()
            self.assertEqual(estimator.call_args.args, ({}, source/'scan-1-raw', room))
            result = json.loads((output/'before-after.json').read_text())
            self.assertTrue(result['scans'][0]['dimensions']['length']['within_2cm'])
            self.assertIsNone(result['repeat_spread_cm']['length'])
            self.assertEqual(len(result['scans'][0]['inputSha256']), 64)

    def test_empty_negative_scene_passes_basic_checks_only(self):
        empty = {'version':1,'units':'meters','elements':[]}
        score = score_case(empty, empty, CONFIG)
        self.assertTrue(score['basicChecksPassed'])
        self.assertIsNone(score['instanceRecall'])
        self.assertFalse(score['releaseQualified'])

    def test_missing_shared_frame_and_bad_controls_are_rejected(self):
        with self.assertRaises(ValueError):
            score_case(self.prediction, self.reference, {k:v for k,v in CONFIG.items() if k != 'sharedFrame'})
        config = {**CONFIG, 'controls': {'prediction': [[0,0,0],[1,0,0],[0,1,0]],
                                       'reference': [[0,0,0],[2,0,0],[0,2,0]], 'maxResidualMeters': .005}}
        with self.assertRaisesRegex(ValueError, 'residual'):
            score_case(self.prediction, self.reference, config)


if __name__ == '__main__':
    unittest.main()
