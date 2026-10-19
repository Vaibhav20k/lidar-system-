"""Canonical ARKit RGB/depth geometry, in metres and right-handed Y-up.

sceneDepth is processed depth, not independent raw laser samples. Preserve frame
identity and original data; no sensor-bias correction is learned here.
"""
import json
import re
import numpy as np


def pose_matrix(values):
    pose=np.asarray(values,dtype=float).reshape(4,4).T
    if not np.isfinite(pose).all() or not np.allclose(pose[3],[0,0,0,1],atol=1e-5):
        raise ValueError('Invalid affine camera pose')
    rotation=pose[:3,:3]
    if not np.allclose(rotation.T@rotation,np.eye(3),atol=1e-3) or abs(np.linalg.det(rotation)-1)>.001:
        raise ValueError('Camera pose must be rigid; scaling/reflection is invalid')
    return pose


def unproject(depth, record):
    depth=np.asarray(depth,dtype=float)
    if depth.ndim!=2 or min(depth.shape)<2:
        raise ValueError('Depth must be a two-dimensional image')
    h,w=depth.shape
    k=np.asarray(record['intrinsics'],dtype=float).reshape(3,3).T
    width,height=record['imageWidth'],record['imageHeight']
    if width<=0 or height<=0 or not np.isfinite(k).all() or k[0,0]<=0 or k[1,1]<=0:
        raise ValueError('Invalid camera intrinsics')
    sx,sy=w/width,h/height
    u,v=np.meshgrid(np.arange(w)+.5,np.arange(h)+.5)
    camera=np.stack([(u-k[0,2]*sx)/(k[0,0]*sx)*depth,
                     -(v-k[1,2]*sy)/(k[1,1]*sy)*depth,-depth],axis=-1)
    return camera


def camera_normals(camera):
    normal=np.cross(np.gradient(camera,axis=1),np.gradient(camera,axis=0))
    normal/=np.maximum(np.linalg.norm(normal,axis=-1,keepdims=True),1e-9)
    normal[np.sum(normal*camera,axis=-1)>0]*=-1
    return normal


def project_camera(camera, record, depth_size):
    k=np.asarray(record['intrinsics'],float).reshape(3,3).T
    sx,sy=depth_size[0]/record['imageWidth'],depth_size[1]/record['imageHeight']
    depth=-camera[...,2]
    if np.any(depth<=0): raise ValueError('Points must be in front of the camera')
    return np.stack([camera[...,0]/depth*k[0,0]*sx+k[0,2]*sx,
                     -camera[...,1]/depth*k[1,1]*sy+k[1,2]*sy],axis=-1)


def frame_observations(raw, min_confidence=2, max_range=4.5):
    manifest=json.loads((raw/'manifest.json').read_text())
    w,h=manifest['depthSize']
    if not isinstance(w,int) or not isinstance(h,int) or min(w,h)<2 or w*h>4_000_000:
        raise ValueError('Invalid depth resolution')
    records=[json.loads(line) for line in (raw/'frames.jsonl').read_text().splitlines() if line.strip()]
    for index,record in enumerate(records):
        name=record['frame']
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',name):
            raise ValueError('Invalid frame identifier')
        path=raw/'depth'/f'{name}.bin'; confidence_path=raw/'confidence'/f'{name}.bin'
        if not record.get('depth') or not path.exists() or not confidence_path.exists():
            continue
        depth=np.fromfile(path,dtype='<f4').reshape(h,w)
        confidence=np.fromfile(confidence_path,dtype=np.uint8).reshape(h,w)
        pose=pose_matrix(record['cameraToWorld'])
        # Replace invalid values only for normal calculation; keep them excluded.
        camera=unproject(np.where(np.isfinite(depth),depth,0),record)
        normals=camera_normals(camera)
        valid=(confidence>=min_confidence)&np.isfinite(depth)&(depth>.3)&(depth<max_range)
        # Neighbours across large depth edges do not provide reliable normals.
        edge=np.maximum(abs(np.gradient(np.nan_to_num(depth),axis=0)),abs(np.gradient(np.nan_to_num(depth),axis=1)))
        valid &= (edge<.1)&(np.linalg.norm(normals,axis=-1)>.5)
        yield {'frameId':index,'record':record,'camera':pose[:3,3],
               'points':(camera@pose[:3,:3].T+pose[:3,3])[valid],
               'normals':(normals@pose[:3,:3].T)[valid]}
