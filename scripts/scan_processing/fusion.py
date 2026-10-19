"""Frame-balanced oriented surfels, preserving support and disagreement.

Display/segmentation representation only: final dimensions must be fitted to
source observations. No hole filling, hidden-face synthesis or TSDF smoothing.
"""
import numpy as np


def means(values, groups, count):
    sums=np.zeros((count,values.shape[1]))
    np.add.at(sums,groups,values)
    return sums/np.bincount(groups,minlength=count)[:,None]


def fuse(points,normals,frames,voxel=.015):
    points,normals,frames=np.asarray(points,float),np.asarray(normals,float),np.asarray(frames)
    if voxel<=0 or not np.isfinite(voxel): raise ValueError('Invalid voxel size')
    if points.shape!=normals.shape or points.ndim!=2 or points.shape[1]!=3 or frames.shape!=(len(points),):
        raise ValueError('Observation shapes disagree')
    if not np.isfinite(points).all() or not np.isfinite(normals).all(): raise ValueError('Nonfinite observations')
    lengths=np.linalg.norm(normals,axis=1)
    if np.any(lengths<.5): raise ValueError('Invalid normals')
    normals=normals/lengths[:,None]
    keys=np.column_stack([np.floor(points/voxel).astype(np.int64),np.round(normals*4).astype(int)])
    # Equalize frame contributions before combining repeated views.
    frame_keys,per_frame=np.unique(np.column_stack([keys,frames]),axis=0,return_inverse=True)
    fp=means(points,per_frame,len(frame_keys));fn=means(normals,per_frame,len(frame_keys))
    cells,groups=np.unique(frame_keys[:,:-1],axis=0,return_inverse=True)
    position=means(fp,groups,len(cells));normal=means(fn,groups,len(cells))
    normal/=np.maximum(np.linalg.norm(normal,axis=1,keepdims=True),1e-9)
    variance=means((fp-position[groups])**2,groups,len(cells))
    return {'points':position,'normals':normal,'frameCount':np.bincount(groups,minlength=len(cells)),
            'variance':variance,'support':np.column_stack([groups,frame_keys[:,-1]]).astype(int),
            'observationToSurfel':groups[per_frame], 'voxel':voxel}
