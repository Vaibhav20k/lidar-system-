"""Anchored rigid pose graph. No scale variable or dimension references.

This small dense solver is intended for at most 64 capture segments. Constraints
must already have independent patch validation; callers retain closure and
held-out surface gates before applying any correction to original observations.
"""
import cv2
import numpy as np


def update(pose, step):
    delta=np.eye(4)
    delta[:3,:3]=cv2.Rodrigues(step[3:])[0]
    delta[:3,3]=step[:3]
    return delta@pose


def residual(poses, edges):
    values=[]
    for e in edges:
        error=np.linalg.inv(poses[e['from']]@np.asarray(e['transform']))@poses[e['to']]
        values.extend(error[:3,3])
        # One metre angular lever arm; acceptance uses actual room corners.
        rotation=error[:3,:3]
        if np.trace(rotation)>2.99999:
            values.extend(np.array([rotation[2,1]-rotation[1,2],rotation[0,2]-rotation[2,0],rotation[1,0]-rotation[0,1]])/2)
        else:
            values.extend(cv2.Rodrigues(rotation)[0].ravel())
    return np.asarray(values)


def optimize(initial, edges, iterations=8):
    poses={k:np.array(v,copy=True) for k,v in initial.items()}
    keys=sorted(poses)
    if not keys or len(keys)>64:
        raise ValueError('Pose graph requires 1..64 segments')
    if any(e['from'] not in poses or e['to'] not in poses for e in edges):
        raise ValueError('Unknown graph node')
    connected={keys[0]}
    for _ in keys:
        for e in edges:
            if e['from'] in connected or e['to'] in connected:
                connected.update([e['from'],e['to']])
    if len(connected)!=len(keys):
        raise ValueError('Disconnected graph')
    free=keys[1:]
    before=float(np.linalg.norm(residual(poses,edges)))
    for _ in range(iterations):
        r=residual(poses,edges)
        if not free or np.linalg.norm(r)<1e-10: break
        jac=np.zeros((len(r),6*len(free)))
        for i,k in enumerate(free):
            for axis in range(6):
                step=np.zeros(6);step[axis]=1e-5
                candidate={**poses,k:update(poses[k],step)}
                jac[:,6*i+axis]=(residual(candidate,edges)-r)/1e-5
        weights=np.sqrt(np.minimum(1.,.01/np.maximum(abs(r),1e-12)))
        delta,_,rank,_=np.linalg.lstsq(jac*weights[:,None],-r*weights,rcond=None)
        if rank<6*len(free): raise ValueError('Unobservable graph')
        accepted=False
        for fraction in [1.,.5,.25,.125]:
            candidate={**poses,**{k:update(poses[k],fraction*delta[6*i:6*i+6]) for i,k in enumerate(free)}}
            if np.linalg.norm(residual(candidate,edges))<=np.linalg.norm(r):
                poses=candidate;accepted=True;break
        if not accepted or np.linalg.norm(delta)<1e-7: break
    return poses,{'initialResidual':before,'finalResidual':float(np.linalg.norm(residual(poses,edges))),
                  'anchor':keys[0],'scaleOptimized':False}
