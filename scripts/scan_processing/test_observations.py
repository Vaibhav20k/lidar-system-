import unittest
import json
import tempfile
from pathlib import Path
import numpy as np
from observations import unproject, project_camera, pose_matrix, camera_normals, frame_observations


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.record={'intrinsics':np.array([[400,0,320],[0,500,240],[0,0,1]]).T.ravel().tolist(),
                     'imageWidth':640,'imageHeight':480}

    def test_nonuniform_resize_and_pixel_centers_round_trip(self):
        camera=unproject(np.full((3,8),2.),self.record)
        pixels=project_camera(camera,self.record,(8,3))
        x,y=np.meshgrid(np.arange(8)+.5,np.arange(3)+.5)
        np.testing.assert_allclose(pixels,np.stack([x,y],axis=-1),atol=1e-12)
        np.testing.assert_allclose(camera_normals(camera)[...,2],1,atol=1e-12)

    def test_rigid_pose_and_rejection_of_scale_or_reflection(self):
        pose=np.eye(4);pose[:3,3]=[1,2,3]
        np.testing.assert_array_equal(pose_matrix(pose.T.ravel()),pose)
        pose[0,0]=2
        with self.assertRaises(ValueError):pose_matrix(pose.T.ravel())
        pose[0,0]=-1
        with self.assertRaises(ValueError):pose_matrix(pose.T.ravel())

    def test_invalid_intrinsics_fail(self):
        self.record['imageWidth']=0
        with self.assertRaises(ValueError):unproject(np.ones((2,2)),self.record)

    def test_missing_frames_preserve_identity_and_low_confidence_is_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            raw=Path(directory)
            (raw/'depth').mkdir(); (raw/'confidence').mkdir()
            (raw/'manifest.json').write_text(json.dumps({'depthSize':[8,3]}))
            records=[{**self.record,'frame':str(i),'depth':True,
                      'cameraToWorld':np.eye(4).T.ravel().tolist()} for i in range(3)]
            (raw/'frames.jsonl').write_text('\n'.join(map(json.dumps,records)))
            for i in [0,2]:
                np.full((3,8),2,dtype='<f4').tofile(raw/'depth'/f'{i}.bin')
                np.full((3,8),i,dtype=np.uint8).tofile(raw/'confidence'/f'{i}.bin')
            observations=list(frame_observations(raw))
            self.assertEqual([v['frameId'] for v in observations],[0,2])
            self.assertEqual(len(observations[0]['points']),0)
            self.assertEqual(len(observations[1]['points']),24)
