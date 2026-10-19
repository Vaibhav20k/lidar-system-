import tempfile
import unittest
import zipfile
from pathlib import Path
from unpack_bundle import unpack


class BundleTests(unittest.TestCase):
    def test_traversal_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);archive=root/'input.zip'
            with zipfile.ZipFile(archive,'w') as out:
                out.writestr('ok.txt','ok');out.writestr('../outside.txt','bad')
            with self.assertRaises(ValueError):unpack(archive,root/'bundle')
            self.assertFalse((root/'bundle').exists())

    def test_valid_bundle_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);archive=root/'input.zip'
            with zipfile.ZipFile(archive,'w') as out:out.writestr('scan/manifest.json','{}')
            unpack(archive,root/'bundle')
            self.assertEqual((root/'bundle/scan/manifest.json').read_text(),'{}')
            with self.assertRaises(FileExistsError):unpack(archive,root/'bundle')
