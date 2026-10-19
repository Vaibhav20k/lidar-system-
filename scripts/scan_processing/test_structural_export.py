import unittest
import numpy as np
from structural_candidates import element
from export_structural_benchmark import convert


class ExportTests(unittest.TestCase):
    def test_inferred_height_remains_explicit_and_rejection_is_excluded(self):
        e=element('column',[[1,1],[1.4,1],[1.4,1.6],[1,1.6]],0,3,[0,2],inferred=['top','bottom'])
        exported=convert({'structures':[e]})['elements'][0]
        np.testing.assert_allclose(exported['center'],[1.2,1.5,1.3])
        self.assertEqual(exported['inferredDimensions'],['height'])
        e['review']='rejected';self.assertEqual(convert({'structures':[e]})['elements'],[])
